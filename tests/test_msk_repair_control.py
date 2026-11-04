"""Offline regression tests for MSK lite control. Run: python -m unittest discover -s tests -p 'test_msk_repair_control.py'"""
import unittest
from tools.msk_repair_control import analyze, classify, sanitize

class RepairControlTests(unittest.TestCase):
    def test_known_errors(self):
        self.assertEqual(classify("Screener err: 2 (of 10) futures unfinished")[0], "SCANNER_TIMEOUT")
        self.assertEqual(classify("ALL sources failed for JIO")[0], "MARKET_DATA_FAILURE")
        self.assertEqual(classify("HTTP Error 401")[0], "PROVIDER_AUTH")
        self.assertEqual(classify("HTTP Error 429")[0], "PROVIDER_RATE_LIMIT")

    def test_unknown_is_recorded(self):
        self.assertEqual(classify("new and unexpected exception")[0], "UNKNOWN")

    def test_redacts_secrets(self):
        clean = sanitize("token=abc123 password=private Bearer my.jwt.value postgresql://user:pass@host/db")
        for secret in ("abc123", "private", "my.jwt.value", "user:pass"):
            self.assertNotIn(secret, clean)

    def test_deduplicates_without_dropping_events(self):
        result = analyze(["HTTP Error 401", "HTTP Error 401", "ALL sources failed for JIO"])
        self.assertEqual(result["total_events"], 3)
        self.assertEqual(len(result["incidents"]), 2)
        self.assertEqual(result["mode"], "read_only")
        self.assertTrue(all("action" in item for item in result["incidents"]))

if __name__ == "__main__":
    unittest.main()
