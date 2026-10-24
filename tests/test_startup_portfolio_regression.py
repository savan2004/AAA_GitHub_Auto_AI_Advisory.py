"""Regression checks for webhook configuration and portfolio deadlocks.

Runs without importing main.py, which needs live Telegram credentials.
"""
import ast
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import unittest

MAIN_FILE = Path(__file__).resolve().parents[1] / "main.py"


def load_portfolio_manager():
    source = MAIN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
    namespace = {"os": os, "json": json, "threading": threading, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MAIN_FILE), "exec"), namespace)
    return namespace["PortfolioManager"]


class PortfolioRegressionTests(unittest.TestCase):
    def test_remove_does_not_deadlock_and_persists(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "positions.json")
            manager = cls(path)
            manager.add(100, "SBIN", 2, 500)
            result = []
            worker = threading.Thread(target=lambda: result.append(manager.remove(100, "SBIN")), daemon=True)
            worker.start()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "remove() deadlocked on the portfolio lock")
            self.assertEqual(result, [True])
            self.assertEqual(cls(path).get(100), {})

    def test_missing_position_returns_false(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(cls(str(Path(folder) / "positions.json")).remove(1, "MISSING"))


class WebhookSecurityTests(unittest.TestCase):
    def test_token_not_permitted_in_webhook_base_url(self):
        source = MAIN_FILE.read_text(encoding="utf-8")
        self.assertIn('if TOKEN in WEBHOOK_URL:', source)
        self.assertIn('raise RuntimeError("WEBHOOK_URL must not contain TELEGRAM_TOKEN")', source)
        self.assertIn('secret_token=WEBHOOK_SECRET', source)
        self.assertIn('WEBHOOK_PATH = "/webhook/telegram"', source)



class PortfolioMigrationSafetyTests(unittest.TestCase):
    """Offline checks: no Telegram credentials, network, or live user data."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "legacy.json"
        self.database = self.root / "holdings.sqlite3"
        self.expected = {
            "100": {"SBIN": {"qty": 2, "avg": 500.25}},
            "201": {"RELIANCE": {"qty": 3, "avg": 2400}},
        }
        self.source.write_text(json.dumps(self.expected), encoding="utf-8")

    def test_dry_run_never_creates_database_or_changes_source(self):
        from portfolio_migration import migrate_legacy_holdings
        original = self.source.read_bytes()
        self.assertEqual(migrate_legacy_holdings(self.source, self.database),
                         {"status": "dry-run", "positions": 2})
        self.assertFalse(self.database.exists())
        self.assertEqual(self.source.read_bytes(), original)

    def test_import_retains_holdings_after_database_reopen(self):
        import sqlite3
        from portfolio_migration import migrate_legacy_holdings
        original = self.source.read_bytes()
        self.assertEqual(migrate_legacy_holdings(
            self.source, self.database, apply=True)["status"], "imported")
        with sqlite3.connect(self.database) as db:
            rows = db.execute(
                "SELECT uid, symbol, qty, avg FROM holdings ORDER BY uid"
            ).fetchall()
        self.assertEqual(rows, [
            (100, "SBIN", 2.0, 500.25),
            (201, "RELIANCE", 3.0, 2400.0),
        ])
        self.assertEqual(self.source.read_bytes(), original)

    def test_repeated_identical_import_does_not_duplicate_lots(self):
        import sqlite3
        from portfolio_migration import migrate_legacy_holdings
        migrate_legacy_holdings(self.source, self.database, apply=True)
        second = migrate_legacy_holdings(self.source, self.database, apply=True)
        self.assertEqual(second["status"], "already-imported")
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 2)

    def test_existing_different_positions_are_not_overwritten(self):
        import sqlite3
        from portfolio_migration import PortfolioMigrationError, migrate_legacy_holdings
        migrate_legacy_holdings(self.source, self.database, apply=True)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE holdings SET avg=999 WHERE uid=100")
        with self.assertRaisesRegex(PortfolioMigrationError, "no overwrite"):
            migrate_legacy_holdings(self.source, self.database, apply=True)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                "SELECT avg FROM holdings WHERE uid=100"
            ).fetchone()[0], 999)

    def test_invalid_legacy_data_rejected_before_db_created(self):
        from portfolio_migration import PortfolioMigrationError, migrate_legacy_holdings
        invalid_inputs = (
            '{"100":{"SBIN":{"qty":1,"avg":2},"SBIN":{"qty":4,"avg":5}}}',
            '{"001":{"SBIN":{"qty":1,"avg":2}}}',
            '{"100":{"SBIN":{"qty":-1,"avg":2}}}',
            '{"100":{"SBIN":{"qty":true,"avg":2}}}',
            '{"100":{"SBIN":{"qty":1,"avg":NaN}}}',
            '{"100":{"SBIN":{"qty":1,"avg":2,"extra":42}}}',
        )
        for payload in invalid_inputs:
            with self.subTest(payload=payload):
                self.source.write_text(payload, encoding="utf-8")
                with self.assertRaises(PortfolioMigrationError):
                    migrate_legacy_holdings(self.source, self.database, apply=True)
                self.assertFalse(self.database.exists())

    def test_backup_restores_positions_and_never_overwrites(self):
        import sqlite3
        from portfolio_migration import (
            PortfolioMigrationError, backup_sqlite_database, migrate_legacy_holdings,
        )
        migrate_legacy_holdings(self.source, self.database, apply=True)
        backup = self.root / "snapshot.sqlite3"
        backup_sqlite_database(self.database, backup)
        with sqlite3.connect(backup) as restored:
            self.assertEqual(restored.execute(
                "SELECT COUNT(*) FROM holdings"
            ).fetchone()[0], 2)
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        with self.assertRaises(PortfolioMigrationError):
            backup_sqlite_database(self.database, backup)
        with sqlite3.connect(backup) as restored:
            self.assertEqual(restored.execute(
                "SELECT qty FROM holdings WHERE uid=100 AND symbol='SBIN'"
            ).fetchone()[0], 2.0)


if __name__ == "__main__":
    unittest.main()
