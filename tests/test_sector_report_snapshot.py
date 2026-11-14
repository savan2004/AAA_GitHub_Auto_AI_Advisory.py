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


if __name__ == "__main__":
    unittest.main()
