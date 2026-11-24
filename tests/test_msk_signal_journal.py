"""Offline safety acceptance: no NSE network, Telegram or real database."""
import unittest
from datetime import datetime, timedelta, timezone

from msk_signal_journal import JournalError, PostgresSignalJournal, make_event

UTC = timezone.utc
BAR = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
WHEN = BAR + timedelta(minutes=2)
DSN = "postgresql://example.invalid/msk?sslmode=require"


def event(kind="OBSERVED", **overrides):
    args = dict(
        strategy="swing_safe", version="v1", symbol="SBIN", direction="LONG",
        candle_at=BAR, kind=kind, event_at=WHEN, source_ref="nse:2026-11-20:SBIN",
        entry="100", stop="96", target1="108", target2="114",
    )
    if kind == "WITHHELD":
        args.update(symbol="UNIVERSE", direction="NONE", source_ref="",
                    entry=None, stop=None, target1=None, target2=None)
    if kind == "PAPER_CLOSE":
        args.update(exit_price="108", outcome="TARGET1")
    args.update(overrides)
    return make_event(**args)


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("SELECT event_at, payload"):
            signal, kind = params
            value = self.store.get((signal, kind))
            if value:
                return Cursor((datetime.fromisoformat(value["event_at"]), value))
            return Cursor()
        if sql.startswith("INSERT INTO msk_signal_journal.events_v1"):
            event_id, signal, kind, _, payload, digest = params
            key = (signal, kind)
            if key in self.store:
                return Cursor()
            import json
            parsed = json.loads(payload)
            parsed["_stored_digest"] = digest
            self.store[key] = parsed
            return Cursor((event_id,))
        if sql.startswith("SELECT digest FROM msk_signal_journal.events_v1"):
            value = self.store.get(tuple(params))
            return Cursor((value["_stored_digest"],) if value else None)
        return Cursor()


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        self.journal = PostgresSignalJournal(
            DSN, connector=lambda _: FakeConn(self.rows))

    def test_wait_is_observation_not_paper_fill(self):
        observed = event()
        self.assertEqual(observed["kind"], "OBSERVED")
        self.assertNotIn("exit_price", observed)
        self.assertNotIn("outcome", observed)
        self.assertTrue(self.journal.append(observed))
        self.assertFalse(self.journal.append(observed))  # safe duplicate

    def test_cannot_open_without_recorded_observation(self):
        with self.assertRaisesRegex(JournalError, "previously recorded OBSERVED"):
            self.journal.append(event("PAPER_OPEN"))

    def test_cannot_close_without_open(self):
        self.journal.append(event())
        with self.assertRaisesRegex(JournalError, "previously recorded PAPER_OPEN"):
            self.journal.append(event("PAPER_CLOSE"))

    def test_full_paper_lifecycle(self):
        plan = event()
        opened = event("PAPER_OPEN", event_at=WHEN + timedelta(minutes=1),
                       source_ref="nse:paper:entry:SBIN")
        closed = event("PAPER_CLOSE", event_at=WHEN + timedelta(days=1),
                       source_ref="nse:paper:exit:SBIN")
        self.assertEqual(plan["signal_id"], opened["signal_id"])
        self.assertEqual(opened["signal_id"], closed["signal_id"])
        self.assertNotEqual(opened["event_id"], closed["event_id"])
        for item in (plan, opened, closed):
            self.assertTrue(self.journal.append(item))
            self.assertFalse(self.journal.append(item))
        self.assertEqual(len(self.rows), 3)

    def test_conflicting_duplicate_fails_closed(self):
        self.journal.append(event())
        with self.assertRaisesRegex(JournalError, "different immutable data"):
            self.journal.append(event(entry="101", stop="96", target1="108", target2="114"))

    def test_open_cannot_change_initial_trade_levels(self):
        self.journal.append(event())
        with self.assertRaisesRegex(JournalError, "levels differ"):
            self.journal.append(event("PAPER_OPEN", stop="95"))

    def test_actual_paper_fill_may_differ_from_reference_price(self):
        self.journal.append(event())
        open_fill = event("PAPER_OPEN", entry="101", event_at=WHEN + timedelta(minutes=1))
        close = event("PAPER_CLOSE", entry="101", event_at=WHEN + timedelta(days=1))
        self.assertTrue(self.journal.append(open_fill))
        self.assertTrue(self.journal.append(close))
        self.assertEqual(self.rows[(open_fill["signal_id"], "PAPER_OPEN")]["entry"], "101")

    def test_close_must_keep_actual_fill_and_consistent_outcome(self):
        self.journal.append(event())
        self.journal.append(event("PAPER_OPEN", entry="101"))
        with self.assertRaisesRegex(JournalError, "levels differ"):
            self.journal.append(event("PAPER_CLOSE", entry="100",
                                      event_at=WHEN + timedelta(days=1)))
        with self.assertRaises(ValueError):
            event("PAPER_CLOSE", exit_price="102", outcome="TARGET1")
        with self.assertRaises(ValueError):
            event("PAPER_CLOSE", exit_price="99", outcome="STOP")

    def test_chronological_transitions(self):
        self.journal.append(event())
        with self.assertRaisesRegex(JournalError, "precedes prior event"):
            self.journal.append(event("PAPER_OPEN", event_at=BAR))

    def test_no_unverified_signal_can_claim_paper_result(self):
        with self.assertRaises(ValueError):
            event("OBSERVED", outcome="TARGET1", exit_price="108")
        with self.assertRaises(ValueError):
            event("PAPER_CLOSE", outcome="MAYBE")
        with self.assertRaises(ValueError):
            event("PAPER_OPEN", source_ref="synthetic:bar")
        with self.assertRaises(ValueError):
            event("PAPER_CLOSE", source_ref="synthetic:bar")

    def test_swing_and_intraday_use_independent_signal_ids(self):
        swing = event()
        intraday = event(strategy="intraday_nifty200")
        self.assertNotEqual(swing["signal_id"], intraday["signal_id"])
        self.assertEqual(swing["event_id"], event()["event_id"])

    def test_withheld_never_contains_fake_price(self):
        withheld = event("WITHHELD")
        self.assertEqual(withheld["symbol"], "UNIVERSE")
        self.assertNotIn("entry", withheld)
        with self.assertRaises(ValueError):
            event("WITHHELD", entry="100")

    def test_price_validation_and_wrong_direction(self):
        for bad in ("nan", "-1", "Infinity", "0", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    event(entry=bad)
        with self.assertRaises(ValueError):
            event(stop="102")
        with self.assertRaises(ValueError):
            event(direction="SHORT", stop="96", target1="108", target2="114")

    def test_awareness_and_storage_security(self):
        with self.assertRaises(ValueError):
            event(event_at=datetime(2026, 11, 20, 10, 2))
        with self.assertRaises(ValueError):
            event(event_at=BAR - timedelta(minutes=1))
        with self.assertRaises(JournalError):
            PostgresSignalJournal("sqlite:///tmp/example.db", connector=lambda _: None)
        with self.assertRaises(JournalError):
            PostgresSignalJournal("postgresql://example.invalid/msk", connector=lambda _: None)

    def test_no_personal_data_in_record(self):
        payload = event()
        joined = " ".join(payload)
        for prohibited in ("chat_id", "user_id", "holdings", "broker", "phone", "token", "account"):
            self.assertNotIn(prohibited, joined)


if __name__ == "__main__":
    unittest.main()
