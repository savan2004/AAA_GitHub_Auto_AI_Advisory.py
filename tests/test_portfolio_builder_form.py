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
        "_builder_allowed": lambda m: m.chat.type == "private" and m.chat.id == m.from_user.id,
        "_builder_form": {},
        "portfolio_doctor_builder_command": lambda m, payload_override=None, *, form_entry=None: submitted.append((payload_override, form_entry)),
    }
    for name in ("_builder_options", "portfolio_builder_button",
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
    def test_complete_manual_input_flow_and_payload(self):
        ns, sent, submitted, answers, msg, click, clock = harness()
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "1"}):
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            self.assertTrue(ns["portfolio_builder_form_text"](msg("1000000"), "1000000"))
            self.assertEqual([b.text for b in sent[-1][2]["reply_markup"].buttons],
                             ["Low", "Mid", "High"])
            ns["portfolio_builder_option"](click("builder|risk|medium"), "risk|medium")
            self.assertEqual([b.text for b in sent[-1][2]["reply_markup"].buttons],
                             ["6 months", "18 months", "36 months", "60 months"])
            ns["portfolio_builder_option"](click("builder|horizon|18"), "horizon|18")
            self.assertTrue(ns["portfolio_builder_form_text"](msg("SBIN 25\nBHEL 20"), "SBIN 25\nBHEL 20"))
            self.assertTrue(ns["portfolio_builder_form_text"](msg("20000 35 45"), "20000 35 45"))
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][0].decode(), "/doctor_builder\n"
            "CAPITAL=1000000 HORIZON=18 RISK=medium CASH=20000 STOCKCAP=35 SECTORCAP=45\nSBIN 25\nBHEL 20")
        self.assertIs(submitted[0][1], ns["_builder_form"][UID])
        self.assertEqual(ns["_builder_form"][UID]["step"], "limits")
        self.assertEqual(len(answers), 2)

    def test_invalid_amount_and_limits_do_not_submit(self):
        ns, sent, submitted, _, msg, click, _ = harness()
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "1"}):
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            ns["portfolio_builder_form_text"](msg("abc"), "abc")
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            ns["portfolio_builder_form_text"](msg("100000"), "100000")
            ns["portfolio_builder_option"](click("builder|risk|low"), "risk|low")
            ns["portfolio_builder_option"](click("builder|horizon|6"), "horizon|6")
            ns["portfolio_builder_form_text"](msg("SBIN 20"), "SBIN 20")
            ns["portfolio_builder_form_text"](msg("200000 35 45"), "200000 35 45")
        self.assertEqual(ns["_builder_form"][UID]["step"], "limits")
        self.assertEqual(submitted, [])

    def test_expired_and_disabled_form(self):
        ns, sent, submitted, _, msg, click, clock = harness()
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "0"}):
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
        self.assertEqual(ns["_builder_form"], {})
        with patch.dict(os.environ, {"MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED": "1"}):
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            clock.t += 181
            self.assertTrue(ns["portfolio_builder_form_text"](msg("100000"), "100000"))
        self.assertEqual(ns["_builder_form"], {})
        self.assertEqual(submitted, [])


if __name__ == "__main__":
    unittest.main()
