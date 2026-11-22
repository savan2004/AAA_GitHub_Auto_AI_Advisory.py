"""Real PostgreSQL-only tests for Telegram portfolio operation idempotency.

Uses a disposable localhost database with SYNTHETIC users and positions.
Never reads live holdings, sends Telegram, or changes production configuration.
"""
import concurrent.futures
import os
import unittest
from urllib.parse import urlsplit

from postgres_portfolio import (
    PostgresPortfolioStore, PortfolioStorageError, _create_schema,
)


class PortfolioMessageIdempotencyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("MSK_IDEMPOTENCY_PG_TEST") != "YES":
            raise unittest.SkipTest("isolated real PostgreSQL test not enabled")
        raw = os.environ.get("MSK_IDEMPOTENCY_TEST_DATABASE_URL", "")
        p = urlsplit(raw)
        if (p.scheme not in ("postgresql", "postgres")
                or p.hostname not in ("localhost", "127.0.0.1")
                or p.port != 5432
                or p.path != "/msk_idempotency_test"
                or p.username != "msk_test" or p.query or p.fragment):
            raise RuntimeError("Refusing database outside the isolated local test target")
        import psycopg
        cls.psycopg = psycopg
        cls.raw_dsn = raw
        # Application DSN validator insists on TLS; Docker-only test service
        # has no TLS. The injected connector connects ONLY to the checked
        # disposable local URL; production connector logic is not modified.
        cls.app_dsn = "postgresql://msk_test@localhost/msk_idempotency_test?sslmode=require"
        cls.connector = staticmethod(lambda _dsn: psycopg.connect(raw))
        with cls.connector(cls.app_dsn) as conn:
            _create_schema(conn)
            conn.execute(
                """INSERT INTO portfolio_cutover
                   (id, source_sha256, initial_position_count)
                   VALUES (1,%s,0) ON CONFLICT(id) DO NOTHING""",
                ("1" * 64,),
            )

    def setUp(self):
        # The hostname/DB/user guard above is mandatory before deleting
        # synthetic test rows. This DB is unique to this CI test suite.
        with self.connector(self.app_dsn) as conn:
            conn.execute("DELETE FROM portfolio_message_operations")
            conn.execute("DELETE FROM holdings")
            conn.execute("DELETE FROM portfolio_accounts")
        self.store = PostgresPortfolioStore(self.app_dsn, connector=self.connector)

    def test_same_buy_once_even_after_restart(self):
        first = self.store.add(100, "SBIN", 2, 500.25, telegram_message_id=201)
        self.assertEqual(first, {"applied": True, "duplicate": False})
        reopened = PostgresPortfolioStore(self.app_dsn, connector=self.connector)
        retry = reopened.add(100, "SBIN", 2, 500.25, telegram_message_id=201)
        self.assertEqual(retry, {"applied": False, "duplicate": True})
        self.assertEqual(reopened.get(100)["SBIN"]["qty"], 2.0)

    def test_duplicate_sell_cannot_delete_later_buy(self):
        self.store.add(100, "SBIN", 2, 500.25, telegram_message_id=301)
        first = self.store.remove(100, "SBIN", telegram_message_id=302)
        self.assertEqual(first, {"removed": True, "duplicate": False})
        self.store.add(100, "SBIN", 1, 501, telegram_message_id=303)
        retry = self.store.remove(100, "SBIN", telegram_message_id=302)
        self.assertEqual(retry, {"removed": True, "duplicate": True})
        self.assertEqual(self.store.get(100)["SBIN"]["qty"], 1.0)

    def test_conflicting_message_reuse_fails_without_mutation(self):
        self.store.add(100, "SBIN", 2, 500, telegram_message_id=401)
        with self.assertRaises(PortfolioStorageError):
            self.store.add(100, "SBIN", 5, 500, telegram_message_id=401)
        with self.assertRaises(PortfolioStorageError):
            self.store.remove(100, "SBIN", telegram_message_id=401)
        self.assertEqual(self.store.get(100)["SBIN"]["qty"], 2.0)

    def test_crash_after_receipt_write_before_commit_rolls_back_both(self):
        psycopg = self.psycopg
        raw = self.raw_dsn

        class InjectedCrash:
            def __init__(self):
                self.conn = psycopg.connect(raw)

            def __enter__(self):
                self.conn.__enter__()
                return self

            def __exit__(self, *exc):
                return self.conn.__exit__(*exc)

            def execute(self, sql, params=None):
                result = self.conn.execute(sql, params)
                if "INSERT INTO portfolio_message_operations" in sql:
                    raise RuntimeError("simulated crash before commit")
                return result

        failing_store = PostgresPortfolioStore(
            self.app_dsn, connector=lambda _: InjectedCrash()
        )
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            failing_store.add(100, "SBIN", 2, 500, telegram_message_id=501)
        self.assertEqual(self.store.get(100), {})
        with self.connector(self.app_dsn) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM portfolio_message_operations"
            ).fetchone()[0]
            self.assertEqual(count, 0)
        self.assertEqual(
            self.store.add(100, "SBIN", 2, 500, telegram_message_id=501),
            {"applied": True, "duplicate": False},
        )
        self.assertEqual(self.store.get(100)["SBIN"]["qty"], 2.0)

    def test_failed_sell_replay_never_removes_later_buy(self):
        first = self.store.remove(100, "SBIN", telegram_message_id=701)
        self.assertEqual(first, {"removed": False, "duplicate": False})
        self.store.add(100, "SBIN", 1, 501, telegram_message_id=702)
        reopened = PostgresPortfolioStore(self.app_dsn, connector=self.connector)
        retry = reopened.remove(100, "SBIN", telegram_message_id=701)
        self.assertEqual(retry, {"removed": False, "duplicate": True})
        self.assertEqual(reopened.get(100)["SBIN"]["qty"], 1.0)

    def test_lost_ack_after_commit_does_not_duplicate_buy(self):
        psycopg = self.psycopg
        raw = self.raw_dsn

        class LostCommitAck:
            def __init__(self):
                self.conn = psycopg.connect(raw)
                self.changed = False

            def __enter__(self):
                self.conn.__enter__()
                return self

            def __exit__(self, *exc):
                result = self.conn.__exit__(*exc)
                if self.changed and exc[0] is None:
                    raise RuntimeError("simulated lost acknowledgment after commit")
                return result

            def execute(self, sql, params=None):
                result = self.conn.execute(sql, params)
                if "INSERT INTO portfolio_message_operations" in sql:
                    self.changed = True
                return result

        failing_store = PostgresPortfolioStore(
            self.app_dsn, connector=lambda _: LostCommitAck()
        )
        with self.assertRaisesRegex(RuntimeError, "lost acknowledgment"):
            failing_store.add(100, "SBIN", 3, 500, telegram_message_id=801)
        reopened = PostgresPortfolioStore(self.app_dsn, connector=self.connector)
        self.assertEqual(reopened.get(100)["SBIN"]["qty"], 3.0)
        self.assertEqual(
            reopened.add(100, "SBIN", 3, 500, telegram_message_id=801),
            {"applied": False, "duplicate": True},
        )
        self.assertEqual(reopened.get(100)["SBIN"]["qty"], 3.0)

    def test_parallel_duplicate_buy_applies_once(self):
        def operation(_):
            store = PostgresPortfolioStore(self.app_dsn, connector=self.connector)
            return store.add(100, "SBIN", 2, 500, telegram_message_id=601)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(operation, range(5)))
        self.assertEqual(sum(r["applied"] for r in responses), 1)
        self.assertEqual(sum(r["duplicate"] for r in responses), 4)
        self.assertEqual(self.store.get(100)["SBIN"]["qty"], 2.0)

    def test_missing_invalid_message_identifier_rejected(self):
        for invalid in (0, -1, True, "701", 2**63):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.add(100, "SBIN", 1, 500,
                               telegram_message_id=invalid)
        self.assertEqual(self.store.get(100), {})


if __name__ == "__main__":
    unittest.main()
