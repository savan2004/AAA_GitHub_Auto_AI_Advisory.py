"""Regression: no public Status menu or /status command in MSK Bot."""
import ast
from pathlib import Path
import unittest


class StatusRemovalTests(unittest.TestCase):
    def test_status_ui_and_command_removed(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("📋 Status", source)
        self.assertNotIn('commands=["status"]', source)
        functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("cmd_status", functions)
        self.assertNotIn("stat_btn", functions)
        self.assertIn("main_keyboard", functions)


if __name__ == "__main__":
    unittest.main()
