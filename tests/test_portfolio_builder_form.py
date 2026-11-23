"""Regression tests for the retired customer Builder and owner-only fictional preview."""
import ast
import copy
import logging
import os
from pathlib import Path
import re
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
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
    t = 100.0
    @classmethod
    def monotonic(cls):
        return cls.t


def _function(name, env):
    node = next(x for x in TREE.body if isinstance(x, ast.FunctionDef) and x.name == name)
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "<builder-regression>", "exec"), env)
    return env[name]


def harness():
    sent, answers, delivered = [], [], []
    ns = {
        "types": Types, "os": os, "re": re, "time": Clock,
        "state": SimpleNamespace(clear=lambda uid: None),
        "logger": logging.getLogger("builder-regression"),
        "bot": SimpleNamespace(answer_callback_query=lambda call_id: answers.append(call_id)),
        "safe_send": lambda uid, msg, **kw: sent.append((uid, msg, kw)),
        "_portfolio_doctor_lock": threading.Lock(),
        "_portfolio_doctor_running": set(),
        "_portfolio_doctor_waiting": {},
        "_portfolio_audit_mode": {},
        "_portfolio_doctor_slots": threading.BoundedSemaphore(1),
        "executor": SimpleNamespace(submit=lambda fn: delivered.append("submitted")),
        "_builder_allowed": lambda m: m.chat.type == "private" and m.chat.id == m.from_user.id,
        "_builder_form": {},
        "send_private_builder_beta": lambda uid, entry, horizon: delivered.append(
            (uid, entry.get("risk"), entry.get("amount"), horizon)),
    }
    names = (
        "_builder_options", "portfolio_builder_button", "portfolio_builder_private_beta",
        "portfolio_builder_option", "portfolio_builder_form_text",
        "portfolio_doctor_builder_command", "_looks_like_retired_builder_allocations",
        "handle_text",
    )
    for name in names:
        _function(name, ns)
    msg = lambda text: SimpleNamespace(
        text=text, chat=SimpleNamespace(id=UID, type="private"),
        from_user=SimpleNamespace(id=UID))
    click = lambda value: SimpleNamespace(
        id="callback", data="builder|" + value,
        message=SimpleNamespace(chat=SimpleNamespace(id=UID, type="private")),
        from_user=SimpleNamespace(id=UID))
    return ns, sent, answers, delivered, msg, click


def preview_module():
    module = ModuleType("msk_ai_builder_telegram")
    module.preview_allowed = lambda uid: uid == UID
    module.deliver_preview = lambda *args, **kwargs: SimpleNamespace(message_id=9001)
    return module


class BuilderRetirementTests(unittest.TestCase):
    def setUp(self):
        Clock.t = 100.0

    def test_customer_button_stays_closed_and_cancels_stale_sessions(self):
        ns, sent, _, delivered, msg, _ = harness()
        ns["_builder_form"][UID] = {"step": "stocks", "until": Clock.t + 180}
        ns["_portfolio_doctor_waiting"][UID] = Clock.t + 180
        ns["_portfolio_audit_mode"][UID] = Clock.t + 180
        ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
        self.assertEqual(ns["_builder_form"], {})
        self.assertEqual(ns["_portfolio_doctor_waiting"], {})
        self.assertEqual(ns["_portfolio_audit_mode"], {})
        self.assertIn("verified, approved", sent[-1][1])
        self.assertNotIn("Step 4", sent[-1][1])
        self.assertEqual(delivered, [])

    def test_owner_preview_remains_three_steps_and_clears_doctor_sessions(self):
        ns, sent, _, _, msg, _ = harness()
        ns["_portfolio_doctor_waiting"][UID] = Clock.t + 180
        ns["_portfolio_audit_mode"][UID] = Clock.t + 180
        with patch.dict(sys.modules, {"msk_ai_builder_telegram": preview_module()}):
            ns["portfolio_builder_private_beta"](msg("/builder_v2_preview"))
            self.assertEqual(ns["_portfolio_doctor_waiting"], {})
            self.assertEqual(ns["_portfolio_audit_mode"], {})
            self.assertEqual(ns["_builder_form"][UID]["mode"], "private_beta")
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            self.assertIn("Step 1/3", sent[-1][1])

            self.assertTrue(ns["portfolio_builder_form_text"](msg("1000000"), "1000000"))
            self.assertEqual(ns["_builder_form"][UID]["step"], "risk")
            self.assertIn("Step 2/3", sent[-1][1])
            self.assertEqual([b.text for b in sent[-1][2]["reply_markup"].buttons],
                             ["Low", "Mid", "High"])

    def test_all_risk_choices_and_all_four_horizons_reach_only_fictional_preview(self):
        ns, sent, answers, delivered, msg, click = harness()
        for risk in ("low", "medium", "high"):
            for horizon in ("6", "18", "36", "60"):
                ns["_builder_form"][UID] = {
                    "mode": "private_beta", "amount": "1000000", "step": "risk",
                    "until": Clock.t + 180,
                }
                ns["portfolio_builder_option"](click("risk|" + risk), "risk|" + risk)
                self.assertEqual(ns["_builder_form"][UID]["risk"], risk)
                self.assertEqual(ns["_builder_form"][UID]["step"], "horizon")
                self.assertIn("Step 3/3", sent[-1][1])
                self.assertEqual([b.callback_data for b in sent[-1][2]["reply_markup"].buttons],
                                 ["builder|horizon|6", "builder|horizon|18",
                                  "builder|horizon|36", "builder|horizon|60"])

                ns["portfolio_builder_option"](click("horizon|" + horizon),
                                               "horizon|" + horizon)
                self.assertEqual(delivered[-1], (UID, risk, "1000000", horizon))
                self.assertNotIn("stocks", ns["_builder_form"][UID])
                self.assertNotIn("limits", ns["_builder_form"][UID])
        self.assertEqual(len(answers), 24)

    def test_old_callbacks_cannot_restore_manual_stocks_step(self):
        ns, sent, _, delivered, _, click = harness()
        ns["_builder_form"][UID] = {"step": "horizon", "until": Clock.t + 180}
        ns["portfolio_builder_option"](click("horizon|18"), "horizon|18")
        self.assertEqual(ns["_builder_form"], {})
        self.assertIn("old Builder option", sent[-1][1])
        self.assertEqual(delivered, [])
        self.assertFalse(any("Step 4" in item[1] for item in sent))

    def test_old_manual_text_states_are_retired_not_parsed(self):
        for old_step, text in (("stocks", "SBIN 25\nBHEL 20"),
                               ("limits", "20000 35 45")):
            ns, sent, _, delivered, msg, _ = harness()
            ns["_builder_form"][UID] = {"step": old_step, "until": Clock.t + 180}
            self.assertTrue(ns["portfolio_builder_form_text"](msg(text), text))
            self.assertEqual(ns["_builder_form"], {})
            self.assertIn("old manual Builder form", sent[-1][1])
            self.assertEqual(delivered, [])

    def test_doctor_builder_command_is_retired(self):
        ns, sent, _, delivered, msg, _ = harness()
        ns["_builder_form"][UID] = {"step": "stocks", "until": Clock.t + 180}
        ns["portfolio_doctor_builder_command"](msg("/doctor_builder"))
        self.assertEqual(ns["_builder_form"], {})
        self.assertIn("retired", sent[-1][1])
        self.assertNotIn("SBIN", sent[-1][1])
        self.assertEqual(delivered, [])

    def test_allocation_and_capital_text_never_fall_through_to_stock_search(self):
        ns, sent, _, delivered, msg, _ = harness()
        self.assertTrue(ns["_looks_like_retired_builder_allocations"]("SBIN 25\nBHEL 20"))
        self.assertTrue(ns["_looks_like_retired_builder_allocations"]("SBIN 25"))
        self.assertFalse(ns["_looks_like_retired_builder_allocations"]("SBIN"))
        ns["API_RATE_LIMITER"] = SimpleNamespace(is_allowed=lambda uid: True)
        ns["RATE_LIMIT_WINDOW"] = 30
        ns["logger"] = logging.getLogger("builder-routing")
        ns["portfolio_builder_form_text"] = lambda m, value: False
        ns["_doctor_allowed"] = lambda m: False
        ns["state"] = SimpleNamespace(get=lambda uid: None, clear=lambda uid: None)
        with patch.dict(sys.modules, {
            "portfolio_doctor_live": SimpleNamespace(looks_like_holdings_text=lambda value: False)
        }):
            ns["handle_text"](msg("SBIN 25\nBHEL 20"))
        self.assertIn("old manual Builder format", sent[-1][1])
        self.assertNotIn("submitted", delivered)

        ns2, sent2, _, _, msg2, _ = harness()
        ns2["API_RATE_LIMITER"] = SimpleNamespace(is_allowed=lambda uid: True)
        ns2["RATE_LIMIT_WINDOW"] = 30
        ns2["logger"] = logging.getLogger("builder-capital-routing")
        ns2["portfolio_builder_form_text"] = lambda m, value: False
        ns2["_doctor_allowed"] = lambda m: False
        ns2["state"] = SimpleNamespace(get=lambda uid: None, clear=lambda uid: None)
        with patch.dict(sys.modules, {
            "portfolio_doctor_live": SimpleNamespace(looks_like_holdings_text=lambda value: False)
        }):
            ns2["handle_text"](msg2("CAPITAL=100000 HORIZON=36 RISK=medium"))
        self.assertIn("automatic Builder is not yet available", sent2[-1][1])
        self.assertIn("No stock search was started.", sent2[-1][1])

    def test_production_source_contains_no_customer_step_four_or_manual_worker(self):
        self.assertNotIn("Step 4/5 — Type 1–5 symbols", SOURCE)
        self.assertNotIn("Step 5/5 — Set your limits", SOURCE)
        self.assertNotIn("run_isolated_builder(payload", SOURCE)
        self.assertIn('@bot.message_handler(commands=["doctor_builder"])', SOURCE)


if __name__ == "__main__":
    unittest.main()
