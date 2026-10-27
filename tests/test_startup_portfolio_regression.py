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
            '{"0":{"SBIN":{"qty":1,"avg":2}}}',
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



class _SQLitePostgresConnection:
    """Translate parameter markers for offline SQL-behavior checks only.

    This is NOT a real PostgreSQL integration test.
    """

    def __init__(self, path):
        import sqlite3
        self.conn = sqlite3.connect(path)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, trace):
        try:
            if error_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()

    def execute(self, query, params=()):
        from decimal import Decimal
        converted = tuple(str(p) if isinstance(p, Decimal) else p for p in params)
        return self.conn.execute(query.replace("%s", "?"), converted)


class PostgresPortfolioOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "postgres_sql_simulation.sqlite"
        self.source = self.root / "legacy.json"
        self.source.write_text(json.dumps({
            "100": {"SBIN": {"qty": 2, "avg": 100.25}},
            "200": {"HDFCBANK": {"qty": 3, "avg": 1500.5}}
        }), encoding="utf-8")
        self.dsn = "postgresql://localhost/mock?sslmode=require"
        self.connector = lambda _: _SQLitePostgresConnection(str(self.database))

    def test_default_factory_does_not_open_db_or_change_legacy(self):
        from postgres_portfolio import build_portfolio_backend
        sentinel = object()
        legacy = lambda: sentinel
        selected = build_portfolio_backend(
            legacy, environ={}, connector=lambda _: self.fail("DB connection attempted")
        )
        self.assertIs(selected, sentinel)
        self.assertFalse(self.database.exists())

    def test_cutover_fails_closed_without_approval_or_migration(self):
        from postgres_portfolio import build_portfolio_backend, PortfolioStorageError
        env = {"PORTFOLIO_BACKEND": "postgres",
               "PORTFOLIO_DATABASE_URL": self.dsn}
        with self.assertRaisesRegex(PortfolioStorageError, "approval"):
            build_portfolio_backend(lambda: object(), environ=env, connector=self.connector)
        self.assertFalse(self.database.exists())
        env["PORTFOLIO_CUTOVER_APPROVED"] = "true"
        with self.assertRaisesRegex(PortfolioStorageError, "marker"):
            build_portfolio_backend(lambda: object(), environ=env, connector=self.connector)

    def test_migration_then_reopen_add_remove_and_weighted_average(self):
        from postgres_portfolio import (
            import_legacy_to_postgres, build_portfolio_backend,
        )
        self.assertEqual(
            import_legacy_to_postgres(self.source, self.dsn, connector=self.connector),
            {"status": "dry-run", "positions": 2}
        )
        self.assertFalse(self.database.exists())
        result = import_legacy_to_postgres(
            self.source, self.dsn, connector=self.connector, apply=True
        )
        self.assertEqual(result["status"], "imported")
        env = {"PORTFOLIO_BACKEND": "postgres",
               "PORTFOLIO_DATABASE_URL": self.dsn,
               "PORTFOLIO_CUTOVER_APPROVED": "true"}
        store = build_portfolio_backend(
            lambda: self.fail("Fallback occurred"), environ=env,
            connector=self.connector
        )
        self.assertEqual(store.get(100)["SBIN"], {"qty": 2.0, "avg": 100.25})
        store.add(100, "SBIN", 2, 200.25)
        self.assertEqual(store.get(100)["SBIN"], {"qty": 4.0, "avg": 150.25})
        # A reopened store must find the same persisted positions.
        restarted = build_portfolio_backend(
            lambda: self.fail("Fallback occurred"), environ=env,
            connector=self.connector
        )
        self.assertEqual(restarted.get(100)["SBIN"]["qty"], 4.0)
        self.assertTrue(restarted.remove(100, "SBIN"))
        self.assertFalse(restarted.remove(100, "SBIN"))
        self.assertEqual(restarted.get(100), {})
        self.assertEqual(restarted.get(200)["HDFCBANK"]["qty"], 3.0)

    def test_import_detects_corrupted_cost_with_same_row_count_and_rolls_back(self):
        from postgres_portfolio import import_legacy_to_postgres, PortfolioStorageError

        class CorruptingConnection(_SQLitePostgresConnection):
            def execute(self, query, params=()):
                # Simulate a storage/adapter fault: count is correct, but the
                # second imported position has an incorrect average cost.
                if (query.startswith("INSERT INTO holdings(uid, symbol")
                        and len(params) == 4 and params[1] == "HDFCBANK"):
                    params = (params[0], params[1], params[2], "1501.5")
                return super().execute(query, params)

        connector = lambda _: CorruptingConnection(str(self.database))
        with self.assertRaisesRegex(PortfolioStorageError, "readback mismatch"):
            import_legacy_to_postgres(
                self.source, self.dsn, connector=connector, apply=True
            )
        with self.connector(self.dsn) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM portfolio_cutover").fetchone()[0], 0
            )

    def test_reimport_same_snapshot_no_duplicate_or_loss(self):
        from postgres_portfolio import import_legacy_to_postgres
        import_legacy_to_postgres(self.source, self.dsn,
                                  connector=self.connector, apply=True)
        self.assertEqual(
            import_legacy_to_postgres(
                self.source, self.dsn, connector=self.connector, apply=True
            )["status"], "already-imported"
        )
        with self.connector(self.dsn) as conn:
            count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        self.assertEqual(count, 2)

    def test_conflicting_snapshot_is_refused_without_overwriting(self):
        from postgres_portfolio import import_legacy_to_postgres, PortfolioStorageError
        import_legacy_to_postgres(self.source, self.dsn,
                                  connector=self.connector, apply=True)
        self.source.write_text(json.dumps({
            "100": {"SBIN": {"qty": 99, "avg": 1}}
        }), encoding="utf-8")
        with self.assertRaisesRegex(PortfolioStorageError, "Different legacy"):
            import_legacy_to_postgres(
                self.source, self.dsn, connector=self.connector, apply=True
            )
        with self.connector(self.dsn) as conn:
            qty = conn.execute(
                "SELECT qty FROM holdings WHERE uid = 100 AND symbol = 'SBIN'"
            ).fetchone()[0]
        self.assertEqual(qty, 2)

    def test_existing_unmarked_holdings_are_not_overwritten(self):
        from postgres_portfolio import (
            PortfolioStorageError, import_legacy_to_postgres,
        )
        with self.connector(self.dsn) as conn:
            conn.execute(
                "CREATE TABLE holdings (uid BIGINT, symbol TEXT, qty REAL, avg REAL)"
            )
            conn.execute("INSERT INTO holdings VALUES (100, 'SBIN', 50, 1)")
        with self.assertRaisesRegex(PortfolioStorageError, "refusing overwrite"):
            import_legacy_to_postgres(
                self.source, self.dsn, connector=self.connector, apply=True
            )
        with self.connector(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT qty FROM holdings WHERE uid = 100"
            ).fetchone()[0], 50)

    def test_empty_legacy_snapshot_requires_explicit_approval(self):
        from postgres_portfolio import import_legacy_to_postgres, PortfolioStorageError
        self.source.write_text("{}", encoding="utf-8")
        self.assertEqual(
            import_legacy_to_postgres(self.source, self.dsn, connector=self.connector),
            {"status": "dry-run", "positions": 0},
        )
        with self.assertRaisesRegex(PortfolioStorageError, "Empty legacy portfolio"):
            import_legacy_to_postgres(self.source, self.dsn, connector=self.connector, apply=True)
        self.assertFalse(self.database.exists())
        result = import_legacy_to_postgres(
            self.source, self.dsn, connector=self.connector, apply=True, allow_empty=True
        )
        self.assertEqual(result, {"status": "imported", "positions": 0})

    def test_invalid_input_is_rejected_before_any_db_write(self):
        from postgres_portfolio import import_legacy_to_postgres
        from portfolio_migration import PortfolioMigrationError
        self.source.write_text(
            '{"100":{"SBIN":{"qty":2,"avg":NaN}}}', encoding="utf-8"
        )
        with self.assertRaises(PortfolioMigrationError):
            import_legacy_to_postgres(
                self.source, self.dsn, connector=self.connector, apply=True
            )
        self.assertFalse(self.database.exists())

    def test_invalid_backend_numeric_and_connection_settings_fail(self):
        from postgres_portfolio import (
            PortfolioStorageError, build_portfolio_backend,
            import_legacy_to_postgres,
        )
        with self.assertRaises(PortfolioStorageError):
            build_portfolio_backend(lambda: None, environ={"PORTFOLIO_BACKEND": "mysql"})
        with self.assertRaises(PortfolioStorageError):
            import_legacy_to_postgres(
                self.source, "postgresql://db.example.com/portfolio",
                connector=self.connector
            )
        import_legacy_to_postgres(self.source, self.dsn,
                                  connector=self.connector, apply=True)
        store = build_portfolio_backend(
            lambda: None, environ={
                "PORTFOLIO_BACKEND": "postgres",
                "PORTFOLIO_DATABASE_URL": self.dsn,
                "PORTFOLIO_CUTOVER_APPROVED": "true",
            }, connector=self.connector
        )
        for args in ((100, "SBIN", 0, 100), (100, "SBIN", 1, float("nan")),
                     (100, "BAD;SQL", 1, 10), (True, "SBIN", 1, 10)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                store.add(*args)

    def test_main_still_uses_legacy_manager_through_safe_factory(self):
        source = MAIN_FILE.read_text(encoding="utf-8")
        self.assertIn("portfolio = build_portfolio_backend(PortfolioManager)", source)
        self.assertIn("from postgres_portfolio import build_portfolio_backend", source)


if __name__ == "__main__":
    unittest.main()
