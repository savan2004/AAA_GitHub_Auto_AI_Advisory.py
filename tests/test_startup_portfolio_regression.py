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


class FundamentalButtonAccuracyTests(unittest.TestCase):
    """Exercise the real Fundamental button formatter with immutable provider fixtures."""

    def _render(self, fund, info):
        import sys
        import types
        from unittest.mock import patch

        source = MAIN_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = [
            n for n in tree.body if isinstance(n, ast.FunctionDef) and
            n.name in ("build_fundamental_text", "_fundamental_field",
                       "_verified_fundamental_number", "frow", "fmt_mcap", "_fmt_revenue")
        ]
        self.assertEqual(len(selected), 6)
        scope = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]),
                     str(MAIN_FILE), "exec"), scope)
        scope["get_info"] = lambda _sym: info
        stub = types.ModuleType("fundamentals")
        stub.get_fundamentals = lambda _sym: fund
        with patch.dict(sys.modules, {"fundamentals": stub}):
            return scope["build_fundamental_text"]("SBIN", "Mock SBIN")

    def test_fixture_zero_roa_and_debt_equity_preserved(self):
        result = self._render(
            {"roe": 0, "de": 0, "div_y": 0, "eps": 0, "mcap": 100000000},
            {"debtToEquity": 300, "eps": 22},
        )
        self.assertIn("ROE           : 0.0%", result)
        self.assertIn("Debt/Equity   : 0.0", result)
        self.assertIn("EPS: ₹0", result)
        self.assertIn("Div Yield     : 0.0%", result)
        self.assertNotIn("EPS: ₹22", result)

    def test_yahoo_debt_equity_percent_is_converted_to_ratio(self):
        result = self._render({"pe": 12, "roe": 14}, {"debtToEquity": 45})
        self.assertIn("Debt/Equity   : 0.45", result)
        self.assertNotIn("Debt/Equity   : 45", result)

    def test_invalid_primary_value_uses_only_valid_fallback(self):
        result = self._render(
            {"pe": float("nan"), "pb": float("inf"), "mcap": True, "roe": float("nan")},
            {"pe": 18, "pb": 2.5, "market_cap": 200000000, "debtToEquity": -50},
        )
        self.assertIn("PE (TTM)      : 18.0", result)
        self.assertIn("Price/Book    : 2.5", result)
        self.assertIn("Debt/Equity   : N/A", result)
        self.assertNotIn("nan", result.lower())
        self.assertNotIn("inf", result.lower())

    def test_revenue_larger_than_market_cap_is_not_discarded(self):
        # A distributor can have sales several times its market value.
        result = self._render(
            {"mcap": 100000000, "rev": 900000000, "pe": 10}, {}
        )
        self.assertIn("Revenue       : ₹90.00 Cr", result)
        self.assertNotIn("data err", result)

    def test_html_company_name_is_escaped(self):
        # The Telegram bot uses HTML parse mode; provider names are untrusted.
        result = self._render(
            {"name": "<bad&name>", "pe": 10}, {}
        )
        self.assertNotIn("<bad&name>", result)

    def test_missing_unreconciled_data_is_not_fabricated(self):
        result = self._render({}, {})
        self.assertIn("Fundamentals unavailable", result)
        self.assertNotIn("BUY", result)

    def test_partial_data_has_freshness_warning_not_claim_of_live_accuracy(self):
        result = self._render({"pe": 11}, {})
        self.assertIn("Data timestamp and provider reconciliation not verified", result)
        self.assertIn("Revenue       : N/A", result)
        self.assertNotIn("BUY", result)


if __name__ == "__main__":
    unittest.main()
