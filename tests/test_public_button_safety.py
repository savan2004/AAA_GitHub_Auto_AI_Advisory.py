"""Static regression checks for public Telegram menu and safe failure paths."""
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
        node = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(self.source, node)

    def test_status_hidden_from_keyboard(self):
        menu = self.function_source("main_keyboard")
        self.assertNotIn("📋 Status", menu)
        self.assertIn("📄 Report", menu)

    def test_status_command_requires_owner(self):
        status = self.function_source("cmd_status")
        self.assertIn('m.chat.type != "private"', status)
        self.assertIn("usage_analytics.is_owner(m.from_user.id)", status)

    def test_breadth_and_swing_do_not_send_raw_exceptions(self):
        for name in ("breadth_btn", "swing_btn"):
            body = self.function_source(name)
            self.assertNotIn('safe_send(chat_id, f"❌ Error: {e}")', body)
            self.assertIn("logger.error(", body)

    def test_top5_levels_not_mislabeled_fixed_ratio(self):
        source = (ROOT / "index_top5.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertNotIn('(R:R 1:2) | T2', source)
        self.assertIn("r['t1']-r['entry']", source)

if __name__ == "__main__":
    unittest.main()
