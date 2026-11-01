"""Offline regression checks for the read-only Monthly Outlook archive."""
import json
import tempfile
import unittest
from pathlib import Path

from monthly_outlook import current_month, format_outlook, load_outlook


class MonthlyOutlookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = {
            "month": "2026-11", "approved": True,
            "published_at": "2026-11-01", "as_of": "2026-10-30",
            "title": "November market outlook",
            "summary": "Neutral until evidence confirms a trend.",
            "risk_notice": "Educational research, not investment advice.",
        }

    def write(self, data):
        (self.root / "2026-11.json").write_text(json.dumps(data), encoding="utf-8")

    def test_missing_edition_does_not_invent_market_data(self):
        self.assertIsNone(load_outlook("2026-11", self.root))
        self.assertIn("No reviewed edition", format_outlook(None, "2026-11"))

    def test_unapproved_edition_is_not_published(self):
        self.doc["approved"] = False
        self.write(self.doc)
        self.assertIsNone(load_outlook("2026-11", self.root))

    def test_one_approved_edition_is_repeatable(self):
        self.write(self.doc)
        self.assertEqual(load_outlook("2026-11", self.root), load_outlook("2026-11", self.root))
        text = format_outlook(load_outlook("2026-11", self.root), "2026-11")
        self.assertIn("2026-10-30", text)
        self.assertIn("\nEdition:", text)
        self.assertNotIn("\\nEdition:", text)

    def test_html_is_escaped(self):
        self.doc["title"] = "<i>do not trust</i>"
        self.doc["summary"] = "<script>alert('x')</script>"
        self.write(self.doc)
        output = format_outlook(load_outlook("2026-11", self.root), "2026-11")
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("&lt;i&gt;", output)

    def test_invalid_month_rejected(self):
        for month in ("../etc", "2026-13", "2026-1", "2026-11/../x"):
            with self.subTest(month=month), self.assertRaises(ValueError):
                load_outlook(month, self.root)

    def test_missing_field_and_mismatched_month_fail(self):
        self.doc["month"] = "2026-10"
        self.write(self.doc)
        with self.assertRaises(ValueError):
            load_outlook("2026-11", self.root)
        self.doc["month"] = "2026-11"
        self.doc.pop("as_of")
        self.write(self.doc)
        with self.assertRaises(ValueError):
            load_outlook("2026-11", self.root)

    def test_oversize_summary_rejected(self):
        self.doc["summary"] = "x" * 3001
        self.write(self.doc)
        with self.assertRaises(ValueError):
            load_outlook("2026-11", self.root)

    def test_current_month_is_valid_format(self):
        self.assertRegex(current_month(), r"^\d{4}-(0[1-9]|1[0-2])$")


if __name__ == "__main__":
    unittest.main()
