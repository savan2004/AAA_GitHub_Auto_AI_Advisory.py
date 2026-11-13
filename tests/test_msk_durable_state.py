"""MSK Bot P0 durable webhook/subscriber regressions, synthetic identities only.

No Telegram token, external request, paid entitlement or real customer account.
Real PostgreSQL concurrency/restore is separately tested in PR94 integration.
"""
import ast
import concurrent.futures
import copy
import hmac
import json
import logging
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MAIN_FILE = ROOT / "main.py"
SECRET = "synthetic-webhook-secret-123456789"


class ImmediateExecutor:
    def __init__(self):
        self.reject = False

    def submit(self, fn, *args, **kwargs):
        if self.reject:
            raise RuntimeError("synthetic executor unavailable")
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class SpyBot:
    def __init__(self):
        self.received = []
        self.fail = False

    def process_new_updates(self, updates):
        if self.fail:
            raise RuntimeError("synthetic handler failure")
        self.received.extend(update["update_id"] for update in updates)


class SyntheticLedger:
    """In-memory shared fake only; real durability is proven with PostgreSQL16."""

    def __init__(self):
        self.statuses = {}
        self.claimed_users = {}
        self.outage = False

    def claim(self, update_id, user_id=None):
        if self.outage:
            raise RuntimeError("synthetic DB outage")
        if update_id in self.statuses:
            return False
        self.statuses[update_id] = "claimed"
        self.claimed_users[update_id] = user_id
        return True

    def finish(self, update_id, *, dispatched):
        if self.statuses[update_id] != "claimed":
            raise AssertionError("duplicate completion")
        self.statuses[update_id] = "dispatched" if dispatched else "failed"

    def abandon_unscheduled(self, update_id):
        if self.statuses[update_id] != "claimed":
            raise AssertionError("not a pending claim")
        self.statuses.pop(update_id)
        self.claimed_users.pop(update_id)


def webhook_harness(ledger):
    source = MAIN_FILE.read_text(encoding="utf-8")
    names = {"_process_webhook", "_complete_durable_webhook", "webhook"}
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            fn = copy.deepcopy(node)
            fn.decorator_list = []
            nodes.append(fn)
    if set(x.name for x in nodes) != names:
        raise AssertionError("Incomplete real webhook functions")
    bot = SpyBot()
    executor = ImmediateExecutor()
    ns = {
        "hmac": hmac, "json": json, "WEBHOOK_SECRET": SECRET,
        "logger": logging.getLogger("msk-durable-test"),
        "bot": bot,
        "telebot": SimpleNamespace(types=SimpleNamespace(
            Update=SimpleNamespace(de_json=staticmethod(json.loads))
        )),
        "webhook_executor": executor,
        "_webhook_lock": threading.Lock(),
        "_webhook_slots": threading.BoundedSemaphore(1),
        "_processed_updates": __import__("collections").deque(maxlen=1000),
        "_processed_updates_set": set(),
        "_durable_update_ledger": ledger,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]),
                 str(MAIN_FILE), "exec"), ns)

    def send(update_id=1200, *, sender=1001, secret=SECRET, callback=False):
        actor = {"id": sender}
        event = ({"callback_query": {"from": actor, "data": "fund|SBIN",
                                     "message": {"chat": {"id": 1001}}}}
                 if callback else
                 {"message": {"from": actor, "text": "/buy SBIN 1 100",
                              "chat": {"id": 1001, "type": "private"}}})
        payload = {"update_id": update_id, **event}
        ns["request"] = SimpleNamespace(
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            content_length=len(json.dumps(payload)),
            get_json=lambda silent=True: payload,
        )
        return ns["webhook"]()

    return ns, bot, executor, send


class WebhookReplayTests(unittest.TestCase):
    def test_duplicate_after_restart_has_one_handler_dispatch(self):
        ledger = SyntheticLedger()
        first, bot1, _, send1 = webhook_harness(ledger)
        self.assertEqual(send1(), ("ok", 200))
        self.assertEqual(bot1.received, [1200])
        self.assertEqual(ledger.statuses[1200], "dispatched")
        self.assertEqual(ledger.claimed_users[1200], 1001)
        second, bot2, _, send2 = webhook_harness(ledger)
        self.assertEqual(send2(), ("ok", 200))
        self.assertEqual(bot2.received, [])
        self.assertEqual(ledger.statuses[1200], "dispatched")
        for ns in (first, second):
            slots = ns["_webhook_slots"]
            self.assertTrue(slots.acquire(blocking=False), "slot leak")
            slots.release()

    def test_claimed_and_failed_update_never_replay_automatically(self):
        ledger = SyntheticLedger()
        _, bot, _, send = webhook_harness(ledger)
        bot.fail = True
        self.assertEqual(send(update_id=1210), ("ok", 200))
        self.assertEqual(ledger.statuses[1210], "failed")
        bot.fail = False
        self.assertEqual(send(update_id=1210), ("ok", 200))
        self.assertEqual(bot.received, [])
        ledger.statuses[1211] = "claimed"  # crash-before-finish simulation
        self.assertEqual(send(update_id=1211), ("ok", 200))
        self.assertEqual(bot.received, [])
        self.assertEqual(ledger.statuses[1211], "claimed")

    def test_store_outage_returns_503_without_dispatch_and_releases_slot(self):
        ledger = SyntheticLedger()
        ns, bot, _, send = webhook_harness(ledger)
        ledger.outage = True
        self.assertEqual(send(update_id=1220), ("durable queue unavailable", 503))
        self.assertEqual(bot.received, [])
        self.assertTrue(ns["_webhook_slots"].acquire(blocking=False))
        ns["_webhook_slots"].release()
        ledger.outage = False
        self.assertEqual(send(update_id=1220), ("ok", 200))
        self.assertEqual(bot.received, [1220])

    def test_submit_failure_abandons_only_unscheduled_claim(self):
        ledger = SyntheticLedger()
        ns, bot, executor, send = webhook_harness(ledger)
        executor.reject = True
        self.assertEqual(send(update_id=1230), ("busy", 503))
        self.assertNotIn(1230, ledger.statuses)
        self.assertEqual(bot.received, [])
        self.assertTrue(ns["_webhook_slots"].acquire(blocking=False))
        ns["_webhook_slots"].release()
        executor.reject = False
        self.assertEqual(send(update_id=1230), ("ok", 200))
        self.assertEqual(bot.received, [1230])

    def test_invalid_header_or_update_id_cannot_create_claim(self):
        ledger = SyntheticLedger()
        ns, bot, _, send = webhook_harness(ledger)
        self.assertEqual(send(update_id=1231, secret="invalid"), ("unauthorized", 403))
        for invalid in (True, -1, 9223372036854775808):
            self.assertEqual(send(update_id=invalid),
                             ("invalid Telegram update", 400))
        self.assertEqual(ledger.statuses, {})
        self.assertEqual(bot.received, [])

    def test_callback_sender_identity_recorded_but_never_entitled(self):
        ledger = SyntheticLedger()
        _, bot, _, send = webhook_harness(ledger)
        self.assertEqual(send(update_id=1232, sender=2002, callback=True),
                         ("ok", 200))
        self.assertEqual(ledger.claimed_users[1232], 2002)
        self.assertEqual(bot.received, [1232])


class SubscriberInputSafetyTests(unittest.TestCase):
    def test_only_explicit_opt_in_can_open_durable_db(self):
        from msk_durable_state import (
            DurableStateError, build_durable_bot_state
        )
        marker = lambda _: self.fail("Unexpected DB connection")
        self.assertIsNone(build_durable_bot_state(environ={}, connector=marker))
        with self.assertRaisesRegex(DurableStateError, "without MSK_STATE"):
            build_durable_bot_state(
                environ={"MSK_DURABLE_UPDATES_ENABLED": "true"},
                connector=marker
            )
        with self.assertRaises(DurableStateError):
            build_durable_bot_state(
                environ={"MSK_DURABLE_UPDATES_ENABLED": "YES"},
                connector=marker
            )

    def test_invalid_ids_expiry_and_features_fail_before_db_access(self):
        from msk_durable_state import _id, _utc_aware
        for value in (True, 0, -1, 3.5, "123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _id(value)
        self.assertEqual(_id(0, allow_zero=True), 0)
        with self.assertRaises(ValueError):
            _utc_aware(datetime(2026, 11, 12))

    def test_schema_does_not_publish_payments_or_store_raw_payload(self):
        from msk_durable_state import _SCHEMA
        tables = " ".join(_SCHEMA)
        self.assertIn("msk_bot_state.telegram_updates", tables)
        self.assertIn("msk_bot_state.entitlements", tables)
        self.assertIn("msk_bot_state.review_events", tables)
        self.assertNotIn("message_text", tables)
        self.assertNotIn("payment_token", tables)


if __name__ == "__main__":
    unittest.main()
