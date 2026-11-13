"""Offline checks for Telegram Bot API acknowledgment accounting.

A Bot API message_id is server acceptance only; no claim that a user read it.
These tests exercise the real safe_send implementation without Telegram calls.
"""
import ast
import logging
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class FakeLedger:
    def __init__(self, fail=False):
        self.acks = []
        self.fail = fail

    def record_delivery_ack(self, chat_id, message_id, kind):
        if self.fail:
            raise RuntimeError("synthetic receipt storage failure")
        self.acks.append((chat_id, message_id, kind))


class FakeBot:
    def __init__(self):
        self.sent = []
        self.fail_first = False
        self.fail_all = False

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        if self.fail_all or (self.fail_first and len(self.sent) == 1):
            raise ValueError("bad request: can't parse entities")
        return SimpleNamespace(message_id=1000 + len(self.sent))


def safe_send_harness(ledger=None):
    tree = ast.parse(SRC)
    names = {"safe_send", "_record_telegram_api_ack"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    assert {n.name for n in nodes} == names
    bot = FakeBot()
    env = {
        "bot": bot,
        "_durable_update_ledger": ledger,
        "logger": logging.getLogger("mock-telegram-ack"),
        "re": re,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]),
                 "main.py", "exec"), env)
    return env, bot


class TelegramAckTests(unittest.TestCase):
    def test_positive_bot_api_result_records_one_receipt(self):
        ledger = FakeLedger()
        env, bot = safe_send_harness(ledger)
        sent = env["safe_send"](200, "Hello")
        self.assertEqual(sent.message_id, 1001)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(ledger.acks, [(200, 1001, "text")])

    def test_failed_send_never_claims_api_delivery(self):
        ledger = FakeLedger()
        env, bot = safe_send_harness(ledger)
        bot.fail_all = True
        self.assertIsNone(env["safe_send"](200, "<b>bad</b>"))
        self.assertEqual(ledger.acks, [])

    def test_syntax_fallback_acks_only_successful_retry(self):
        ledger = FakeLedger()
        env, bot = safe_send_harness(ledger)
        bot.fail_first = True
        result = env["safe_send"](200, "<b>Hello</b>")
        self.assertEqual(result.message_id, 1002)
        self.assertEqual(ledger.acks, [(200, 1002, "text")])
        self.assertEqual(bot.sent[1][1], "Hello")

    def test_receipt_storage_failure_never_resends_existing_message(self):
        ledger = FakeLedger(fail=True)
        env, bot = safe_send_harness(ledger)
        sent = env["safe_send"](200, "Hello")
        self.assertEqual(sent.message_id, 1001)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(ledger.acks, [])

    def test_without_opt_in_ledger_never_fakes_receipt(self):
        env, bot = safe_send_harness(None)
        sent = env["safe_send"](200, "Hello")
        self.assertEqual(sent.message_id, 1001)
        self.assertEqual(len(bot.sent), 1)
        self.assertFalse(env["_record_telegram_api_ack"](
            200, SimpleNamespace(message_id=None), "text"
        ))


if __name__ == "__main__":
    unittest.main()
