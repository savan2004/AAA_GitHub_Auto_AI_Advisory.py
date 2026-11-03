"""Groq report editorial is optional and never modifies audited figures."""
import unittest
from unittest.mock import MagicMock, patch
import groq_overnight_editor as editorial


class GroqEditorialTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {"MSK_GROQ_REPORT_ENABLED": "0", "GROQ_API_KEY": "example"}):
            self.assertIsNone(editorial.groq_editorial("moderate", "2026-10-29", 150, 0, 5))

    def test_missing_key_returns_none(self):
        with patch.dict("os.environ", {"MSK_GROQ_REPORT_ENABLED": "1", "GROQ_API_KEY": ""}):
            self.assertIsNone(editorial.groq_editorial("moderate", "2026-10-29", 150, 0, 5))

    def test_whitelist_contains_only_approved_phrases(self):
        self.assertTrue(all("₹" not in item for item in editorial.ALLOWED.values()))
        self.assertNotIn("BUY", " ".join(editorial.ALLOWED.values()))


if __name__ == "__main__":
    unittest.main()
