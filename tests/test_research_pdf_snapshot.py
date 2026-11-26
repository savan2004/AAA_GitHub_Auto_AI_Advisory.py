"""Isolated Neon research PDF cache integrity; no secrets or Telegram."""
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import research_pdf_snapshot as cache


class PdfCacheSafetyTests(unittest.TestCase):
    def setUp(self):
        # These are NOT real report data. Source validation is mocked only for
        # unit tests; production CI independently builds a genuine SBIN PDF.
        self.raw = b"%PDF-" + bytes([65]) * 30_000
        self.sha = hashlib.sha256(self.raw).hexdigest()

    def _fake_database(self, row):
        fake = types.ModuleType("psycopg")
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row
        context = MagicMock()
        context.__enter__.return_value = conn
        fake.connect = MagicMock(return_value=context)
        return fake, conn

    def test_load_requires_matching_checksum_and_current_session(self):
        fake, conn = self._fake_database((self.raw, self.sha, 8, len(self.raw)))
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(cache, "_dsn", return_value="postgresql://dummy"), \
             patch.object(cache, "_current_session", return_value="2026-11-13"), \
             patch.object(cache, "_verified",
                          return_value={"pages":8,"bytes":len(self.raw)}), \
             patch.dict(sys.modules, {"psycopg":fake}):
            loaded = cache.load_verified_report("SBIN", output_dir=folder)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[1], "2026-11-13")
            self.assertEqual(Path(loaded[0]).read_bytes(), self.raw)
            self.assertIn("WHERE symbol=%s AND session_date=%s",
                          conn.execute.call_args_list[-1].args[0])
            self.assertEqual(conn.execute.call_args_list[-1].args[1],
                             ("SBIN", "2026-11-13"))
            self.assertNotIn("options", fake.connect.call_args.kwargs)
            Path(loaded[0]).unlink(missing_ok=True)
            conn.execute.return_value.fetchone.return_value = (
                self.raw, "0"*64, 8, len(self.raw))
            self.assertIsNone(cache.load_verified_report("SBIN", output_dir=folder))

    def test_no_stale_or_missing_pdf_is_returned(self):
        fake, conn = self._fake_database(None)
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(cache, "_dsn", return_value="postgresql://dummy"), \
             patch.object(cache, "_current_session", return_value="2026-11-13"), \
             patch.dict(sys.modules, {"psycopg":fake}):
            self.assertIsNone(cache.load_verified_report("SBIN", output_dir=folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_save_writes_verified_public_report_only(self):
        fake, conn = self._fake_database(None)
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "SBIN.pdf"
            report.write_bytes(self.raw)
            with patch.object(cache, "_dsn", return_value="postgresql://dummy"), \
                 patch.object(cache, "_verified",
                              return_value={"pages":8,"bytes":len(self.raw)}), \
                 patch.dict(sys.modules, {"psycopg":fake}):
                digest = cache.save_verified_report(
                    "SBIN", report, session="2026-11-13")
            self.assertEqual(digest, self.sha)
            self.assertTrue(any(
                "INSERT INTO public.msk_research_pdf_snapshot_v1"
                in call.args[0] for call in conn.execute.call_args_list))
            all_sql = " ".join(call.args[0] for call in conn.execute.call_args_list)
            self.assertNotIn("portfolio", all_sql.lower())
            self.assertNotIn("telegram", all_sql.lower())

    def test_local_cache_same_bytes_no_sliding_expiry_and_new_session_rejected(self):
        from collections import OrderedDict
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(cache, "_LOCAL", OrderedDict()), \
             patch.object(cache, "_LOCAL_DIR", None), \
             patch.object(cache, "_current_session", return_value="2026-11-13") as session, \
             patch.object(cache.time, "monotonic", return_value=100.0) as clock:
            report = Path(folder) / "original.pdf"
            report.write_bytes(self.raw)
            quality = {"pages": 8, "bytes": len(self.raw)}
            try:
                cache.remember_local_report("SBIN", report, "13-Nov-2026", quality)
                first = cache.load_recent_local_report("SBIN", output_dir=folder)
                self.assertEqual(Path(first[0]).read_bytes(), self.raw)
                Path(first[0]).unlink()
                clock.return_value = 100.0 + cache.LOCAL_TTL_SECONDS - 1
                second = cache.load_recent_local_report("SBIN", output_dir=folder)
                self.assertEqual(Path(second[0]).read_bytes(), self.raw)
                Path(second[0]).unlink()
                clock.return_value += 1
                self.assertIsNone(cache.load_recent_local_report("SBIN", output_dir=folder))
                cache.remember_local_report("SBIN", report, "13-Nov-2026", quality)
                session.return_value = "2026-11-16"
                self.assertIsNone(cache.load_recent_local_report("SBIN", output_dir=folder))
            finally:
                if cache._LOCAL_DIR is not None:
                    cache._LOCAL_DIR.cleanup()

    def test_separate_quote_is_timestamped_and_missing_quote_is_safe(self):
        quote = {"price": 123.45, "quote_source": "NSE India",
                 "quote_asof_ist": "2026-11-13T14:10:00+05:30",
                 "quote_status": "recent_observation"}
        text = cache.report_quote_line(quote)
        self.assertIn("123.45", text)
        self.assertIn("14:10 IST", text)
        self.assertIn("NSE India", text)
        self.assertIn("unavailable", cache.report_quote_line(None))
        self.assertIn("unavailable", cache.report_quote_line(dict(quote, price=float("nan"))))


if __name__ == "__main__":
    unittest.main()
