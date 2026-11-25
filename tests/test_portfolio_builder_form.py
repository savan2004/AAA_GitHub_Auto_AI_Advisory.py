"""Offline acceptance tests for the interactive, temporary Portfolio Builder form."""
import ast
import copy
import logging
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
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


def harness():
    sent, submitted, answers = [], [], []
    class Clock:
        t = 100.
        @staticmethod
        def monotonic():
            return Clock.t
    ns = {
        "types": Types, "os": os, "re": re, "time": Clock,
        "state": SimpleNamespace(clear=lambda uid: None),
        "logger": logging.getLogger("wizard-test"),
        "bot": SimpleNamespace(answer_callback_query=lambda call_id: answers.append(call_id)),
        "safe_send": lambda uid, msg, **kw: sent.append((uid, msg, kw)),
        "_portfolio_doctor_lock": threading.Lock(),
        "_portfolio_doctor_running": set(),
        "_portfolio_doctor_waiting": {},
        "_portfolio_audit_mode": {},
        "_builder_form": {},
        "portfolio_doctor_builder_command": lambda m, payload_override=None, *, form_entry=None: submitted.append((payload_override, form_entry)),
    }
    for name in ("_builder_allowed", "_builder_options", "portfolio_builder_button",
                 "portfolio_builder_option", "portfolio_builder_form_text"):
        node = next(x for x in TREE.body if isinstance(x, ast.FunctionDef) and x.name == name)
        node = copy.deepcopy(node)
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "<wizard>", "exec"), ns)
    msg = lambda text: SimpleNamespace(text=text,
                                       chat=SimpleNamespace(id=UID, type="private"),
                                       from_user=SimpleNamespace(id=UID))
    click = lambda data: SimpleNamespace(id="cb", data=data,
                                        message=SimpleNamespace(chat=SimpleNamespace(id=UID, type="private")),
                                        from_user=SimpleNamespace(id=UID))
    return ns, sent, submitted, answers, msg, click, Clock


class BuilderFormTests(unittest.TestCase):
    def test_builder_guard_rejects_missing_and_mismatched_chat_safely(self):
        ns, sent, submitted, answers, msg, click, clock = harness()
        self.assertTrue(ns["_builder_allowed"](msg("hi")))
        self.assertFalse(ns["_builder_allowed"](SimpleNamespace(chat=None,
            from_user=SimpleNamespace(id=UID))))
        self.assertFalse(ns["_builder_allowed"](SimpleNamespace(chat=SimpleNamespace(
            id=UID, type="group"), from_user=SimpleNamespace(id=UID))))
        self.assertFalse(ns["_builder_allowed"](SimpleNamespace(chat=SimpleNamespace(
            id=UID, type="private"), from_user=SimpleNamespace(id=UID+1))))
        self.assertFalse(ns["_builder_allowed"](SimpleNamespace(chat=SimpleNamespace(
            id="bad", type="private"), from_user=SimpleNamespace(id=UID))))

    def test_public_builder_never_opens_manual_steps(self):
        for flag in ("0", "1"):
            ns, sent, submitted, _, msg, _, clock = harness()
            ns["_builder_form"][UID] = {"step": "stocks", "until": clock.t + 180}
            ns["_portfolio_doctor_waiting"][UID] = clock.t + 180
            ns["_portfolio_audit_mode"][UID] = clock.t + 180
            with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": flag}):
                ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            self.assertEqual(ns["_builder_form"], {})
            self.assertEqual(ns["_portfolio_doctor_waiting"], {})
            self.assertEqual(ns["_portfolio_audit_mode"], {})
            self.assertIn("verified, approved", sent[-1][1])
            self.assertFalse(any("Step 4" in x[1] for x in sent))
            self.assertFalse(submitted)

    def test_all_old_keyboard_steps_fail_closed(self):
        with patch.dict(os.environ, {
                "MSK_BUILDER_V2_PREVIEW_ENABLED": "1",
                "MSK_BUILDER_V2_PREVIEW_USER_ID": str(UID),
                "MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "1"}):
            for step, choice in (("amount", "risk|low"), ("risk", "risk|medium"),
                                 ("horizon", "horizon|6"), ("stocks", "horizon|36"),
                                 ("limits", "risk|high")):
                with self.subTest(step=step):
                    ns, sent, submitted, ack, _, click, clock = harness()
                    ns["_builder_form"][UID] = {"step": step, "until": clock.t + 180}
                    ns["portfolio_builder_option"](click("builder|" + choice), choice)
                    self.assertFalse(ns["_builder_form"])
                    self.assertIn("old Builder button", sent[-1][1])
                    self.assertEqual(len(ack), 1)
                    self.assertFalse(submitted)

    def test_manual_stale_text_is_consumed_without_allocation(self):
        for step, text in (("amount", "1000000"), ("risk", "low"),
                           ("horizon", "36"), ("stocks", "SBIN 25\nBHEL 20"),
                           ("limits", "20000 35 45")):
            with self.subTest(step=step):
                ns, sent, submitted, _, msg, _, clock = harness()
                ns["_builder_form"][UID] = {"step": step, "until": clock.t + 180}
                self.assertTrue(ns["portfolio_builder_form_text"](msg(text), text))
                self.assertFalse(ns["_builder_form"])
                self.assertIn("retired", sent[-1][1].lower())
                self.assertFalse(submitted)

    def test_owner_preview_3_steps_all_risks_and_horizons(self):
        with patch.dict(os.environ, {"MSK_BUILDER_V2_PREVIEW_ENABLED": "1",
                                     "MSK_BUILDER_V2_PREVIEW_USER_ID": str(UID)}):
            for risk in ("low", "medium", "high"):
                for horizon in ("6", "18", "36", "60"):
                    with self.subTest(risk=risk, horizon=horizon):
                        ns, sent, submitted, ack, msg, click, clock = harness()
                        completed = []
                        ns["send_private_builder_beta"] = lambda uid, entry, h: completed.append(
                            (uid, entry["amount"], entry["risk"], h))
                        ns["_builder_form"][UID] = {"mode": "private_beta", "step": "amount",
                                                    "until": clock.t + 180}
                        self.assertTrue(ns["portfolio_builder_form_text"](msg("1000000"), "1000000"))
                        self.assertIn("Step 2/3", sent[-1][1])
                        self.assertEqual([b.text for b in sent[-1][2]["reply_markup"].buttons],
                                         ["Low", "Medium", "High"])
                        ns["portfolio_builder_option"](click("builder|risk|" + risk), "risk|" + risk)
                        self.assertIn("Step 3/3", sent[-1][1])
                        self.assertEqual([b.text for b in sent[-1][2]["reply_markup"].buttons],
                                         ["6 months", "18 months", "36 months", "60 months"])
                        ns["portfolio_builder_option"](click("builder|horizon|" + horizon),
                                                       "horizon|" + horizon)
                        self.assertEqual(completed, [(UID, "1000000", risk, horizon)])
                        self.assertEqual(len(ack), 2)
                        self.assertFalse(submitted)

    def test_owner_preview_gate_and_invalid_amount(self):
        ns, sent, submitted, _, msg, click, clock = harness()
        ns["_builder_form"][UID] = {"mode": "private_beta", "step": "amount",
                                    "until": clock.t + 180}
        with patch.dict(os.environ, {"MSK_BUILDER_V2_PREVIEW_ENABLED": "0"}):
            self.assertTrue(ns["portfolio_builder_form_text"](msg("1000000"), "1000000"))
        self.assertFalse(ns["_builder_form"])
        self.assertIn("unavailable", sent[-1][1])
        with patch.dict(os.environ, {"MSK_BUILDER_V2_PREVIEW_ENABLED": "1",
                                     "MSK_BUILDER_V2_PREVIEW_USER_ID": str(UID)}):
            ns["_builder_form"][UID] = {"mode": "private_beta", "step": "amount",
                                        "until": clock.t + 180}
            ns["portfolio_builder_form_text"](msg("15000001"), "15000001")
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            ns["portfolio_builder_form_text"](msg("15000000"), "15000000")
            ns["portfolio_builder_option"](click("builder|risk|extreme"), "risk|extreme")
            self.assertEqual(ns["_builder_form"][UID]["step"], "risk")
            ns["portfolio_builder_option"](click("builder|risk|low"), "risk|low")
            ns["portfolio_builder_option"](click("builder|horizon|72"), "horizon|72")
            self.assertEqual(ns["_builder_form"][UID]["step"], "horizon")
        self.assertFalse(submitted)

    def test_expired_legacy_form_is_consumed(self):
        ns, sent, submitted, _, msg, _, clock = harness()
        ns["_builder_form"][UID] = {"step": "limits", "until": clock.t - 1}
        self.assertTrue(ns["portfolio_builder_form_text"](msg("20000 35 45"), "20000 35 45"))
        self.assertFalse(ns["_builder_form"])
        self.assertFalse(submitted)

    def test_old_worker_is_not_referenced_by_active_telegram_handlers(self):
        selected = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}
        for name in ("portfolio_builder_option", "portfolio_builder_form_text",
                     "portfolio_doctor_builder_command"):
            body = ast.get_source_segment(SOURCE, selected[name])
            for removed in ("parse_builder_request", "run_isolated_builder", "Step 4/5",
                            "Step 5/5", "STOCKCAP="):
                self.assertNotIn(removed, body)


if __name__ == "__main__":
    unittest.main()
