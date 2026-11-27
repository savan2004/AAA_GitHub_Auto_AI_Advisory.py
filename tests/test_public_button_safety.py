"""Regression: public bot messages do not expose raw exceptions or Status command."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicButtonSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def function_source(self, name):
        node = next(n for n in self.tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(self.source, node)

    def test_status_button_and_command_are_removed(self):
        keyboard = self.function_source("main_keyboard")
        self.assertNotIn("📋 Status", keyboard)
        self.assertNotIn("cmd_status", {n.name for n in self.tree.body
                                        if isinstance(n, ast.FunctionDef)})
        self.assertNotIn('commands=["status"]', self.source)

    def test_breadth_failure_is_safe_and_generic(self):
        body = self.function_source("breadth_btn")
        self.assertIn("Market breadth is temporarily unavailable", body)
        self.assertNotIn('safe_send(chat_id, f"❌ Error: {e}")', body)
        self.assertNotIn("exc_info=True", body)


if __name__ == "__main__":
    unittest.main()
