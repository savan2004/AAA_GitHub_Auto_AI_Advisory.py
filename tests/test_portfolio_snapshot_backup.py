"""Offline, synthetic-only regression tests for the legacy JSON backup."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from portfolio_migration import PortfolioMigrationError, load_legacy_holdings
from portfolio_snapshot_backup import backup_legacy_json


class PortfolioSnapshotBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "portfolio.json"
        self.backup = self.root / "backup.json"
        self.source.write_text(json.dumps({
            "100": {"SBIN": {"qty": 2, "avg": 501.25}},
            "200": {"INFY": {"qty": 1, "avg": 1550}},
        }), encoding="utf-8")

    def test_dry_run_does_not_create_backup(self):
        before = self.source.read_bytes()
        result = backup_legacy_json(self.source, self.backup)
        self.assertEqual(result, {"status": "dry-run", "positions": 2})
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_backup_can_be_read_back_as_independent_restore_source(self):
        before = self.source.read_bytes()
        result = backup_legacy_json(self.source, self.backup, apply=True)
        self.assertEqual(result["status"], "verified-backup")
        self.assertEqual(result["positions"], 2)
        self.assertEqual(result["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertEqual(load_legacy_holdings(self.backup),
                         load_legacy_holdings(self.source))
        self.assertEqual(stat.S_IMODE(os.stat(self.backup).st_mode), 0o600)

    def test_refuses_to_overwrite_backup(self):
        self.backup.write_bytes(b"independent pre-existing backup")
        with self.assertRaisesRegex(PortfolioMigrationError, "overwrite"):
            backup_legacy_json(self.source, self.backup, apply=True)
        self.assertEqual(self.backup.read_bytes(),
                         b"independent pre-existing backup")

    def test_invalid_source_rejected_without_writing(self):
        self.source.write_text('{"0":{"SBIN":{"qty":1,"avg":2}}}', encoding="utf-8")
        with self.assertRaises(PortfolioMigrationError):
            backup_legacy_json(self.source, self.backup, apply=True)
        self.assertFalse(self.backup.exists())

    def test_same_source_and_destination_rejected(self):
        with self.assertRaisesRegex(PortfolioMigrationError, "differ"):
            backup_legacy_json(self.source, self.source, apply=True)

    def test_changed_source_during_validation_rejected(self):
        actual_validator = load_legacy_holdings

        def mutate_after_validation(path):
            rows = actual_validator(path)
            self.source.write_text('{"300":{"SBIN":{"qty":5,"avg":100}}}',
                                   encoding="utf-8")
            return rows

        with patch("portfolio_snapshot_backup.load_legacy_holdings",
                   side_effect=mutate_after_validation):
            with self.assertRaisesRegex(PortfolioMigrationError, "changed"):
                backup_legacy_json(self.source, self.backup, apply=True)
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
