"""Private research appendix: no fake prices, issuer numbers or excessive pages."""
import io
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import portfolio_doctor_live as doctor

import pandas as pd
from pypdf import PdfReader

from portfolio_doctor_company_research import (
    prepare_company_pages, _financial_evidence,
)
from portfolio_doctor_live import IST
from portfolio_doctor_report import build_pdf_bytes
from portfolio_doctor_risk_history import calculate_historical_risk

NOW = datetime(2026, 11, 18, 16, 15, tzinfo=IST)


def prices(scale=1):
    days = pd.bdate_range(end="2026-11-17", periods=150)
    close = [100 + .22 * scale * i + .8 * (i % 7) for i in range(150)]
    return pd.DataFrame(
        {"Open": [x * .995 for x in close],
         "High": [x * 1.01 for x in close],
         "Low": [x * .98 for x in close],
         "Close": close,
         "Volume": [100000 + i * 12 for i in range(150)]},
        index=days)


def holding(symbol, frame):
    close = float(frame["Close"].iloc[-1])
    return {"symbol": symbol, "qty": 5, "buy": 80, "close": close,
            "date": "2026-11-17", "ema20": close-3, "ema50": close-7,
            "rsi14": 56, "high20": close+2, "low20": close-14}


class CompanyResearchAcceptance(unittest.TestCase):
    def setUp(self):
        self.frames = {
            sym: prices(i+1) for i, sym in enumerate(
                ("SAMPLEA", "SAMPLEB", "SAMPLEC", "SAMPLED", "SAMPLEE"))
        }
        self.rows = [holding(sym, df) for sym, df in self.frames.items()]

    def test_five_verified_dossiers_and_static_risk_make_ten_pdf_pages(self):
        evidence = prepare_company_pages(self.rows, self.frames, now=NOW)
        self.assertEqual(len(evidence), 5)
        self.assertTrue(all(item["status"] == "VERIFIED" for item in evidence))
        for item in evidence:
            self.assertEqual(len(item["chart"]), 150)
            self.assertEqual(len(item["chart_ema20"]), 150)
            self.assertEqual(len(item["chart_ema50"]), 150)
            self.assertEqual(item["chart_dates"][-1], "2026-11-17")
            self.assertGreater(item["annual_vol_pct"], 0)
            self.assertEqual(item["financial"]["status"], "UNAVAILABLE")
        risk = calculate_historical_risk(self.rows, self.frames, now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        raw = build_pdf_bytes(self.rows, companies=evidence, risk=risk)
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        self.assertEqual(len(pages), 10)
        whole = "\n".join(p.extract_text() for p in pages)
        for sym in self.frames:
            self.assertIn("COMPANY RESEARCH: " + sym, whole)
        self.assertIn("DAILY PRICE, MOVING AVERAGES AND VOLUME", whole)
        self.assertIn("Peer valuation and financial quality are unverified.", whole)
        self.assertNotIn("High-conviction constructive", whole)
        self.assertIn("Page 10 of 10", pages[-1].extract_text())

    def test_parent_only_accepts_large_pdf_with_trailing_company_dossiers(self):
        dossiers = prepare_company_pages(self.rows, self.frames, now=NOW)
        risk = calculate_historical_risk(self.rows, self.frames, now=NOW)
        full_pdf = build_pdf_bytes(self.rows, companies=dossiers, risk=risk)
        mock_run = SimpleNamespace(returncode=0, stdout=full_pdf, stderr=b"")
        with patch.object(doctor.subprocess, "run", return_value=mock_run), \
             patch.dict(doctor.os.environ,
                        {"MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED": "0"}):
            self.assertEqual(doctor.run_isolated_memory(
                "typed.csv", b"SAMPLEA 2 100"), full_pdf)

    def test_incomplete_or_wrong_ticker_history_is_not_presented_as_verified(self):
        source = self.rows[0]
        for frame in (self.frames["SAMPLEA"].iloc[:-1],
                      prices(18), self.frames["SAMPLEA"].iloc[-30:]):
            with self.subTest(last=len(frame)):
                dossiers = prepare_company_pages(
                    [source], {"SAMPLEA": frame}, now=NOW)
                self.assertEqual(dossiers[0]["status"], "UNAVAILABLE")
                data = build_pdf_bytes([source], companies=dossiers)
                pages = PdfReader(io.BytesIO(data)).pages
                self.assertEqual(len(pages), 5)
                self.assertIn("TECHNICAL EVIDENCE UNAVAILABLE",
                              pages[-1].extract_text())

    def test_secondary_financials_are_period_scoped_and_not_audited(self):
        e = {"status": "PARTIAL", "source": "Screener.in (secondary)",
             "scope": "consolidated", "retrieved_utc": "2026-11-18T11:30:00Z",
             "annual_period": "2026-03-31",
             "metrics": {
                "revenue": {"value": 1234, "period": "2026-03-31"},
                "roe": {"value": 19, "period": "not supplied"},
                "pe": {"value": None, "period": "not supplied"},
             }}
        dossier = prepare_company_pages(
            [self.rows[0]], {"SAMPLEA": self.frames["SAMPLEA"]},
            fundamentals={"SAMPLEA": e},
            industries={"SAMPLEA": "Synthetic industry"}, now=NOW)[0]
        self.assertEqual(dossier["financial"]["status"], "SECONDARY")
        self.assertFalse(dossier["financial"]["issuer_verified"])
        self.assertEqual(len(dossier["financial"]["metrics"]), 2)
        pages = PdfReader(io.BytesIO(
            build_pdf_bytes([self.rows[0]], companies=[dossier]))).pages
        self.assertEqual(len(pages), 5)
        txt = pages[-1].extract_text()
        self.assertIn("Revenue:", txt)
        self.assertIn("Issuer filing reconciliation: NO", txt)
        self.assertIn("Synthetic industry", txt)
        self.assertNotIn("P/E: 0.00", txt)

    def test_wrong_financial_source_or_scope_fails_closed(self):
        bad = {"status": "PARTIAL", "source": "Unverified secondary financial data",
               "scope": "unverified", "retrieved_utc": "x",
               "metrics": {"revenue": {"value": 100, "period": "unknown"}}}
        self.assertEqual(_financial_evidence(bad)["status"], "UNAVAILABLE")
        self.assertEqual(_financial_evidence({})["metrics"], [])
        self.assertEqual(_financial_evidence({"status": "COMPLETE"})["metrics"], [])


if __name__ == "__main__":
    unittest.main()
