"""Sector-report Neon cache must reject stale, partial or altered real reports."""
import hashlib
import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

import sector_report_snapshot as snap


class FakeConnection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        return types.SimpleNamespace(fetchone=lambda: self.row)


class SectorSnapshotSafety(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 13)
        self.session = patch.object(snap, "_expected_session", return_value=self.day)
        self.session.start()
        self.addCleanup(self.session.stop)

    def _report(self):
        return {"asof": "13-Nov-2026", "index_coverage": (12, 15),
                "n_unique": 190, "n_configured": 250}

    def test_complete_session_and_coverage(self):
        self.assertEqual(snap._check_metadata(self._report()), self.day)

    def test_stale_date_is_rejected(self):
        report = self._report()
        report["asof"] = "12-Nov-2026"
        with self.assertRaisesRegex(ValueError, "stale"):
            snap._check_metadata(report)

    def test_undercovered_stock_sample_is_rejected(self):
        report = self._report()
        report["n_unique"] = 60
        with self.assertRaisesRegex(ValueError, "insufficient"):
            snap._check_metadata(report)

    def test_undercovered_indices_are_rejected(self):
        report = self._report()
        report["index_coverage"] = (4, 15)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            snap._check_metadata(report)

    def test_corrupt_neon_pdf_is_not_sent(self):
        data = b"%PDF-" + b"x" * 16000
        fake = types.SimpleNamespace(connect=lambda *_a, **_k:
               FakeConnection((data, "0" * 64, 5, len(data), ["Sample"])))
        with patch.object(snap, "_dsn", return_value="postgresql://fake?sslmode=require"), \
             patch.dict(sys.modules, {"psycopg": fake}):
            self.assertIsNone(snap.load_verified_sector_report())

    def test_snapshot_miss_does_not_create_fake_report(self):
        fake = types.SimpleNamespace(connect=lambda *_a, **_k:
               FakeConnection(None))
        with patch.object(snap, "_dsn", return_value="postgresql://fake?sslmode=require"), \
             patch.dict(sys.modules, {"psycopg": fake}):
            self.assertIsNone(snap.load_verified_sector_report())



class DirectSectorGenerationSafety(unittest.TestCase):
    """Exercise the actual public entrypoint with isolated market/transport fixtures."""

    def setUp(self):
        import ast
        import logging
        import os
        import tempfile
        import threading
        import time
        from datetime import datetime, timezone
        from pathlib import Path
        from typing import List, Tuple
        from unittest.mock import Mock

        self.day = date(2026, 11, 13)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        source = Path(__file__).resolve().parents[1].joinpath("sector_report.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        entrypoint = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef)
                          and node.name == "generate_sector_report_pdf")
        self.report = {"asof": "13-Nov-2026", "index_coverage": (12, 15),
                       "n_unique": 190, "n_configured": 250}
        self.build = Mock(side_effect=lambda report, path:
                          Path(path).write_bytes(b"%PDF-UNIT-FIXTURE" + b"x" * 1200))
        self.summary = Mock(return_value=["Isolated fixture, not live financial evidence"])
        self.cache = {"path": None, "summary": None, "session": None}
        self.ns = {
            "Tuple": Tuple, "List": List, "os": os, "time": time,
            "datetime": datetime, "IST": timezone.utc,
            "OUTPUT_DIR": self.tmp.name, "_gen_lock": threading.Lock(),
            "_cache": self.cache, "_last_completed_session": lambda: self.day,
            "logger": logging.getLogger(__name__),
            "_prune_old_files": lambda: None, "collect": Mock(return_value={}),
            "assemble": Mock(return_value=self.report),
            "build_pdf": self.build, "summary_lines": self.summary,
        }
        exec(compile(ast.Module(body=[entrypoint], type_ignores=[]),
                     str(source), "exec"), self.ns)
        for target, value in [("_expected_session", self.day),
                              ("load_verified_sector_report", None)]:
            mock = patch.object(snap, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def assert_withheld(self, expected_error):
        from pathlib import Path
        ok, error, summary = self.ns["generate_sector_report_pdf"]()
        self.assertFalse(ok)
        self.assertIn(expected_error, error)
        self.assertEqual(summary, [])
        self.build.assert_not_called()
        self.summary.assert_not_called()
        self.assertIsNone(self.cache["path"])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_stale_direct_report_is_not_built_or_cached(self):
        self.report["asof"] = "12-Nov-2026"
        self.assert_withheld("stale")

    def test_future_direct_report_is_not_built_or_cached(self):
        self.report["asof"] = "16-Nov-2026"
        self.assert_withheld("stale")

    def test_direct_index_undercoverage_is_not_built_or_cached(self):
        self.report["index_coverage"] = (4, 15)
        self.assert_withheld("insufficient")

    def test_direct_stock_undercoverage_is_not_built_or_cached(self):
        self.report["n_unique"] = 60
        self.assert_withheld("insufficient")

    def test_direct_stock_fraction_undercoverage_is_not_built_or_cached(self):
        self.report["n_unique"] = 100
        self.assert_withheld("insufficient")

    def test_valid_direct_report_builds_once_and_reuses_same_session(self):
        ok, path, summary = self.ns["generate_sector_report_pdf"]()
        self.assertTrue(ok)
        self.assertEqual(self.cache["session"], self.day)
        self.assertEqual(self.ns["generate_sector_report_pdf"](),
                         (True, path, summary))
        self.build.assert_called_once()
        self.ns["collect"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
