"""Offline, synthetic safeguards for portfolio backup preflight.

No production paths, network transfers, credentials or real user data.
"""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import portfolio_backup_preflight as preflight


class PortfolioPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="msk-backup-preflight-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "portfolio_data.json"
        self.backup = self.base / "snapshot.json"
        self.fixture = {
            "101": {"SBIN": {"qty": 2, "avg": 123.45}},
            "202": {"TCS": {"qty": 3, "avg": 2345.67}}
        }
        self.source.write_text(json.dumps(self.fixture, sort_keys=True),
                               encoding="utf-8")

    def test_private_snapshot_byte_for_byte_and_no_customer_data_in_output(self):
        before = self.source.read_bytes()
        stat_before = self.source.stat()
        report = preflight.create_local_snapshot(self.source, self.backup)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.stat().st_mtime_ns, stat_before.st_mtime_ns)
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(report["users"], 2)
        self.assertEqual(report["positions"], 2)
        self.assertEqual(report["status"], "local_copy_verified")
        self.assertTrue(report["source_and_copy_equal"])
        self.assertFalse(report["off_host_verified"])
        self.assertFalse(report["independent_restore_verified"])
        self.assertFalse(report["production_cutover_approved"])
        rendered = json.dumps(report)
        for private_value in ("101", "202", "SBIN", "TCS", "123.45", "2345.67"):
            self.assertNotIn('"' + private_value + '"', rendered)
        verified = preflight.verify_source_against_backup(
            self.source, self.backup
        )
        self.assertEqual(verified["sha256"], report["source_sha256"])
        self.assertEqual(verified["status"], "source_and_candidate_match")
        self.assertFalse(verified["production_cutover_approved"])

    def test_snapshot_cannot_replace_existing_backup(self):
        self.backup.write_bytes(b"precious-original-backup")
        before = self.backup.read_bytes()
        with self.assertRaises(preflight.BackupPreflightError):
            preflight.create_local_snapshot(self.source, self.backup)
        self.assertEqual(self.backup.read_bytes(), before)

    def test_rejects_corruption_and_symlink_without_creating_output(self):
        self.source.write_text('{"101":{"SBIN":{"qty":1,"avg":1},'
                               '"SBIN":{"qty":9,"avg":2}}}', encoding="utf-8")
        with self.assertRaises(preflight.BackupPreflightError):
            preflight.create_local_snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())
        self.source.write_text(json.dumps(self.fixture), encoding="utf-8")
        link = self.base / "source-link.json"
        link.symlink_to(self.source)
        with self.assertRaises(preflight.BackupPreflightError):
            preflight.create_local_snapshot(link, self.backup)
        self.assertFalse(self.backup.exists())

    def test_source_changed_while_copying_removes_incomplete_backup(self):
        original = preflight._read_regular
        counter = {"n": 0}

        def changed_between_reads(path):
            counter["n"] += 1
            if counter["n"] == 2:
                changed = {"101": {"SBIN": {"qty": 9, "avg": 123.45}}}
                self.source.write_text(json.dumps(changed), encoding="utf-8")
            return original(path)

        with patch.object(preflight, "_read_regular",
                          side_effect=changed_between_reads):
            with self.assertRaisesRegex(preflight.BackupPreflightError,
                                         "Source changed"):
                preflight.create_local_snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())

    def test_rejects_empty_snapshot_without_explicit_operator_flag(self):
        self.source.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(preflight.BackupPreflightError, "Empty"):
            preflight.create_local_snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())
        result = preflight.create_local_snapshot(self.source, self.backup,
                                                 allow_empty=True)
        self.assertEqual(result["positions"], 0)
        self.assertFalse(result["production_cutover_approved"])

    def test_rejects_permissive_backup_directory(self):
        parent = self.base / "public"
        parent.mkdir(mode=0o755)
        try:
            with self.assertRaisesRegex(preflight.BackupPreflightError,
                                         "mode 0700"):
                preflight.create_local_snapshot(self.source,
                                                parent / "backup.json")
        finally:
            os.chmod(parent, 0o700)

    def test_rejects_mismatched_candidate_and_identical_path(self):
        self.backup.write_text('{"101":{"SBIN":{"qty":42,"avg":5}}}',
                               encoding="utf-8")
        with self.assertRaisesRegex(preflight.BackupPreflightError, "differs"):
            preflight.verify_source_against_backup(self.source, self.backup)
        with self.assertRaisesRegex(preflight.BackupPreflightError, "differ"):
            preflight.verify_source_against_backup(self.source, self.source)

    def test_cli_reports_only_aggregate_and_cannot_approve_production(self):
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with redirect_stdout(output):
            code = preflight.main(["--source", str(self.source),
                                   "--snapshot-to", str(self.backup)])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["off_host_verified"])
        self.assertFalse(report["production_cutover_approved"])


if __name__ == "__main__":
    unittest.main()
