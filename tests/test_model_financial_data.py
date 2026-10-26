"""Contract tests for named NSE financial evidence; no live network needed."""
from datetime import datetime, timezone
import unittest

from model_financial_data import parse_screener_model

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp()


def demo_html(symbol="INFY", fy="Mar 2026", older="Mar 2025"):
    return f"""<!doctype html><html><body>
      <h1>Example Software Ltd</h1>
      <a href="https://www.nseindia.com">NSE: {symbol}</a>
      <ul id="top-ratios">
        <li><span class="name">Market Cap</span><span class="number">₹ 4,72,503 Cr.</span></li>
        <li><span class="name">Stock P/E</span><span class="number">14.9</span></li>
        <li><span class="name">ROE</span><span class="number">31.9 %</span></li>
      </ul>
      <section id="profit-loss"><table><thead><tr>
        <th></th><th>{older}</th><th>{fy}</th><th>TTM</th>
      </tr></thead><tbody>
       <tr><td>Sales +</td><td>162,990</td><td>178,650</td><td>189,336</td></tr>
       </tbody></table></section>
      <section id="balance-sheet"><table><thead><tr>
        <th></th><th>{older}</th><th>{fy}</th>
      </tr></thead><tbody>
        <tr><td>Equity Capital</td><td>2,073</td><td>2,024</td></tr>
        <tr><td>Reserves</td><td>93,745</td><td>90,828</td></tr>
        <tr><td>Borrowings +</td><td>8,227</td><td>9,176</td></tr>
      </tbody></table></section>
      </body></html>"""


class ScreenerModelEvidenceTests(unittest.TestCase):
    def parse(self, html=None, **kwargs):
        return parse_screener_model(html if html is not None else demo_html(), "INFY",
                                    fetched_at=NOW, sector="Technology", **kwargs)

    def test_normalizes_roe_growth_debt_and_rupees(self):
        result, reason = self.parse()
        self.assertIsNone(reason)
        self.assertEqual(result["roe_pct"], 31.9)
        self.assertEqual(result["pe"], 14.9)
        self.assertEqual(result["market_cap"], 472503 * 10_000_000)
        self.assertAlmostEqual(result["revenue_growth_pct"], 9.60795, places=4)
        self.assertAlmostEqual(result["debt_equity_ratio"], 9176 / (2024 + 90828), places=5)
        self.assertEqual(result["fiscal_sales_period"], "2026-03")
        self.assertIn("Screener.in", result["provider"])
        self.assertEqual(result["balance_sheet_period"], "2026-03")

    def test_rejects_wrong_company_and_anti_bot_html(self):
        self.assertIn("NSE symbol", self.parse(demo_html("TCS"))[1])
        self.assertIsNone(self.parse("<html>Access denied</html>")[0])

    def test_no_fake_growth_from_same_year_or_ttm(self):
        data = demo_html().replace("Mar 2025", "Mar 2024")
        self.assertIn("non-consecutive", self.parse(data)[1])

    def test_stale_fiscal_period_refused(self):
        data = demo_html(fy="Mar 2023", older="Mar 2022")
        self.assertIn("stale", self.parse(data)[1])

    def test_negative_equity_not_passed(self):
        data = demo_html().replace("<td>90,828</td>", "<td>-9,999</td>")
        self.assertIsNone(self.parse(data)[0])

    def test_missing_balance_sheet_refused(self):
        data = demo_html().replace("<td>9,176</td>", "<td>-</td>")
        self.assertIsNone(self.parse(data)[0])

    def test_rejects_unreasonable_company_valuation_and_dates(self):
        data = demo_html().replace("14.9", "550")
        self.assertIsNone(self.parse(data)[0])
        data = demo_html(fy="Mar 2030", older="Mar 2029")
        self.assertIsNone(self.parse(data)[0])

    def test_bad_sector_rejected(self):
        record, why = parse_screener_model(demo_html(), "INFY", fetched_at=NOW, sector=None)
        self.assertIsNone(record)
        self.assertIn("sector", why)

    def test_missing_roe_rejected(self):
        data = demo_html().replace("31.9 %", "-")
        self.assertIn("ROE", self.parse(data)[1])


if __name__ == "__main__":
    unittest.main()
