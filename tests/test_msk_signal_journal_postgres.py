"""Real psycopg-to-PostgreSQL integration on isolated CI test database.

No Neon, production, user IDs, Telegram tokens or broker credentials. This
file also supports separate Python-process write/read checks around a database
service restart to prove journal persistence.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from msk_signal_journal import JournalError, PostgresSignalJournal, make_event

DSN_ENV = "MSK_JOURNAL_CI_DSN"
BASE = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def sample(kind="OBSERVED", *, version="ci-test-v1", source="ci:real-postgres:bar"):
    params = dict(
        strategy="swing_safe", version=version, symbol="SBIN",
        direction="LONG", candle_at=BASE, kind=kind,
        event_at=BASE + timedelta(minutes={"OBSERVED": 2, "PAPER_OPEN": 3, "PAPER_CLOSE": 1440}[kind]),
        source_ref=source, entry="100" if kind == "OBSERVED" else "101",
        stop="96", target1="108", target2="114",
    )
    if kind == "PAPER_CLOSE":
        params.update(exit_price="108", outcome="TARGET1")
    return make_event(**params)


def connect():
    import psycopg
    return psycopg.connect(os.environ[DSN_ENV], connect_timeout=5)


@unittest.skipUnless(os.getenv(DSN_ENV), "isolated PostgreSQL CI database required")
class RealPostgresJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ[DSN_ENV]
        cls.store = PostgresSignalJournal(cls.dsn)

    def test_real_tls_connection_and_schema(self):
        with connect() as connection:
            enabled = connection.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone()[0]
        self.assertTrue(enabled, "test PostgreSQL connection must use TLS")
        self.assertTrue(self.store.append(sample(version="ci-schema")))
        with connect() as connection:
            row = connection.execute(
                "SELECT to_regclass('msk_signal_journal.events_v1')"
            ).fetchone()
        self.assertIsNotNone(row[0])

    def test_identical_retry_and_conflict_across_instances(self):
        item = sample(version="ci-retry")
        self.assertTrue(self.store.append(item))
        other = PostgresSignalJournal(self.dsn)
        self.assertFalse(other.append(item))
        conflicting = sample(version="ci-retry", source="ci:changed-evidence")
        with self.assertRaisesRegex(JournalError, "different immutable data"):
            other.append(conflicting)
        with connect() as connection:
            count = connection.execute(
                "SELECT count(*) FROM msk_signal_journal.events_v1 "
                "WHERE signal_id=%s AND kind='OBSERVED'",
                (item["signal_id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_full_paper_lifecycle_real_database(self):
        plan = sample(version="ci-lifecycle")
        opened = sample("PAPER_OPEN", version="ci-lifecycle", source="ci:independent-fill")
        closed = sample("PAPER_CLOSE", version="ci-lifecycle", source="ci:independent-exit")
        with self.assertRaisesRegex(JournalError, "previously recorded OBSERVED"):
            self.store.append(opened)
        self.assertTrue(self.store.append(plan))
        with self.assertRaisesRegex(JournalError, "previously recorded PAPER_OPEN"):
            self.store.append(closed)
        self.assertTrue(self.store.append(opened))
        self.assertTrue(self.store.append(closed))
        with connect() as connection:
            rows = connection.execute(
                "SELECT kind,payload->>'entry',payload->>'exit_price' "
                "FROM msk_signal_journal.events_v1 "
                "WHERE signal_id=%s ORDER BY event_at",
                (plan["signal_id"],),
            ).fetchall()
        self.assertEqual(rows, [
            ("OBSERVED", "100", None),
            ("PAPER_OPEN", "101", None),
            ("PAPER_CLOSE", "101", "108"),
        ])

    def test_changed_stop_cannot_erase_original_plan(self):
        plan = sample(version="ci-risk")
        self.assertTrue(self.store.append(plan))
        wrong = make_event(
            strategy="swing_safe", version="ci-risk", symbol="SBIN",
            direction="LONG", candle_at=BASE, kind="PAPER_OPEN",
            event_at=BASE + timedelta(minutes=3), source_ref="ci:independent-fill",
            entry="101", stop="95", target1="108", target2="114",
        )
        with self.assertRaisesRegex(JournalError, "levels differ"):
            self.store.append(wrong)

    def test_unverified_connection_and_unlisted_strategy_fail(self):
        with self.assertRaises(JournalError):
            PostgresSignalJournal(self.dsn.replace("sslmode=require", "sslmode=disable"))
        with self.assertRaises(ValueError):
            make_event(strategy="unknown", version="v1", symbol="SBIN", direction="LONG",
                       candle_at=BASE, event_at=BASE, kind="OBSERVED",
                       source_ref="ci:source:quote", entry=100, stop=96,
                       target1=108, target2=114)


def check_restart(mode):
    """These are separate command invocations around a real DB restart."""
    if not os.environ.get(DSN_ENV):
        raise RuntimeError("CI-only PostgreSQL DSN missing")
    item = sample(version="ci-after-db-restart")
    store = PostgresSignalJournal(os.environ[DSN_ENV])
    if mode == "write":
        assert store.append(item), "Expected first write before restart"
        print("PRE_RESTART_PYTHON_WRITE_OK")
    elif mode == "read":
        with connect() as connection:
            row = connection.execute(
                "SELECT payload->>'symbol', payload->>'kind' "
                "FROM msk_signal_journal.events_v1 WHERE event_id=%s",
                (item["event_id"],),
            ).fetchone()
        assert row == ("SBIN", "OBSERVED"), "Entry lost or mutated after restart"
        assert store.append(item) is False, "Duplicate inserted after restart"
        print("POST_RESTART_PYTHON_READBACK_AND_DEDUP_OK")
    else:
        raise ValueError("Unknown restart stage")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in ("write", "read"):
        check_restart(sys.argv[1])
    else:
        unittest.main()
