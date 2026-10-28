"""Offline execution of the real three Telegram screener handlers from main.py.

Synthetic fixtures only. No tokens, HTTP requests, Telegram sends, or trading.
"""
import ast
from datetime import datetime, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest

from model_portfolios import select_model, format_model

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp()


class Button:
    def __init__(self, label, callback_data):
        self.label = label
        self.callback_data = callback_data


class Markup:
    def __init__(self, row_width):
        self.row_width = row_width
        self.buttons = []

    def add(self, *buttons):
        self.buttons.extend(buttons)


class ImmediateExecutor:
    def submit(self, function):
        function()


def candidate(index):
    return {
        "symbol": f"TEST{index:02d}", "name": f"Synthetic stock {index}",
        "sector": f"Industry {index // 2}",
        "asof": "26-Oct-2026", "provider": "Mock verified provider",
        "provider_url": "https://www.nseindia.com/research/mock",
        "fiscal_sales_period": "2026-03",
        "fiscal_sales_previous": "2025-03",
        "balance_sheet_period": "2026-09",
        "fetched_at": NOW - 60,
        "roe": 22., "growth": 12., "debt": .25, "cap": 50000.,
        "pe": 22., "vol": 19., "turnover": 25., "price": 300.,
        "timing": "Constructive",
    }


def make_handler(builder, captured):
    """Run production AST function, not a reimplemented button router."""
    text = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf8")
    tree = ast.parse(text)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "scan_btn")
    fn.decorator_list = []
    code = compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec")
    env = {
        "safe_send": lambda chat_id, message, reply_markup=None: captured.append(
            (chat_id, message, reply_markup)),
        "build_scan": builder,
        "executor": ImmediateExecutor(),
        "logger": logging.getLogger(__name__),
        "types": SimpleNamespace(InlineKeyboardMarkup=Markup,
                                 InlineKeyboardButton=Button),
    }
    exec(code, env)
    return env["scan_btn"]


class ActualTelegramHandlerDeliveryTests(unittest.TestCase):
    def test_all_buttons_deliver_six_fields_and_ten_callback_symbols(self):
        for label, profile in (
            ("🏦 Conservative", "conservative"),
            ("⚖️ Moderate", "moderate"),
            ("🚀 Aggressive", "aggressive"),
        ):
            with self.subTest(profile=profile):
                result = select_model([candidate(i) for i in range(10)],
                                      profile, now=NOW)
                messages, symbols = format_model(result, {"checked": 10,
                                                            "universe": 300})
                captured = []
                handler = make_handler(lambda _: (messages, symbols), captured)
                handler(SimpleNamespace(chat=SimpleNamespace(id=1234), text=label))
                self.assertEqual(len(captured), 5)  # progress + 4 chunks
                self.assertEqual([m[1] for m in captured[1:]], messages)
                for _chat_id, message, markup in captured:
                    self.assertEqual(_chat_id, 1234)
                    self.assertLessEqual(len(message), 4096)
                keyboard = captured[-1][2]
                self.assertEqual(len(keyboard.buttons), 10)
                self.assertEqual([b.callback_data for b in keyboard.buttons],
                                 [f"menu|{s}" for s in symbols])
                self.assertTrue(all(m[2] is None for m in captured[:-1]))
                rendered = "\n".join(x[1] for x in captured[2:4])
                for field in ("Target ₹", "SL ₹", "Allocation ₹",
                              "Timeframe ", "Reference close ₹"):
                    self.assertEqual(rendered.count(field), 10)

    def test_unavailable_financial_evidence_sends_reason_no_buttons(self):
        captured = []
        handler = make_handler(lambda _: (["No approved financial statement data"], []),
                               captured)
        handler(SimpleNamespace(chat=SimpleNamespace(id=1234),
                                text="⚖️ Moderate"))
        self.assertEqual(len(captured), 2)
        self.assertIn("No approved financial statement", captured[1][1])
        self.assertIsNone(captured[1][2])


if __name__ == "__main__":
    unittest.main()
