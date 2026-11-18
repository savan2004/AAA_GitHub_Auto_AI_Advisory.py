"""Evidence-bound sample-inspired basic Portfolio Doctor PDF regressions."""
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pypdf import PdfReader
from portfolio_doctor_input import decode_holdings
from portfolio_doctor_report import build_pdf_bytes


class SampleReportTests(unittest.TestCase):
    def test_numbered_two_stock_input_and_evidence_bound_pdf(self):
        positions = decode_holdings("typed_holdings.csv",
                                    b"1. SBIN 100 840\n2. BHEL 150 150")
        self.assertEqual([h["symbol"] for h in positions], ["SBIN", "BHEL"])
        closes = {"SBIN": 1108.60, "BHEL": 430.65}
        records = []
        for position in positions:
            records.append(dict(position, close=closes[position["symbol"]],
                                date="2026-11-17", ema20=1000.0,
                                ema50=950.0, rsi14=55.0,
                                high20=1200.0, low20=350.0))
        pdf = build_pdf_bytes(records)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        pages = PdfReader(io.BytesIO(pdf)).pages
        content = "\n".join(page.extract_text() for page in pages)
        self.assertIn("EXECUTIVE DIAGNOSIS", content)
        self.assertIn("HOLDING ALLOCATION", content)
        self.assertIn("PORTFOLIO HEALTH", content)
        self.assertIn("106,500.00", content)
        self.assertIn("175,457.50", content)
        self.assertIn("68,957.50", content)
        self.assertIn("sector diversification cannot be established", content)
        self.assertIn("Health score: withheld", content)
        self.assertNotIn("ADD ON DIPS", content)
        self.assertNotIn("EXIT COMPLETELY", content)


if __name__ == "__main__":
    unittest.main()
