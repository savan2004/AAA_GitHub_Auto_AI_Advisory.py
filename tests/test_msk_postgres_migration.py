"""Offline Postgres cutover/restart simulations; SQLite adapter, no production DB."""
import json
import tempfile
import unittest
from pathlib import Path

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
