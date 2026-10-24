import tempfile
import json
import unittest
from pathlib import Path
from monthly_outlook import load_outlook, format_outlook


class MonthlyOutlookTests(unittest.TestCase):
    def test_missing_month_never_invents_report(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(load_outlook("2026-11", folder))
            self.assertIn("No reviewed report", format_outlook(None, "2026-11"))

    def test_approved_report_is_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = {"month": "2026-11", "published_at": "2026-11-01",
                   "as_of": "2026-10-30", "title": "November edition",
                   "summary": "Neutral until further confirmation.",
                   "risk_notice": "Educational research only."}
            (Path(folder) / "2026-11.json").write_text(json.dumps(doc))
            a = load_outlook("2026-11", folder)
            self.assertEqual(a, load_outlook("2026-11", folder))
            self.assertIn("Data as of: 2026-10-30", format_outlook(a, "2026-11"))

    def test_invalid_month_rejected(self):
        with self.assertRaises(ValueError):
            load_outlook("../etc")


class ScannerTimeoutTests(unittest.TestCase):
    def test_partial_result_timeout_handled(self):
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        self.assertIn("done, pending = wait(futs, timeout=20)", src)
        self.assertNotIn("as_completed(futs, timeout=15)", src)
        self.assertIn('m.text == "📅 Monthly Outlook"', src)


if __name__ == "__main__":
    unittest.main()
