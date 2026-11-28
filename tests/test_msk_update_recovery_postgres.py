"""Isolated real PostgreSQL validation of read-only Telegram recovery reporting.

Only runs after explicit opt-in on a disposable localhost test database.
Never connects to a live MSK database or stores real Telegram identifiers.
"""
import os
import unittest
from urllib.parse import urlsplit

from msk_update_recovery import recovery_report


class RecoveryPostgresTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("MSK_RECOVERY_PG_TEST") == "YES", "real PostgreSQL test not opted in")
    def test_readonly_report_on_synthetic_isolated_postgres(self):
        dsn = os.environ.get("MSK_RECOVERY_TEST_DATABASE_URL", "")
        parsed = urlsplit(dsn)
        # Guard BEFORE connecting or creating any test tables.
        if (parsed.scheme not in ("postgres", "postgresql")
                or parsed.hostname not in ("localhost", "127.0.0.1")
                or parsed.path != "/msk_recovery_test"
                or parsed.username != "msk_test"):
            self.fail("Refusing nonlocal or nonisolated PostgreSQL integration target")
        import psycopg

        with psycopg.connect(dsn) as conn:
            actual = conn.execute("SELECT current_database(), current_user").fetchone()
            self.assertEqual(actual, ("msk_recovery_test", "msk_test"))
            conn.execute("CREATE SCHEMA msk_bot_state")
            conn.execute("""CREATE TABLE msk_bot_state.telegram_updates (
                update_id BIGINT PRIMARY KEY,
                stage TEXT NOT NULL,
                claimed_at TIMESTAMPTZ NOT NULL
            )""")
            conn.execute("""INSERT INTO msk_bot_state.telegram_updates
                (update_id,stage,claimed_at) VALUES
                (1001,'claimed',CURRENT_TIMESTAMP - INTERVAL '60 minutes'),
                (1002,'failed',CURRENT_TIMESTAMP - INTERVAL '20 minutes'),
                (1003,'claimed',CURRENT_TIMESTAMP - INTERVAL '5 minutes'),
                (1004,'dispatched',CURRENT_TIMESTAMP - INTERVAL '90 minutes')""")

        class CheckedConnection:
            def __init__(self, url):
                self.connection = psycopg.connect(url)
                self.readonly_confirmed = False
            def __enter__(self):
                self.connection.__enter__()
                return self
            def __exit__(self, *args):
                return self.connection.__exit__(*args)
            def execute(self, sql, params=None):
                result = self.connection.execute(sql, params)
                if sql == "SET TRANSACTION READ ONLY":
                    mode = self.connection.execute("SHOW transaction_read_only").fetchone()[0]
                    self.readonly_confirmed = mode == "on"
                    assert self.readonly_confirmed, "PostgreSQL transaction is not read-only"
                return result

        checked = []
        def connector(url):
            conn = CheckedConnection(url)
            checked.append(conn)
            return conn

        result = recovery_report(dsn, minimum_age_minutes=15, connector=connector)
        self.assertTrue(checked[0].readonly_confirmed)
        self.assertEqual(result["aged_unresolved_count"], 2)
        self.assertEqual(result["stages"]["claimed"]["count"], 1)
        self.assertEqual(result["stages"]["failed"]["count"], 1)
        self.assertFalse(result["automatic_replay_allowed"])
        self.assertFalse(result["portfolio_execution_verified"])

        # The report is SELECT-only: all original rows remain unchanged.
        with psycopg.connect(dsn) as conn:
            count = conn.execute("SELECT COUNT(*) FROM msk_bot_state.telegram_updates").fetchone()[0]
            self.assertEqual(count, 4)


if __name__ == "__main__":
    unittest.main()
