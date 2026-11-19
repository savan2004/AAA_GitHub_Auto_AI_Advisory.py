"""Evidence-driven regression of historical Portfolio Doctor risk (synthetic)."""
import io
import math
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd
from pypdf import PdfReader

from portfolio_doctor_live import IST
from portfolio_doctor_report import build_pdf_bytes
from portfolio_doctor_risk_history import calculate_historical_risk

# Before the 18-Nov close: 17-Nov is the expected completed session.
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=IST)
DATES = pd.bdate_range(end="2026-11-17", periods=140)


def frame(scale=1.0, last="2026-11-17"):
    dates = pd.bdate_range(end=last, periods=140)
    closes = [100 + scale * (0.18 * i + 4 * math.sin(i / 8))
              for i in range(140)]
    return pd.DataFrame({"Open": [p * .99 for p in closes],
                         "High": [p * 1.01 for p in closes],
                         "Low": [p * .98 for p in closes],
                         "Close": closes, "Volume": [100000] * 140},
                        index=dates)


def holding(symbol, qty, close):
    return {"symbol": symbol, "qty": qty, "buy": 90, "close": close,
            "date": "2026-11-17", "ema20": close * .96,
            "ema50": close * .94, "rsi14": 55.0,
            "high20": close * 1.03, "low20": close * .90}


class HistoricalRiskAcceptance(unittest.TestCase):
    def setUp(self):
        self.frames = {"SAMPLEA": frame(1.0), "SAMPLEB": frame(0.7)}
        self.holdings = [
            holding("SAMPLEA", 20, float(self.frames["SAMPLEA"]["Close"].iloc[-1])),
            holding("SAMPLEB", 50, float(self.frames["SAMPLEB"]["Close"].iloc[-1])),
        ]

    def test_full_history_adds_verified_fifth_page_and_rebased_comparison(self):
        risk = calculate_historical_risk(
            self.holdings, self.frames, benchmark_frame=frame(1.2), now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        self.assertEqual(risk["included"], 2)
        self.assertEqual(risk["coverage"], 140)
        self.assertEqual(risk["benchmark_coverage"], 140)
        self.assertIsNotNone(risk["beta"])
        self.assertEqual(len(risk["chart_portfolio"]), 140)
        self.assertEqual(len(risk["chart_nifty"]), 140)
        self.assertAlmostEqual(risk["chart_portfolio"][0], 100.0)
        self.assertAlmostEqual(risk["chart_nifty"][0], 100.0)
        self.assertGreater(risk["annual_vol_pct"], 0)
        self.assertLessEqual(risk["max_drawdown_pct"], 0)
        self.assertTrue(risk["pairs"])
        raw = build_pdf_bytes(self.holdings, risk=risk)
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        self.assertEqual(len(pages), 5)
        text = pages[4].extract_text()
        for phrase in ("HISTORICAL RISK & NIFTY 50", "Annualised volatility:",
                       "Max historical drawdown:", "Beta vs Nifty 50:",
                       "STOCKS THAT MOVED TOGETHER", "START = 100",
                       "not your actual trade history"):
            self.assertIn(phrase, text)
        self.assertIn("Page 5 of 5", text)

    def test_one_missing_or_stale_holding_does_not_fake_whole_portfolio_risk(self):
        for altered in ({"SAMPLEA": self.frames["SAMPLEA"]},
                        {**self.frames, "SAMPLEB": frame(0.7, "2026-11-16")},
                        {**self.frames, "SAMPLEB": self.frames["SAMPLEB"].iloc[-25:]}):
            with self.subTest(kind=list(altered)):
                risk = calculate_historical_risk(self.holdings, altered, now=NOW)
                self.assertEqual(risk["status"], "UNAVAILABLE")
                pages = PdfReader(io.BytesIO(
                    build_pdf_bytes(self.holdings, risk=risk))).pages
                self.assertEqual(len(pages), 4)

    def test_stale_or_missing_benchmark_must_not_create_beta(self):
        risk = calculate_historical_risk(
            self.holdings, self.frames, benchmark_frame=frame(1.1, "2026-11-16"),
            now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        self.assertIsNone(risk["beta"])
        self.assertIsNone(risk["chart_nifty"])
        self.assertEqual(risk["benchmark_coverage"], 0)
        pages = PdfReader(io.BytesIO(build_pdf_bytes(self.holdings, risk=risk))).pages
        self.assertEqual(len(pages), 5)
        self.assertIn("not verified", pages[4].extract_text())

    def test_mismatched_dates_are_not_forward_filled(self):
        missing = self.frames["SAMPLEB"].iloc[::3]
        risk = calculate_historical_risk(
            self.holdings, {**self.frames, "SAMPLEB": missing}, now=NOW)
        self.assertEqual(risk["status"], "UNAVAILABLE")

    def test_benchmark_subset_has_matching_chart_without_changing_full_risk(self):
        # Later index start and an interior missing date are real omissions,
        # not invented/interpolated benchmark prices. Keep >=65 paired dates.
        benchmark = frame(1.2).iloc[20:].drop(DATES[65])
        full = calculate_historical_risk(self.holdings, self.frames, now=NOW)
        risk = calculate_historical_risk(
            self.holdings, self.frames, benchmark_frame=benchmark, now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        for key in ("coverage", "first_date", "last_date", "annual_vol_pct",
                    "max_drawdown_pct", "current_drawdown_pct", "static_change_pct"):
            self.assertEqual(risk[key], full[key], key)
        expected_dates = [d.date().isoformat() for d in benchmark.index]
        self.assertEqual(risk["chart_dates"], expected_dates)
        self.assertEqual(risk["chart_coverage"], 119)
        self.assertEqual(risk["chart_first_date"], expected_dates[0])
        self.assertEqual(risk["chart_last_date"], expected_dates[-1])
        self.assertEqual(len(risk["chart_portfolio"]), len(risk["chart_nifty"]))
        self.assertEqual(len(risk["chart_nifty"]), 119)
        self.assertEqual(risk["benchmark_coverage"], 119)
        self.assertEqual(risk["chart_portfolio"][0], 100.)
        self.assertEqual(risk["chart_nifty"][0], 100.)
        total = sum(self.frames[h["symbol"]]["Close"] * h["qty"]
                    for h in self.holdings).loc[benchmark.index]
        self.assertAlmostEqual(risk["chart_portfolio"][-1],
                               float(100 * total.iloc[-1] / total.iloc[0]))
        self.assertAlmostEqual(risk["chart_nifty"][-1], float(
            100 * benchmark["Close"].iloc[-1] / benchmark["Close"].iloc[0]))
        self.assertAlmostEqual(risk["chart_portfolio_change_pct"], round(float(
            100 * (total.iloc[-1] / total.iloc[0] - 1)), 2))
        self.assertAlmostEqual(risk["chart_nifty_change_pct"], round(float(
            100 * (benchmark["Close"].iloc[-1] /
                   benchmark["Close"].iloc[0] - 1)), 2))
        self.assertNotEqual(risk["chart_portfolio_change_pct"], risk["static_change_pct"])
        text = PdfReader(io.BytesIO(build_pdf_bytes(self.holdings, risk=risk))).pages[4].extract_text()
        self.assertIn("Nifty 50 (same dates)", text)
        self.assertIn("Chart window: 119 observed sessions", text)
        self.assertIn(expected_dates[0], text)
        self.assertIn("Same chart dates: current holdings", text)
        self.assertIn("140 common observed sessions", text)

    def test_too_short_benchmark_subset_retains_full_portfolio_chart_only(self):
        risk = calculate_historical_risk(
            self.holdings, self.frames, benchmark_frame=frame(1.2).iloc[-64:], now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        self.assertIsNone(risk["chart_nifty"])
        self.assertEqual(risk["chart_coverage"], 140)
        self.assertEqual(risk["chart_first_date"], risk["first_date"])
        self.assertEqual(risk["chart_last_date"], risk["last_date"])
        self.assertIsNone(risk["chart_nifty_change_pct"])

    def test_no_disk_persistence_or_user_holding_in_errors(self):
        from portfolio_doctor_risk_history import _unavailable
        self.assertEqual(_unavailable("Insufficient validated historical series")["status"],
                         "UNAVAILABLE")
        with patch("builtins.open", side_effect=AssertionError("disk read")):
            risk = calculate_historical_risk(self.holdings, self.frames, now=NOW)
            self.assertEqual(risk["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()

