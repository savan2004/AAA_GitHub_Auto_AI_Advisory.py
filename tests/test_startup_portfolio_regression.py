"""Regression checks for webhook configuration and portfolio deadlocks.

Runs without importing main.py, which needs live Telegram credentials.
"""
import ast
import json
import logging
import math
import os
import re
import html
from pathlib import Path
import tempfile
import threading
import unittest

MAIN_FILE = Path(__file__).resolve().parents[1] / "main.py"


def load_portfolio_manager():
    source = MAIN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
    namespace = {"os": os, "json": json, "math": math, "re": re, "threading": threading, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MAIN_FILE), "exec"), namespace)
    return namespace["PortfolioManager"]


class PortfolioRegressionTests(unittest.TestCase):
    def test_remove_does_not_deadlock_and_persists(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "positions.json")
            manager = cls(path)
            manager.add(100, "SBIN", 2, 500)
            result = []
            worker = threading.Thread(target=lambda: result.append(manager.remove(100, "SBIN")), daemon=True)
            worker.start()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "remove() deadlocked on the portfolio lock")
            self.assertEqual(result, [True])
            self.assertEqual(cls(path).get(100), {})

    def test_missing_position_returns_false(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(cls(str(Path(folder) / "positions.json")).remove(1, "MISSING"))


    def test_storage_write_error_does_not_modify_holdings(self):
        from unittest.mock import patch
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            manager = cls(str(Path(folder) / "positions.json"))
            manager.add(100, "SBIN", 2, 500)
            before = manager.get(100)
            with patch("os.replace", side_effect=OSError("synthetic disk failure")):
                with self.assertRaisesRegex(RuntimeError, "could not be saved"):
                    manager.add(100, "SBIN", 2, 600)
            self.assertEqual(manager.get(100), before)
            self.assertEqual(cls(str(Path(folder) / "positions.json")).get(100), before)

    def test_nonfinite_buy_values_are_rejected(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            manager = cls(str(Path(folder) / "positions.json"))
            for bad in (float("nan"), float("inf"), float("-inf"), 0, -1):
                with self.subTest(price=bad):
                    with self.assertRaises(ValueError):
                        manager.add(100, "SBIN", 2, bad)
            self.assertEqual(manager.get(100), {})

    def test_corrupt_file_fails_closed_instead_of_erasing(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "positions.json"
            path.write_text('{"incomplete":', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "manual recovery required"):
                cls(str(path))

    def test_external_get_cannot_modify_holdings(self):
        cls = load_portfolio_manager()
        with tempfile.TemporaryDirectory() as folder:
            manager = cls(str(Path(folder) / "positions.json"))
            manager.add(100, "SBIN", 2, 500)
            local = manager.get(100)
            local["SBIN"]["qty"] = 99999
            self.assertEqual(manager.get(100)["SBIN"]["qty"], 2)


    def test_partial_portfolio_return_does_not_invent_zero_price(self):
        from datetime import date
        source = MAIN_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "build_portfolio_card")
        namespace = {
            "date": date, "math": math,
            "portfolio": type("Portfolio", (), {"get": lambda self, uid: {
                "SBIN": {"qty": 2, "avg": 500},
                "TCS": {"qty": 4, "avg": 1000},
            }})(),
            "get_live_price": lambda symbol: 550 if symbol == "SBIN" else None,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<portfolio-card>", "exec"), namespace)
        rendered = namespace["build_portfolio_card"](100)
        self.assertIn("Partial P&L: ₹+100.00", rendered)
        self.assertIn("LTP:Unavailable", rendered)
        self.assertIn("priced capital: ₹1,000.00", rendered)
        self.assertNotIn("Total P&L:", rendered)

    def test_all_missing_prices_suppress_profit_and_loss(self):
        from datetime import date
        source = MAIN_FILE.read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "build_portfolio_card")
        namespace = {
            "date": date, "math": math,
            "portfolio": type("Portfolio", (), {"get": lambda self, uid: {
                "TCS": {"qty": 4, "avg": 1000}
            }})(),
            "get_live_price": lambda symbol: float("nan"),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<portfolio-card>", "exec"), namespace)
        rendered = namespace["build_portfolio_card"](100)
        self.assertIn("P&L unavailable", rendered)
        self.assertNotIn("Total P&L:", rendered)
        self.assertIn("LTP:Unavailable", rendered)


class WebhookSecurityTests(unittest.TestCase):
    def test_token_not_permitted_in_webhook_base_url(self):
        source = MAIN_FILE.read_text(encoding="utf-8")
        self.assertIn('if TOKEN in WEBHOOK_URL:', source)
        self.assertIn('raise RuntimeError("WEBHOOK_URL must not contain TELEGRAM_TOKEN")', source)
        self.assertIn('secret_token=WEBHOOK_SECRET', source)
        self.assertIn('WEBHOOK_PATH = "/webhook/telegram"', source)



class TelegramMockSessionTests(unittest.TestCase):
    """One offline mock Telegram webhook session; never uses real Bot API."""

    def test_one_mock_session_webhook_start_buy_portfolio_fundamental_sell(self):
        import concurrent.futures
        import copy
        from collections import deque
        from datetime import date
        import hmac
        import re
        from types import SimpleNamespace

        source = MAIN_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = {
            "StateManager", "PortfolioManager", "safe_send",
            "cmd_start", "cmd_help", "cmd_buy", "cmd_sell",
            "cmd_portfolio", "build_portfolio_card",
            "handle_analysis_callback", "_process_webhook", "webhook", "_allow_heavy",
        }
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in selected:
                isolated = copy.deepcopy(node)
                if isinstance(isolated, ast.FunctionDef):
                    isolated.decorator_list = []  # No Flask or Telegram registration.
                nodes.append(isolated)
        self.assertEqual({n.name for n in nodes}, selected)

        class ImmediateExecutor:
            def submit(self, fn, *args, **kwargs):
                future = concurrent.futures.Future()
                try:
                    future.set_result(fn(*args, **kwargs))
                except Exception as exc:
                    future.set_exception(exc)
                return future

        class MockBot:
            """Telegram API spy/dispatcher: records output, never connects."""

            def __init__(self):
                self.sent = []
                self.acknowledged = []
                self.processed = []

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

            def answer_callback_query(self, callback_id, **kwargs):
                self.acknowledged.append((callback_id, kwargs))

            def process_new_updates(self, updates):
                for update in updates:
                    self.processed.append(update["update_id"])
                    if "message" in update:
                        body = update["message"]
                        message = SimpleNamespace(
                            chat=SimpleNamespace(id=body["chat"]["id"]),
                            text=body["text"],
                        )
                        handlers = {
                            "/start": namespace["cmd_start"],
                            "/help": namespace["cmd_help"],
                            "/buy": namespace["cmd_buy"],
                            "/portfolio": namespace["cmd_portfolio"],
                            "/sell": namespace["cmd_sell"],
                        }
                        handlers[message.text.split()[0]](message)
                    elif "callback_query" in update:
                        body = update["callback_query"]
                        callback = SimpleNamespace(
                            id=body["id"],
                            data=body["data"],
                            message=SimpleNamespace(
                                chat=SimpleNamespace(
                                    id=body["message"]["chat"]["id"]
                                )
                            ),
                        )
                        namespace["handle_analysis_callback"](callback)

        secret = "mock-only-secret-123456789"
        bot = MockBot()
        namespace = {
            "os": os, "json": json, "math": math, "threading": threading,
            "re": re, "html": html, "hmac": hmac, "date": date,
            "logger": logging.getLogger("telegram-mock-smoke"),
            "deque": deque, "bot": bot,
            "executor": ImmediateExecutor(),
            "webhook_executor": ImmediateExecutor(),
            "API_RATE_LIMITER": SimpleNamespace(is_allowed=lambda uid: True),
            "RATE_LIMIT_WINDOW": 60,
            "telebot": SimpleNamespace(
                types=SimpleNamespace(
                    Update=SimpleNamespace(de_json=staticmethod(json.loads))
                )
            ),
            "WEBHOOK_SECRET": secret,
            "main_keyboard": lambda: "MOCK_KEYBOARD",
            "analysis_choice_keyboard": lambda sym: f"MOCK_ACTIONS_{sym}",
            "resolve_symbol": lambda sym: (sym.upper() + ".NS", "Mock Company"),
            "get_live_price": lambda sym: 525.0,  # fixture, not market data
            "build_fundamental_text":
                lambda sym, company: f"MOCK FUNDAMENTAL {sym}: NOT INVESTMENT DATA",
            "_webhook_lock": threading.Lock(),
            "_webhook_slots": threading.BoundedSemaphore(40),
            "_processed_updates": deque(maxlen=1000),
            "_processed_updates_set": set(),
        }
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=nodes, type_ignores=[])
        ), str(MAIN_FILE), "exec"), namespace)
        mock_chat_id = 987654321  # Synthetic user; never an actual recipient.
        with tempfile.TemporaryDirectory() as folder:
            positions = str(Path(folder) / "fake_positions.json")
            namespace["state"] = namespace["StateManager"]()
            namespace["portfolio"] = namespace["PortfolioManager"](positions)
            namespace["state"].set(mock_chat_id, "old_state")

            def fake_update(update_id, *, text=None, callback=None, token=secret):
                if text is not None:
                    body = {"update_id": update_id, "message": {
                        "chat": {"id": mock_chat_id}, "text": text
                    }}
                else:
                    body = {"update_id": update_id, "callback_query": {
                        "id": "fake-callback-id", "data": callback,
                        "message": {"chat": {"id": mock_chat_id}}
                    }}
                namespace["request"] = SimpleNamespace(
                    headers={"X-Telegram-Bot-Api-Secret-Token": token},
                    content_length=len(json.dumps(body)),
                    get_json=lambda silent=True: body,
                )
                return namespace["webhook"]()

            # Reject an unauthenticated Telegram-like request.
            self.assertEqual(fake_update(500, text="/start", token="wrong"), ("unauthorized", 403))
            self.assertEqual(bot.sent, [])

            self.assertEqual(fake_update(501, text="/start"), ("ok", 200))
            self.assertIsNone(namespace["state"].get(mock_chat_id))
            self.assertIn("MSK Bot", bot.sent[-1][1])

            self.assertEqual(fake_update(502, text="/help"), ("ok", 200))
            self.assertIn("/buy", bot.sent[-1][1])

            self.assertEqual(fake_update(503, text="/buy SBIN 2 500"), ("ok", 200))
            self.assertEqual(namespace["portfolio"].get(mock_chat_id)["SBIN"],
                             {"qty": 2, "avg": 500.0})
            before_duplicate = len(bot.sent)
            self.assertEqual(fake_update(503, text="/buy SBIN 2 500"), ("ok", 200))
            self.assertEqual(len(bot.sent), before_duplicate)
            self.assertEqual(namespace["portfolio"].get(mock_chat_id)["SBIN"]["qty"], 2)

            self.assertEqual(fake_update(504, text="/portfolio"), ("ok", 200))
            self.assertTrue(any("SBIN" in msg and "Qty:2" in msg for _, msg, _ in bot.sent))

            # Fundamental callback output is a fixed synthetic fixture; no LLM/API call.
            self.assertEqual(fake_update(505, callback="fund|SBIN"), ("ok", 200))
            self.assertTrue(any("MOCK FUNDAMENTAL SBIN" in msg for _, msg, _ in bot.sent))
            before_bad_callback = len(bot.sent)
            self.assertEqual(fake_update(506, callback="fund|SBIN;DROP"), ("ok", 200))
            self.assertEqual(len(bot.sent), before_bad_callback)
            self.assertGreaterEqual(len(bot.acknowledged), 2)

            self.assertEqual(fake_update(507, text="/sell SBIN"), ("ok", 200))
            self.assertEqual(namespace["portfolio"].get(mock_chat_id), {})
            self.assertEqual(len(bot.processed), 7)
            self.assertTrue(all(uid == mock_chat_id for uid, _, _ in bot.sent))
            self.assertEqual(namespace["PortfolioManager"](positions).get(mock_chat_id), {})

if __name__ == "__main__":
    unittest.main()
