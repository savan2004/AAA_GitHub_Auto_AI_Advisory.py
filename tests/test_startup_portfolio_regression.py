"""Regression checks for webhook configuration and portfolio deadlocks.

Runs without importing main.py, which needs live Telegram credentials.
"""
import ast
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import unittest

MAIN_FILE = Path(__file__).resolve().parents[1] / "main.py"


def load_portfolio_manager():
    source = MAIN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
    namespace = {"os": os, "json": json, "threading": threading, "logger": logging.getLogger(__name__)}
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


class WebhookSecurityTests(unittest.TestCase):
    def test_token_not_permitted_in_webhook_base_url(self):
        source = MAIN_FILE.read_text(encoding="utf-8")
        self.assertIn('if TOKEN in WEBHOOK_URL:', source)
        self.assertIn('raise RuntimeError("WEBHOOK_URL must not contain TELEGRAM_TOKEN")', source)
        self.assertIn('secret_token=WEBHOOK_SECRET', source)
        self.assertIn('WEBHOOK_PATH = "/webhook/telegram"', source)


if __name__ == "__main__":
    unittest.main()
