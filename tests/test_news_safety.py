"""Offline safety regressions: news outages never produce invented market facts."""
import os
import unittest
from unittest.mock import patch
import market_news as news


class NewsSafetyTests(unittest.TestCase):
    def test_market_source_outage_has_no_fabricated_numbers_or_headlines(self):
        with patch.object(news, "_fetch_tavily", return_value=[]), \
             patch.object(news, "_fetch_rss", return_value=[]), \
             patch.dict(os.environ, {"ALPHA_VANTAGE_KEY": ""}):
            message = news.get_market_news(5)
        self.assertIn("Source headlines unavailable", message)
        self.assertNotIn("bullish zone above", message)
        self.assertNotIn("Small-cap rally", message)
        self.assertNotIn("RBI likely", message)

    def test_stock_source_outage_returns_empty_instead_of_research_claim(self):
        with patch.object(news, "_fetch_tavily", return_value=[]), \
             patch.object(news, "_fetch_rss", return_value=[]), \
             patch.dict(os.environ, {"FINNHUB_API_KEY": "", "TAVILY_API_KEY": ""}):
            for symbol in ("RELIANCE", "TCS", "UNKNOWN"):
                with self.subTest(symbol=symbol):
                    self.assertEqual(news.get_stock_news(symbol), "")

    def test_source_news_is_escaped_and_not_claimed_to_be_verified_current(self):
        with patch.object(news, "_fetch_tavily",
                          return_value=["Nifty <script> moves & trading on exchange"]), \
             patch.object(news, "_fetch_rss", return_value=[]):
            message = news.get_market_news(1)
        self.assertIn("&lt;script&gt;", message)
        self.assertIn("&amp;", message)
        self.assertNotIn("<script>", message)
        self.assertIn("publication times unverified", message)

    def test_stock_source_title_is_escaped_and_stamped_as_undated(self):
        with patch.object(news, "_fetch_tavily",
                          return_value=["RELIANCE <b> announced deal & investment"]):
            message = news.get_stock_news("RELIANCE")
        self.assertIn("&lt;b&gt;", message)
        self.assertNotIn("<b>", message)
        self.assertIn("[date unverified]", message)

    def test_legacy_placeholder_map_is_empty_for_pdf_compatibility(self):
        self.assertEqual(news._STOCK_NEWS_FALLBACK, {})


if __name__ == "__main__":
    unittest.main()
