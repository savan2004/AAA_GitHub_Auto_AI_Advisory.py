"""Private Portfolio Audit canary: real code, synthetic-only offline evidence."""
import ast
import copy
import io
import logging
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfReader

import portfolio_doctor_live as doctor
from portfolio_audit_report import IST, SyntheticProvider
from portfolio_audit_bridge import build_verified_audit


NOW = datetime(2026, 11, 19, 10, 0, tzinfo=IST)
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _function(name, env):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == name)
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[node], type_ignores=[])), str(MAIN), "exec"), env)
    return env[name]


def _message(uid):
    return SimpleNamespace(
        chat=SimpleNamespace(id=uid, type="private"),
        from_user=SimpleNamespace(id=uid),
        text="/audit")


class PortfolioAuditCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = SyntheticProvider()
        cls.holdings = [
            {"symbol": "FCL", "qty": 40000, "buy": 15},
            {"symbol": "SCI", "qty": 150, "buy": 140.5},
            {"symbol": "DLF", "qty": 50, "buy": 458},
        ]
        cls.frames = {h["symbol"]:
                      cls.provider.stock_history(h["symbol"]).frame
                      for h in cls.holdings}
        cls.recs = [
            {"symbol": h["symbol"], "date": "2026-11-18",
             "close": float(cls.frames[h["symbol"]]["Close"].iloc[-1]),
             "price_source": "NSE official daily file"}
            for h in cls.holdings
        ]
        # A real Telegram account never receives synthetic data. The fixture
        # feeds a hard-coded offline source into the same validator.
        with patch("market_data.bulk_history", return_value=cls.frames), \
             patch("market_data.index_history",
                   side_effect=lambda symbol, *a, **kw:
                   cls.provider.index_history(symbol).frame), \
             patch("portfolio_doctor_peers.official_nifty500_industries",
                   return_value={}):
            cls.report = build_verified_audit(
                cls.holdings, cls.recs, cls.frames, now=NOW,
                extras=cls.provider, budget_seconds=58)

    def test_12_pages_and_no_synthetic_demo_in_production_report(self):
        pdf = PdfReader(io.BytesIO(self.report), strict=True)
        self.assertEqual(len(pdf.pages), 12)
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("PORTFOLIO DASHBOARD", text)
        self.assertIn("AUD-", text)
        self.assertIn("FCL", text)
        self.assertIn("SCI", text)
        self.assertIn("DLF", text)
        self.assertNotIn("SYNTHETIC DEMO", text)
        self.assertNotIn("health score", text.lower())

    def test_strict_parent_accepts_audit_only_in_new_release_gate(self):
        result = SimpleNamespace(returncode=0, stdout=self.report, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=result):
            self.assertEqual(doctor.run_isolated_memory(
                "typed.csv", b"FCL 40000 15", timeout=150, audit=True), self.report)
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory(
                    "typed.csv", b"FCL 40000 15", timeout=150)
            self.assertEqual(err.exception.code, "PDF_FAILED")

    def test_poor_price_crosscheck_refuses_generation(self):
        recs = [dict(x) for x in self.recs]
        recs[0]["close"] += 1
        with self.assertRaises(doctor.PortfolioDoctorError) as err:
            build_verified_audit(self.holdings, recs, self.frames, now=NOW,
                                 extras=self.provider, budget_seconds=1)
        self.assertEqual(err.exception.code, "STALE_PRICES")

    def test_audit_command_keeps_only_expiring_private_mode(self):
        notices = []
        env = {
            "threading": threading, "time": time,
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_running": set(),
            "_portfolio_doctor_waiting": {},
            "_portfolio_audit_mode": {},
            "_doctor_allowed": lambda m: m.chat.type == "private",
            "safe_send": lambda uid, msg: notices.append((uid, msg)),
        }
        start = _function("portfolio_audit_start", env)
        start(_message(101))
        self.assertTrue(env["_portfolio_doctor_waiting"][101] > time.monotonic())
        self.assertEqual(env["_portfolio_audit_mode"][101],
                         env["_portfolio_doctor_waiting"][101])
        self.assertIn("10-14 page", notices[-1][1])
        self.assertNotIn("FCL", str(env["_portfolio_audit_mode"]))

    def test_telegram_handoff_requests_isolated_audit_only_after_explicit_optin(self):
        class FakeMarkup:
            def __init__(self, row_width=2): self.buttons = []
            def add(self, *buttons): self.buttons.extend(buttons)

        class FakeBot:
            def __init__(self): self.received = []
            def send_document(self, uid, handle, **kw):
                self.received.append((uid, handle.name, handle.read(), kw))
                return SimpleNamespace(
                    message_id=1234, document=SimpleNamespace(file_id="telegram-file"))

        bot = FakeBot()
        notices = []
        env = {
            "threading": threading, "time": time,
            "_portfolio_doctor_lock": threading.Lock(),
            "_portfolio_doctor_running": set(),
            "_portfolio_doctor_waiting": {101: time.monotonic()+180},
            "_portfolio_audit_mode": {101: time.monotonic()+180},
            "_portfolio_doctor_slots": threading.BoundedSemaphore(2),
            "_doctor_allowed": lambda m: True,
            "safe_send": lambda uid, msg: notices.append((uid,msg)),
            "bot": bot, "executor": SimpleNamespace(submit=lambda fn: fn()),
            "types": SimpleNamespace(
                InlineKeyboardMarkup=FakeMarkup,
                InlineKeyboardButton=lambda name, callback_data:
                    SimpleNamespace(text=name, callback_data=callback_data)),
            "logger": logging.getLogger("audit-fixture"),
        }
        handler = _function("portfolio_doctor_document", env)
        with patch.object(doctor, "run_isolated_memory",
                          return_value=self.report) as runner:
            handler(_message(101), _typed_payload=b"FCL 40000 15")
        runner.assert_called_once_with(
            "typed_holdings.csv", b"FCL 40000 15", timeout=150, audit=True)
        self.assertEqual(len(bot.received), 1)
        self.assertEqual(bot.received[0][1], "MSK_Portfolio_Audit.pdf")
        self.assertTrue(bot.received[0][2].startswith(b"%PDF-"))
        self.assertEqual(bot.received[0][3]["protect_content"], True)
        self.assertEqual(env["_portfolio_audit_mode"], {})
        self.assertEqual(env["_portfolio_doctor_running"], set())


if __name__ == "__main__":
    unittest.main()
