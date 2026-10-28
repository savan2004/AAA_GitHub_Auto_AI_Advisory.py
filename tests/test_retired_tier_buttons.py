"""Regression for retiring only the three broken tier-scanner actions.

These checks intentionally inspect source without importing main.py: importing it
requires a real Telegram token and would start external network integrations.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
MODULE = ast.parse(SOURCE)
RETIRED_LABELS = ("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")


class RetiredTierButtonsTests(unittest.TestCase):
    def test_risk_tier_worker_and_dispatcher_fully_removed(self):
        functions = {node.name for node in ast.walk(MODULE)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("build_scan", functions)
        self.assertNotIn("scan_btn", functions)
        self.assertNotIn("SCREENER_STOCKS", SOURCE)
        self.assertNotIn("as_completed", SOURCE)
        self.assertNotIn("2 (of 10) futures unfinished", SOURCE)

    def test_old_reply_buttons_not_registered(self):
        menu = SOURCE.split("def main_keyboard():", 1)[1].split(
            "def analysis_choice_keyboard", 1)[0]
        for label in RETIRED_LABELS:
            self.assertNotIn(label, menu)
            self.assertNotIn(label, SOURCE)
        self.assertIn("💼 Portfolio", menu)
        self.assertIn("🎯 Swing (Safe)", menu)
        self.assertIn("🚀 Swing (Agr)", menu)

    def test_no_removed_tier_callback_or_message_handler(self):
        handlers = SOURCE.split("# ── Keyboards", 1)[1]
        self.assertNotIn("build_scan(", handlers)
        self.assertNotIn('callback_data="tier|', handlers)
        self.assertNotIn('callback_data=f"tier|', handlers)
        for label in RETIRED_LABELS:
            self.assertNotIn(label, handlers)

    def test_legacy_keyboard_messages_are_intercepted_before_search(self):
        callback = SOURCE.split("def handle_text(m):", 1)[1]
        marker = 'if text.startswith(("🏦", "⚖", "🚀")):'
        self.assertIn(marker, callback)
        self.assertLess(callback.index(marker), callback.index("looks_ticker ="))
        self.assertLess(callback.index(marker), callback.index("looks_name ="))
        self.assertIn("reply_markup=main_keyboard()", callback)

    def test_other_core_functions_are_preserved(self):
        functions = {node.name for node in ast.walk(MODULE)
                     if isinstance(node, ast.FunctionDef)}
        for name in ("main_keyboard", "swing_btn", "breadth_btn", "build_breadth",
                     "build_portfolio_card", "run_report", "cmd_report",
                     "handle_text", "webhook"):
            self.assertIn(name, functions)


if __name__ == "__main__":
    unittest.main()
