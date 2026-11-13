"""Recovery regression: temporary financial-source failure is never cached for six hours."""
import unittest
from unittest.mock import patch
import research_data as rd


class StatementRecoveryTests(unittest.TestCase):
    def setUp(self):
        rd._cache.clear()
        self.addCleanup(rd._cache.clear)

    def test_empty_screener_statement_cache_expires_quickly(self):
        with patch.object(rd, "_screener_html", return_value=None) as source:
            self.assertEqual(rd.get_screener_extras("SBIN"), {})
            self.assertEqual(rd.get_screener_extras("SBIN"), {})
            self.assertEqual(source.call_count, 1)
            item = rd._cache["scr_SBIN"]
            self.assertEqual(item["ttl"], 45)
            item["ts"] -= 46
            self.assertEqual(rd.get_screener_extras("SBIN"), {})
            self.assertEqual(source.call_count, 2)

    def test_failed_financials_then_successful_source_recovered(self):
        annual = {"label": "Mar 2026", "revenue": 1200, "net_profit": 140}
        with patch.object(rd, "get_screener_extras",
                          side_effect=[{}, {"years": [annual], "quarters": []}]) as screener, \
             patch.object(rd, "_yahoo_statements",
                          return_value={"years": [], "quarters": []}):
            self.assertIsNone(rd.get_statements("SBIN"))
            self.assertEqual(rd._cache["stm_SBIN"]["ttl"], 45)
            self.assertIsNone(rd.get_statements("SBIN"))  # bounded negative cache
            self.assertEqual(screener.call_count, 1)
            rd._cache["stm_SBIN"]["ts"] -= 46
            restored = rd.get_statements("SBIN")
            self.assertEqual(restored["source"], "Screener.in")
            self.assertEqual(restored["years"][0]["net_profit"], 140)
            self.assertEqual(screener.call_count, 2)
            self.assertEqual(rd._cache["stm_SBIN"]["ttl"], 300)

    def test_unusable_consolidated_falls_back_to_standalone(self):
        class Response:
            def __init__(self, html):
                self.ok = True
                self.status_code = 200
                self.text = html
        with patch.object(rd.requests, "get", side_effect=[
                 Response("<table class='data-table'>irrelevant</table>"),
                 Response("<table class='data-table'>standalone</table>")
             ]) as get, \
             patch.object(rd, "parse_years",
                 side_effect=lambda text: [{"label": "Mar 2026"}] if "standalone" in text else []), \
             patch.object(rd, "parse_quarters", return_value=[]):
            html = rd._screener_html("SBIN")
            self.assertIn("standalone", html)
            self.assertEqual(get.call_count, 2)
            self.assertIn("standalone", rd._cget("scr_html_SBIN"))

    def test_no_fabricated_financials_after_double_outage(self):
        with patch.object(rd, "get_screener_extras", return_value={}), \
             patch.object(rd, "_yahoo_statements", side_effect=TimeoutError):
            self.assertIsNone(rd.get_statements("SBIN"))
            self.assertEqual(rd._cache["stm_SBIN"]["val"], {})


if __name__ == "__main__":
    unittest.main()
