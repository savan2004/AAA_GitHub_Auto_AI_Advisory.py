"""Offline 10-page Doctor improvements: real evidence handoff, not stock advice."""
import io
import unittest
from datetime import datetime
from decimal import Decimal

import pandas as pd
from pypdf import PdfReader

from portfolio_doctor_company_research import prepare_company_pages
from portfolio_doctor_diagnostics import weighted_secondary_portfolio_ratios
from portfolio_doctor_live import IST
from portfolio_doctor_peers import research_industry_peers
from portfolio_doctor_report import _report_records, build_pdf_bytes
from portfolio_doctor_risk_history import calculate_historical_risk
from portfolio_doctor_visual import _trend_label, _financial_metric_label


NOW = datetime(2026, 11, 23, 14, 41, tzinfo=IST)
DAY = "2026-11-20"


def history(n=250, base=100):
    days = pd.bdate_range(end=DAY, periods=n)
    prices = [base + .15 * index + (index % 9) * .22 for index in range(n)]
    return pd.DataFrame({
        "Open": [p * .999 for p in prices],
        "High": [p * 1.01 for p in prices],
        "Low": [p * .99 for p in prices],
        "Close": prices,
        "Volume": [120000 + i * 17 for i in range(n)],
    }, index=days)


def holding(symbol="SAMPLEA", *, frame=None, qty=5, buy=80):
    frame = frame if frame is not None else history()
    close = float(frame["Close"].iloc[-1])
    return {"symbol": symbol, "qty": qty, "buy": buy, "close": close,
            "date": DAY, "ema20": close - 3, "ema50": close + 2,
            "rsi14": 68, "high20": close + 4, "low20": close - 9}


def financials(*, pe=12.3, roe=15.4):
    return {
        "status": "PARTIAL", "source": "Screener.in (secondary)",
        "scope": "consolidated", "retrieved_utc": "2026-11-23T09:11:24Z",
        "annual_period": "2026-03-31",
        "metrics": {"pe": {"value": pe, "period": None},
                    "roe": {"value": roe, "period": "not supplied"}},
    }


def text_pages(raw):
    return [page.extract_text() or "" for page in
            PdfReader(io.BytesIO(raw), strict=True).pages]


class DoctorReportUpgradeTests(unittest.TestCase):
    def test_undated_metrics_never_print_not_supplied_or_invent_ttm(self):
        row = holding()
        company = prepare_company_pages(
            [row], {"SAMPLEA": history()},
            fundamentals={"SAMPLEA": financials()},
            industries={"SAMPLEA": "Consumer Services"}, now=NOW)[0]
        self.assertEqual(company["financial"]["status"], "SECONDARY")
        metrics = {m["label"]: m for m in company["financial"]["metrics"]}
        self.assertIsNone(metrics["P/E"]["period"])
        self.assertIsNone(metrics["ROE"]["period"])
        self.assertEqual(_financial_metric_label(metrics["P/E"]), "P/E: 12.30 x")
        pages = text_pages(build_pdf_bytes([row], companies=[company]))
        self.assertEqual(len(pages), 5)
        self.assertIn("P/E: 12.30 x", pages[-1])
        self.assertIn("ROE: 15.40 %", pages[-1])
        self.assertNotIn("(not supplied)", pages[-1])
        self.assertIn("Portfolio P/E: 12.30x", pages[0])
        self.assertIn("Portfolio ROE: 15.40%", pages[0])
        self.assertIn("Fundamentals: Screener.in secondary data", pages[3])
        self.assertNotIn("Company fundamentals, valuation, governance: NOT VERIFIED",
                         pages[3])
        self.assertNotIn("DATA BOUNDARIES", pages[0])
        self.assertIn("Not SEBI-registered investment advice", pages[3])

    def test_page_three_unqualified_verified_comparison_reaches_company_page(self):
        row, frame = holding(), history()
        company = prepare_company_pages(
            [row], {"SAMPLEA": frame},
            industries={"SAMPLEA": "Consumer Services"}, now=NOW)[0]
        window = company["return_window"]
        peer = {"symbol": "PEERONLY", "industry": "Consumer Services",
                "close": 140.0, "return20": 4.2, "rsi14": 56.1,
                "date": DAY, "return_window": window}
        peers = {"status": "SCREENED", "universe": 500, "pool": 17,
                 "screened": 17, "fresh": 16, "qualified": 0,
                 "candidates": [], "comparisons": [peer],
                 "held_industries": {"SAMPLEA": "Consumer Services"},
                 "failure_counts": {"MODERATE_TREND_MOMENTUM_LIQUIDITY_RISK": 16},
                 "industry_coverage": [{
                     "industry": "Consumer Services", "pool": 17,
                     "attempted": 17, "fresh": 16, "qualified": 0}]}
        pages = text_pages(build_pdf_bytes([row], companies=[company], peers=peers))
        self.assertEqual(len(pages), 5)
        self.assertIn("PEERONLY", pages[2])
        self.assertIn("PEERONLY", pages[-1])
        self.assertIn("Verified comparison; NOT qualified", pages[-1])
        self.assertIn("Gate failures:", pages[2])
        bad = dict(peer, return_window=tuple(["2026-11-19"] * 21))
        peers["comparisons"] = [bad]
        page = text_pages(build_pdf_bytes([row], companies=[company], peers=peers))[-1]
        self.assertNotIn("PEERONLY", page)
        self.assertIn("No verified same-industry peer shares all 21 closing dates.", page)

    def test_verified_ema200_and_buy_reference_with_exact_mixed_trend(self):
        frame = history()
        row = holding(frame=frame, buy=220)
        company = prepare_company_pages(
            [row], {"SAMPLEA": frame}, now=NOW)[0]
        self.assertEqual(len(company["chart_ema200"]), 160)
        self.assertIsNotNone(company["ema200"])
        pages = text_pages(build_pdf_bytes([row], companies=[company]))
        self.assertEqual(len(pages), 5)
        self.assertIn("EMA200", pages[-1])
        self.assertIn("Avg buy Rs 220", pages[-1])
        self.assertEqual(_trend_label(150, 140, 145, 170),
                         "Above EMA20/50 | EMA20<EMA50 | Close below EMA200")
        self.assertNotIn("Mixed / insufficient trend confirmation", pages[1])

    def test_weighted_ratios_exclude_missing_and_show_value_coverage(self):
        rows = _report_records([
            {"symbol": "SAMPLEA", "qty": 1, "buy": 40, "close": 100,
             "date": DAY, "ema20": 95, "ema50": 94, "rsi14": 50,
             "high20": 101, "low20": 90},
            {"symbol": "SAMPLEB", "qty": 3, "buy": 40, "close": 100,
             "date": DAY, "ema20": 95, "ema50": 94, "rsi14": 50,
             "high20": 101, "low20": 90},
        ])
        evidence = [
            {"symbol": "SAMPLEA", "financial": {
                "status": "SECONDARY", "source": "Screener.in (secondary)",
                "issuer_verified": False,
                "metrics": [{"label": "P/E", "value": 10},
                            {"label": "ROE", "value": 8}]}},
            {"symbol": "SAMPLEB", "financial": {
                "status": "SECONDARY", "source": "Screener.in (secondary)",
                "issuer_verified": False,
                "metrics": [{"label": "P/E", "value": 30},
                            {"label": "ROE", "value": None}]}},
        ]
        values = weighted_secondary_portfolio_ratios(rows, evidence)
        self.assertAlmostEqual(values["pe"]["value"], 25)
        self.assertAlmostEqual(values["pe"]["coverage_pct"], 100)
        self.assertAlmostEqual(values["roe"]["value"], 8)
        self.assertAlmostEqual(values["roe"]["coverage_pct"], 25)
        self.assertIsNone(weighted_secondary_portfolio_ratios(rows, [])["pe"])
        evidence[0]["financial"]["metrics"][0]["value"] = float("nan")
        values = weighted_secondary_portfolio_ratios(rows, evidence)
        self.assertAlmostEqual(values["pe"]["coverage_pct"], 75)

    def test_screen_attempts_all_151_and_prioritizes_biggest_industry(self):
        members = {"TOPHOLD": "Consumer Services",
                   "LOWHOLD": "Financial Services"}
        members.update({"TOP{:03d}".format(i): "Consumer Services"
                        for i in range(93)})
        members.update({"LOW{:03d}".format(i): "Financial Services"
                        for i in range(58)})
        members.update({"OUT{:03d}".format(i): "Other"
                        for i in range(347)})
        self.assertEqual(len(members), 500)
        captured = []
        def fetch(symbols):
            captured.extend(symbols)
            return {}  # No market data means zero fresh; never fabricated.
        result = research_industry_peers(
            [{"symbol": "TOPHOLD", "value": Decimal("700000")},
             {"symbol": "LOWHOLD", "value": Decimal("100000")}],
            members=members, history_fetcher=fetch, now=NOW)
        self.assertEqual(result["pool"], 151)
        self.assertEqual(result["screened"], 151)
        self.assertEqual(len(captured), 151)
        self.assertTrue(captured[0].startswith("TOP"))
        self.assertEqual(result["industry_coverage"][0]["industry"], "Consumer Services")
        self.assertEqual(result["industry_coverage"][0]["attempted"], 93)
        self.assertEqual(result["failure_counts"]["MISSING_OR_STALE_COMPLETED_OHLCV"], 151)
        self.assertEqual(result["fresh"], 0)
        self.assertEqual(result["qualified"], 0)


    def test_full_five_holding_report_keeps_ten_pages_with_dense_peer_results(self):
        symbols = ("SAMPLEA", "SAMPLEB", "SAMPLEC", "SAMPLED", "SAMPLEE")
        industries = ("Consumer Services", "Financial Services", "Consumer Durables",
                      "Oil Gas & Consumable Fuels", "Capital Goods")
        frames = {sym: history(base=100 + i * 40)
                  for i, sym in enumerate(symbols)}
        rows = [holding(sym, frame=frames[sym], qty=i+1)
                for i, sym in enumerate(symbols)]
        dossier = prepare_company_pages(
            rows, frames, industries=dict(zip(symbols, industries)), now=NOW)
        risk = calculate_historical_risk(rows, frames, now=NOW)
        self.assertEqual(risk["status"], "VERIFIED")
        candidates = [
            {"symbol": "QUAL{:02d}".format(i), "industry": industries[i % 5],
             "score": 75.0 + i, "date": DAY, "close": 200.0 + i,
             "return20": 3.0 + i, "return_window": dossier[i % 5]["return_window"]}
            for i in range(12)]
        comparisons = [
            {"symbol": "COMP{:02d}".format(i), "industry": industries[i % 5],
             "close": 220.0 + i, "return20": 1.0 + i, "rsi14": 60.0,
             "date": DAY, "return_window": dossier[i % 5]["return_window"]}
            for i in range(12)]
        peers = {
            "status": "SCREENED", "universe": 501, "pool": 151,
            "screened": 151, "fresh": 147, "qualified": 12,
            "held_industries": dict(zip(symbols, industries)),
            "candidates": candidates, "comparisons": comparisons,
            "industry_coverage": [
                {"industry": industry, "pool": 31 if i == 0 else 30,
                 "attempted": 31 if i == 0 else 30, "fresh": 29,
                 "qualified": 2}
                for i, industry in enumerate(industries)],
        }
        pages = text_pages(build_pdf_bytes(rows, risk=risk,
                                           companies=dossier, peers=peers))
        self.assertEqual(len(pages), 10)
        self.assertIn("Page 3 of 10", pages[2])
        self.assertIn("Page 10 of 10", pages[9])
        self.assertIn("SCREEN COVERAGE", pages[2])
        self.assertIn("SAME-INDUSTRY MOMENTUM COMPARISON", pages[-1])
        self.assertNotIn("CONTINUED", "\n".join(pages))



if __name__ == "__main__":
    unittest.main()
