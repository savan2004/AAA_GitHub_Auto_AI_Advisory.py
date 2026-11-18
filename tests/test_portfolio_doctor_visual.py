"""Styled Doctor PDF: four-page layout, full totals, peer evidence, privacy."""
import io
import unittest
from pypdf import PdfReader

from portfolio_doctor_report import build_pdf_bytes


def holding(symbol, qty, buy, close, *, rsi=55):
    return {"symbol": symbol, "qty": qty, "buy": buy, "close": close,
            "date": "2026-11-17", "ema20": close * .98,
            "ema50": close * .95, "rsi14": rsi,
            "high20": close * 1.05, "low20": close * .85}


class PortfolioVisualAcceptance(unittest.TestCase):
    def test_five_holding_report_is_four_real_pages_with_source_data(self):
        rows = [holding("BEL", 10, 100, 399.55),
                holding("RELIANCE", 50, 800, 1367, rsi=75),
                holding("SBIN", 150, 865.5, 1108.6),
                holding("DLF", 500, 458, 765.95),
                holding("IDEA", 40000, 15, 12.30, rsi=44.7)]
        peers = {
            "status": "SCREENED", "universe": 501, "pool": 207,
            "screened": 36, "fresh": 35, "qualified": 1,
            "candidates": [{"symbol": "BHARTIHEXA", "industry": "Telecommunication",
                            "score": 50, "close": 1672.70, "date": "2026-11-17"}],
            "comparisons": [{"symbol": "NTPC", "industry": "Electric Utilities",
                             "close": 330.0, "return20": 2.5,
                             "rsi14": 55.4, "date": "2026-11-17"}],
        }
        raw = build_pdf_bytes(rows, peers=peers)
        reader = PdfReader(io.BytesIO(raw), strict=True)
        self.assertEqual(len(reader.pages), 4)
        pages = [page.extract_text() for page in reader.pages]
        self.assertIn("01 / PORTFOLIO DASHBOARD", pages[0])
        self.assertIn("EXECUTIVE DIAGNOSIS", pages[0])
        self.assertIn("Invested: Rs 999,825.00", pages[0])
        self.assertIn("02 / HOLDING-WISE VERIFIED RESULTS", pages[1])
        for symbol in ("BEL", "RELIANCE", "SBIN", "DLF", "IDEA"):
            self.assertIn(symbol + " | Qty", pages[1])
        self.assertIn("03 / NIFTY 500 INDUSTRY-PEER WATCHLIST", pages[2])
        self.assertIn("BHARTIHEXA", pages[2])
        self.assertIn("NTPC", pages[2])
        self.assertIn("NOT QUALIFIED", pages[2])
        self.assertIn("Screened: 36", pages[2])
        self.assertIn("04 / RISK, SOURCES AND LIMITS", pages[3])
        self.assertIn("IDEA concentration", pages[3])
        self.assertIn("No BUY/SELL signal", pages[3])
        self.assertNotIn("BUY NOW", "\n".join(pages))
        self.assertNotIn("EXIT COMPLETELY", "\n".join(pages))

    def test_unavailable_peer_research_never_blocks_pdf(self):
        raw = build_pdf_bytes([holding("SAMPLEA", 5, 90, 100)])
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        self.assertEqual(len(pages), 4)
        text = "\n".join(page.extract_text() for page in pages)
        self.assertIn("Unavailable: official membership", text)
        self.assertIn("500", text)
        self.assertIn("No BUY/SELL signal", text)


if __name__ == "__main__":
    unittest.main()
