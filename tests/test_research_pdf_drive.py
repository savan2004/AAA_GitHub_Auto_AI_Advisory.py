"""Offline FIFO and freshness contract; no real Drive operations."""
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import research_pdf_drive as a

NOW = datetime(2026, 1, 24, 9, tzinfo=timezone.utc)
RAW = b"%PDF-" + b"x" * 30000

def item(i, stamp=None, symbol="OLD", session="2026-01-01"):
    return {"id": str(i), "parents": [a.FOLDER], "mimeType": "application/pdf",
            "trashed": False, "size": str(len(RAW)), "md5Checksum": hashlib.md5(RAW).hexdigest(),
            "appProperties": {"archive": a.TAG, "symbol": symbol, "session": session,
                "generatedAt": (stamp or NOW - timedelta(days=23) + timedelta(seconds=i)).isoformat(),
                "sha256": hashlib.sha256(RAW).hexdigest()}}

class FakeDrive:
    def __init__(self, rows, fail=False, corrupt=False):
        self.rows, self.fail, self.corrupt = list(rows), fail, corrupt
        self.removed, self.uploads = [], 0
    def list(self):
        return list(reversed(self.rows))
    def upload(self, metadata, raw):
        self.uploads += 1
        if self.fail:
            raise TimeoutError()
        entry = item(501)
        entry["appProperties"] = metadata["appProperties"]
        self.rows.append(entry)
        return entry
    def get(self, id):
        return next(x for x in self.rows if x["id"] == id)
    def download(self, entry):
        if self.corrupt:
            raise ValueError("integrity")
        a._verify_bytes(entry, RAW)
        return RAW
    def delete(self, entry):
        self.removed.append(entry["id"])
        self.rows.remove(entry)
    def close(self):
        pass

class ArchiveTests(unittest.TestCase):
    def store(self, d):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "SBIN.pdf"
            pdf.write_bytes(RAW)
            with patch.object(a, "enabled", return_value=True), \
                 patch.object(a, "Drive", return_value=d), \
                 patch.object(a, "_session", return_value="2026-01-23"), \
                 patch.object(a, "_now", return_value=NOW), \
                 patch("pdf_release_gate.validate_research_pdf", return_value={"pages":8,"bytes":len(RAW)}):
                return a.store_cloud_report("SBIN", pdf, "23-Jan-2026")
    def test_501_evicts_oldest(self):
        d = FakeDrive([item(i) for i in range(1,501)])
        self.assertTrue(self.store(d))
        self.assertEqual(d.removed, ["1"])
        self.assertEqual(len(d.rows),500)
    def test_upload_failure_preserves_all(self):
        d = FakeDrive([item(i) for i in range(1,501)], fail=True)
        self.assertFalse(self.store(d))
        self.assertEqual(d.removed, [])
        self.assertEqual(len(d.rows),500)
    def test_readback_failure_blocks_deletion_and_further_upload(self):
        d = FakeDrive([item(i) for i in range(1,501)], corrupt=True)
        self.assertFalse(self.store(d))
        self.assertEqual(d.removed, [])
        self.assertFalse(self.store(d))
        self.assertEqual(d.uploads,1)
    def test_reuse_no_upload(self):
        d = FakeDrive([item(1,NOW-timedelta(hours=1),"SBIN","2026-01-23")])
        self.assertTrue(self.store(d))
        self.assertEqual(d.uploads,0)
    def test_expiration_and_folder_isolation(self):
        i = item(1,NOW-timedelta(hours=23),"SBIN","2026-01-23")
        self.assertTrue(a._fresh(i,"SBIN","2026-01-23",NOW))
        self.assertFalse(a._fresh(i,"SBIN","2026-01-23",NOW+timedelta(hours=1)))
        self.assertFalse(a._fresh(i,"SBIN","2026-01-24",NOW))
        i["parents"]=["sector-folder"]
        self.assertFalse(a._managed(i))
    def test_activation_diagnostics_do_not_expose_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(a._archive_config_status(), "DISABLED")
            os.environ["MSK_RESEARCH_DRIVE_ENABLED"] = "1"
            self.assertEqual(a._archive_config_status(), "FOLDER_NOT_CONFIGURED")
            os.environ["MSK_RESEARCH_DRIVE_FOLDER_ID"] = a.FOLDER
            self.assertEqual(a._archive_config_status(), "OAUTH_CONFIGURATION_MISSING")
            with self.assertLogs(a.LOG, level="WARNING") as logs:
                with self.assertRaises(ValueError):
                    a.Drive()
            self.assertIn("OAUTH_CONFIGURATION_MISSING", " ".join(logs.output))
            for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
                os.environ["MSK_DRIVE_" + key] = "private-test-value"
            self.assertEqual(a._archive_config_status(), "CONFIGURED")

    def test_disabled_archive_logs_reason(self):
        with patch.dict(os.environ, {"MSK_RESEARCH_DRIVE_ENABLED":"0"}):
            with self.assertLogs(a.LOG, level="WARNING") as logs:
                self.assertFalse(a.store_cloud_report("SBIN", "missing", "23-Jan-2026"))
            self.assertIn("reason=DISABLED", " ".join(logs.output))

    def test_upload_failure_logs_exact_stage(self):
        with self.assertLogs(a.LOG, level="WARNING") as logs:
            self.assertFalse(self.store(FakeDrive([], fail=True)))
        self.assertIn("stage=upload", " ".join(logs.output))

    def test_readonly_probe_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(a, "Drive") as drive:
            self.assertFalse(a.probe_drive_access())
            drive.assert_not_called()

    def test_readonly_probe_with_archive_disabled(self):
        env = {"MSK_DRIVE_READONLY_PROBE": "1", "MSK_RESEARCH_DRIVE_ENABLED": "0",
               "MSK_RESEARCH_DRIVE_FOLDER_ID": a.FOLDER,
               **{"MSK_DRIVE_" + k: "test-only" for k in
                  ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN")}}
        with patch.dict(os.environ, env, clear=True), patch.object(a, "Drive") as drive:
            drive.return_value.list.return_value = []
            with self.assertLogs(a.LOG, level="INFO") as logs:
                self.assertTrue(a.probe_drive_access())
            self.assertIn("MSK_DRIVE_PROBE_PASS", " ".join(logs.output))
            drive.return_value.list.assert_called_once()
            drive.return_value.upload.assert_not_called()
            drive.return_value.delete.assert_not_called()
            drive.return_value.close.assert_called_once()

    def test_readonly_probe_missing_credentials(self):
        with patch.dict(os.environ, {"MSK_DRIVE_READONLY_PROBE": "1",
                        "MSK_RESEARCH_DRIVE_FOLDER_ID": a.FOLDER}, clear=True), \
                patch.object(a, "Drive") as drive:
            with self.assertLogs(a.LOG, level="WARNING") as logs:
                self.assertFalse(a.probe_drive_access())
            drive.assert_not_called()
            self.assertIn("OAUTH_CONFIGURATION_MISSING", " ".join(logs.output))

    def test_readonly_probe_sanitizes_oauth_failure(self):
        env = {"MSK_DRIVE_READONLY_PROBE": "1", "MSK_RESEARCH_DRIVE_FOLDER_ID": a.FOLDER,
               **{"MSK_DRIVE_" + k: "test-only" for k in
                  ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN")}}
        response = a.requests.Response()
        response.status_code = 400
        response.url = "https://oauth2.googleapis.com/token"
        exc = a.requests.HTTPError("secret-value-must-not-log", response=response)
        with patch.dict(os.environ, env, clear=True), patch.object(a, "Drive", side_effect=exc):
            with self.assertLogs(a.LOG, level="WARNING") as logs:
                self.assertFalse(a.probe_drive_access())
            output = " ".join(logs.output)
            self.assertIn("stage=token_exchange", output)
            self.assertIn("http=400", output)
            self.assertNotIn("secret-value-must-not-log", output)

    def test_disabled(self):
        with patch.dict(os.environ,{"MSK_RESEARCH_DRIVE_ENABLED":"0"}), patch.object(a,"Drive") as d:
            self.assertIsNone(a.load_cloud_report("SBIN"))
            self.assertFalse(a.store_cloud_report("SBIN","missing","23-Jan-2026"))
            d.assert_not_called()

if __name__ == "__main__":
    unittest.main()
