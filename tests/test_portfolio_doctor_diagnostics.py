"""Reconciliation, truthful diagnostics and four-page visual report regression."""
import io
import unittest
from decimal import Decimal
from pypdf import PdfReader

from portfolio_doctor_diagnostics import diagnose_portfolio, industry_exposure
from portfolio_doctor_report import _report_records, build_pdf_bytes


def holding(symbol, qty, buy, close, *, ema20=11, ema50=12, rsi=65, date="2026-11-17"):
    return dict(symbol=symbol, qty=qty, buy=buy, close=close, date=date,
                ema20=ema20, ema50=ema50, rsi14=rsi, high20=15, low20=9,
                return20=2.5)


class MonetaryReconciliation(unittest.TestCase):
    def test_displayed_close_times_quantity_reconciles_to_paisa(self):
        # Provider float noise must not create phantom profit or penny totals.
        h = _report_records([holding("SAMPLEA", 40000, 15, 12.30000019)])[0]
        self.assertEqual(h["close"], Decimal("12.30"))
        self.assertEqual(h["value"], Decimal("492000.00"))
        self.assertEqual(h["pnl"], Decimal("-108000.00"))
        text = "\n".join(page.extract_text() or "" for page in
                         PdfReader(io.BytesIO(build_pdf_bytes(
                             [holding("SAMPLEA", 40000, 15, 12.30000019)]))).pages)
        self.assertIn("492,000.00", text)
        self.assertNotIn("492,000.01", text)

    def test_diagnostics_expose_concentration_and_real_profit_drivers(self):
        recs = _report_records([
            holding("SAMPLEA", 1, 100, 100, ema20=95, ema50=90),
            holding("SAMPLEB", 9, 200, 100, ema20=110, ema50=120, rsi=45),
            holding("SAMPLEC", 2, 50, 200, ema20=190, ema50=180),
        ])
        d = diagnose_portfolio(recs)
        self.assertEqual(d["largest_symbol"], "SAMPLEB")
        self.assertEqual(d["largest_ten_percent_stress"], Decimal("90.000"))
        self.assertEqual(d["top_gain"][0], "SAMPLEC")
        self.assertEqual(d["top_loss"][0], "SAMPLEB")
        self.assertEqual(d["by_symbol"]["SAMPLEB"]["priority"], "HIGH FLAGS")
        self.assertAlmostEqual(d["top_two_pct"], 100 * 1300 / 1400)
        self.assertTrue(1 < d["effective_positions"] < 3)

    def test_industry_exposure_no_unknown_sector_fabrication(self):
        rows = _report_records([
            holding("SAMPLEA", 1, 100, 100, ema20=95, ema50=90),
            holding("SAMPLEB", 3, 100, 100, ema20=95, ema50=90),
        ])
        self.assertEqual(industry_exposure(rows, {}), [])
        groups = industry_exposure(rows, {"status": "SCREENED",
                                         "held_industries": {"SAMPLEA": "Capital Goods"}})
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["industry"], "Unclassified / no official match")
        self.assertAlmostEqual(groups[0]["weight"], 75)

    def test_four_page_pdf_explains_real_metrics_without_trade_orders(self):
        rows = [
            holding("SAMPLEA", 2, 100, 130, ema20=120, ema50=110, rsi=74),
            holding("SAMPLEB", 80, 20, 11, ema20=12, ema50=13, rsi=45),
        ]
        peers = {
            "status": "SCREENED", "universe": 501, "pool": 16, "screened": 12,
            "fresh": 11, "qualified": 0, "candidates": [], "comparisons": [],
            "held_industries": {"SAMPLEA": "Capital Goods", "SAMPLEB": "Telecommunication"},
        }
        pages = PdfReader(io.BytesIO(build_pdf_bytes(rows, peers=peers))).pages
        self.assertEqual(len(pages), 4)
        page_text = [page.extract_text() or "" for page in pages]
        self.assertIn("WHAT IS DRIVING RESULTS", page_text[0])
        self.assertIn("Effective stock count:", page_text[0])
        self.assertIn("HIGH FLAGS", page_text[1])
        self.assertIn("20-session change: +2.5%", page_text[1])
        self.assertIn("OFFICIAL INDUSTRY EXPOSURE", page_text[2])
        self.assertIn("Capital Goods", page_text[2])
        self.assertIn("CONCENTRATION SENSITIVITY", page_text[3])
        self.assertIn("NOT a probability", page_text[3])
        self.assertIn("quality ranking", page_text[3].lower())
        self.assertNotIn("EXIT COMPLETELY", "\n".join(page_text))
        self.assertNotIn("ADD ON DIPS", "\n".join(page_text))


if __name__ == "__main__":
    unittest.main()
