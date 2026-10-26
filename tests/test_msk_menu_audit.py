"""Static audit of Telegram reply buttons, callback actions and public branding."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "config.py").read_text(encoding="utf-8")
TREE = ast.parse(MAIN)


class MenuAudit(unittest.TestCase):
    def test_all_menu_buttons_have_handler(self):
        menu = MAIN.split("def main_keyboard():", 1)[1].split("def analysis_choice_keyboard", 1)[0]
        labels = re.findall(r'"([^"]+)"', menu)
        for label in labels:
            if not label or label == "3":
                continue
            self.assertIn('m.text', MAIN, "reply-button handler logic missing")
            self.assertIn(label, MAIN.split("def analysis_choice_keyboard", 1)[1], f"Unrouted reply button {label}")

    def test_inline_action_handlers(self):
        actions = re.findall(r'callback_data=f"([a-z]+)\|', MAIN)
        self.assertEqual(set(actions), {"analysis", "tech", "fund", "chart", "pdf", "menu"})
        for action in actions:
            self.assertIn(f'action == "{action}"', MAIN, f"Unrouted callback {action}")

    def test_public_branding(self):
        self.assertIn("MSK Bot", MAIN)
        self.assertNotIn("AutoAI Advisory Bot v6.2", MAIN)
        self.assertIn('_get_env("BRAND_NAME", "MSK Bot")', CONFIG)

    def test_duplicate_webhook_releases_slot(self):
        self.assertRegex(MAIN, r'if uid in _processed_updates_set:\s+_webhook_slots.release\(\)\s+return "ok", 200')

    def test_callback_validation(self):
        self.assertIn('re.fullmatch(r"[A-Za-z0-9&_.-]{1,30}", sym)', MAIN)


if __name__ == "__main__":
    unittest.main()
