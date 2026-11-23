"""Fail-closed customer Builder wizard: no market/network/Telegram side effects.

Fictional profile values validate UI and idempotent callback mechanics ONLY.
Issuer approval is independently gated by msk_ai_builder_research_release.
"""
import ast
import copy
import logging
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

MAIN = Path(__file__).resolve().parents[1] / "main.py"
TREE = ast.parse(MAIN.read_text(encoding="utf-8"))
UID = 101


class Keyboard:
    def __init__(self, row_width=1):
        self.buttons = []
    def add(self, *buttons):
        self.buttons.extend(buttons)


class Types:
    InlineKeyboardMarkup = Keyboard
    @staticmethod
    def InlineKeyboardButton(label, callback_data):
        return SimpleNamespace(text=label, callback_data=callback_data)


class Clock:
    t = 100
    @staticmethod
    def monotonic():
        return Clock.t


def harness():
    messages, acknowledgements = [], []
    ns = {
        "types": Types, "os": os, "re": re, "time": Clock,
        "state": SimpleNamespace(clear=lambda _: None),
        "logger": logging.getLogger("msk-builder-offline-qa"),
        "bot": SimpleNamespace(
            answer_callback_query=lambda callback_id: acknowledgements.append(callback_id)),
        "safe_send": lambda uid, message, **kw: messages.append((uid, message, kw)),
        "_portfolio_doctor_lock": threading.RLock(),
        "_portfolio_doctor_running": set(),
        "_portfolio_doctor_waiting": {},
        "_portfolio_audit_mode": {},
        "_portfolio_doctor_slots": threading.BoundedSemaphore(2),
        "_builder_form": {},
        "executor": SimpleNamespace(submit=lambda fn: fn()),
        "send_private_builder_beta": lambda *args: None,
    }
    for name in ("_builder_allowed", "_builder_options", "portfolio_builder_button",
                 "send_automatic_builder", "portfolio_builder_option",
                 "portfolio_builder_form_text"):
        node = next(x for x in TREE.body
                    if isinstance(x, ast.FunctionDef) and x.name == name)
        node = copy.deepcopy(node)
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=[node], type_ignores=[])), str(MAIN), "exec"), ns)
    def message(text, uid=UID, chat_type="private"):
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=uid, type=chat_type),
                               from_user=SimpleNamespace(id=uid))
    def click(payload, uid=UID):
        return SimpleNamespace(id="ack", data=payload,
                               message=SimpleNamespace(chat=SimpleNamespace(
                                   id=uid, type="private")),
                               from_user=SimpleNamespace(id=uid))
    return ns, messages, acknowledgements, message, click


def walk_form(ns, message, click, *, amount="1000000", risk="medium", horizon="36"):
    uid = UID
    ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
    session = ns["_builder_form"][uid]
    token = session["token"]
    assert ns["portfolio_builder_form_text"](message(amount), amount)
    ns["portfolio_builder_option"](click("builder|risk|" + risk + "|" + token),
                                    "risk|" + risk + "|" + token)
    ns["portfolio_builder_option"](click("builder|horizon|" + horizon + "|" + token),
                                    "horizon|" + horizon + "|" + token)
    return token


class CustomerBuilderWorkflow(unittest.TestCase):
    def test_without_proven_issuer_release_button_is_closed_even_if_feature_flag_enabled(self):
        ns, notices, _, message, _ = harness()
        with patch.dict(os.environ, {"MSK_BUILDER_V2_ENABLED": "1"}, clear=False):
            ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
        self.assertEqual(ns["_builder_form"], {})
        self.assertIn("verified, approved", notices[-1][1])
        self.assertNotIn("Step 1/3", notices[-1][1])

    def test_complete_three_input_customer_flow_and_repeated_callback_sends_only_once(self):
        delivered = Mock(return_value=SimpleNamespace(message_id=888, document=SimpleNamespace(file_id="X")))
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf", delivered):
            ns, messages, answers, message, click = harness()
            token = walk_form(ns, message, click)
            self.assertRegex(token, r"^[0-9a-f]{12}$")
            self.assertEqual(delivered.call_count, 1)
            args = delivered.call_args.args
            self.assertEqual(args[1:], (UID, 1_000_000, "medium", 36))
            self.assertFalse(ns["_builder_form"])
            self.assertFalse(ns["_portfolio_doctor_running"])
            self.assertTrue(all("stock symbols" not in m[1].lower() or
                                "never" in m[1].lower()
                                for m in messages))
            old = "horizon|36|" + token
            ns["portfolio_builder_option"](click("builder|" + old), old)
            self.assertEqual(delivered.call_count, 1)
            self.assertEqual(len(answers), 3)

    def test_stale_buttons_cannot_mutate_new_session_or_trigger_second_pdf(self):
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True):
            ns, messages, _, message, click = harness()
            ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
            token = ns["_builder_form"][UID]["token"]
            ns["portfolio_builder_form_text"](message("5000000"), "5000000")
            stale = "risk|high|deadbeef1234"
            ns["portfolio_builder_option"](click("builder|" + stale), stale)
            self.assertEqual(ns["_builder_form"][UID]["step"], "risk")
            self.assertEqual(ns["_builder_form"][UID]["token"], token)
            self.assertIn("earlier session", messages[-1][1])
            ns["portfolio_builder_option"](click("builder|risk|high"),
                                          "risk|high")
            self.assertEqual(ns["_builder_form"][UID]["step"], "risk")
            ns["portfolio_builder_option"](click("builder|risk|high|" + token),
                                          "risk|high|" + token)
            self.assertEqual(ns["_builder_form"][UID]["step"], "horizon")

    def test_each_tier_and_risk_horizon_passed_without_customer_symbols(self):
        delivered = Mock(return_value=SimpleNamespace(message_id=777))
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf", delivered):
            for capital, count in (("1000000", 5), ("5000000", 10), ("10000000", 20)):
                for risk in ("low", "medium", "high"):
                    for months in ("6", "18", "36", "60"):
                        with self.subTest(capital=capital, risk=risk, months=months):
                            ns, msgs, _, message, click = harness()
                            walk_form(ns, message, click, amount=capital,
                                      risk=risk, horizon=months)
                            self.assertEqual(delivered.call_args.args[1:],
                                             (UID, int(capital), risk, int(months)))
                            self.assertFalse(ns["_builder_form"])
                            self.assertFalse(any("Enter stock" in z[1] or
                                                 "Enter allocation" in z[1] for z in msgs))
        self.assertEqual(delivered.call_count, 36)

    def test_unknown_send_receipt_not_retried_and_capacity_released(self):
        delivered = Mock(return_value=SimpleNamespace(message_id=None))
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf", delivered):
            ns, messages, _, message, click = harness()
            walk_form(ns, message, click)
            self.assertEqual(delivered.call_count, 1)
            self.assertFalse(ns["_portfolio_doctor_running"])
            self.assertIn("not retry", messages[-1][1])
            self.assertTrue(ns["_portfolio_doctor_slots"].acquire(blocking=False))
            ns["_portfolio_doctor_slots"].release()

    def test_expired_or_revoked_research_refuses_pending_customer_pdf(self):
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True):
            ns, messages, _, message, click = harness()
            ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
            token = ns["_builder_form"][UID]["token"]
            ns["portfolio_builder_form_text"](message("1000000"), "1000000")
            ns["portfolio_builder_option"](click("builder|risk|medium|" + token),
                                          "risk|medium|" + token)
            with patch("msk_ai_builder_automatic.automatic_available", return_value=False):
                ns["portfolio_builder_option"](click("builder|horizon|36|" + token),
                                              "horizon|36|" + token)
            self.assertFalse(ns["_builder_form"])
            self.assertIn("no longer current", messages[-1][1])
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True):
            ns, messages, _, message, _ = harness()
            ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
            Clock.t += 301
            try:
                self.assertTrue(ns["portfolio_builder_form_text"](message("1000000"), "1000000"))
                self.assertFalse(ns["_builder_form"])
            finally:
                Clock.t = 100

    def test_invalid_amount_and_no_legacy_stock_weight_input(self):
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True):
            ns, messages, _, message, _ = harness()
            ns["portfolio_builder_button"](message("🏗️ Portfolio Builder"))
            for value in ("SBIN 40\nBHEL 60", "120%", "15000001", "0",
                          "CAPITAL=1000000", "1e6"):
                self.assertTrue(ns["portfolio_builder_form_text"](message(value), value))
                self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            self.assertEqual(ns["_builder_form"][UID]["mode"], "automatic")
            ns["portfolio_builder_form_text"](message("/cancel"), "/cancel")
            self.assertFalse(ns["_builder_form"])


if __name__ == "__main__":
    unittest.main()
