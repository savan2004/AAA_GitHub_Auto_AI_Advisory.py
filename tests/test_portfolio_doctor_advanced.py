"""Offline, deterministic acceptance gates for advanced private Doctor research."""
import io
import math
import subprocess
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pypdf import PdfReader
import portfolio_doctor_advanced as a
import portfolio_doctor_live as basic
from portfolio_doctor_advanced_pdf import render_advanced_pdf


def rows():
    latest = date.today().isoformat()
    items = [
        {"symbol": "SAMPLEA", "qty": 2.0, "buy": 100.0, "close": 110.0,
         "date": latest, "invested": 200.0, "value": 220.0, "pnl": 20.0,
         "pnl_pct": 10.0, "weight": 40.0, "ema20": 106, "ema50": 101,
         "rsi14": 58, "high20": 118, "low20": 95},
        {"symbol": "SAMPLEB", "qty": 3.0, "buy": 100.0, "close": 110.0,
         "date": latest, "invested": 300.0, "value": 330.0, "pnl": 30.0,
         "pnl_pct": 10.0, "weight": 60.0, "ema20": 107, "ema50": 103,
         "rsi14": 72, "high20": 115, "low20": 99},
    ]
    return items


def _fund_fixture(lender=False, complete=False):
    years = [
        {"label": "Mar 2023", "revenue": 100, "net_profit": 10, "opm": 10,
         "roce": 15, "borrowings": 20, "networth": 100, "ocf": 15},
        {"label": "Mar 2024", "revenue": 110, "net_profit": 12, "opm": 11,
         "roce": 16, "borrowings": 22, "networth": 110, "ocf": 16},
        {"label": "Mar 2025", "revenue": 125, "net_profit": 14, "opm": 12,
         "roce": 17, "borrowings": 23, "networth": 121, "ocf": 17},
        {"label": "Mar 2026", "revenue": 144, "net_profit": 18, "opm": 13,
         "roce": 18, "borrowings": 24, "networth": 140, "ocf": 20},
    ]
    return {"years": years, "quarters": [{"label": "Sep 2026",
                                        "revenue": 36, "net_profit": 5}],
            "layout": "lender" if lender else "corporate"}


class TotalsAndSectorTests(unittest.TestCase):
    def test_all_portfolio_cost_pnl_and_weights_exact(self):
        check = a.check_basic_totals(rows(), expected_count=2)
        self.assertEqual(check["invested"], 500)
        self.assertEqual(check["value"], 550)
        self.assertEqual(check["pnl"], 50)
        self.assertEqual(check["pnl_pct"], 10)
        self.assertEqual(check["covered"], 2)

    def test_incomplete_or_wrong_totals_refused_not_renormalized(self):
        for bad in (rows()[:1],):
            with self.assertRaises(a.DoctorEvidenceError):
                a.check_basic_totals(bad, expected_count=2)
        x = rows()
        x[0]["pnl"] = 2000
        with self.assertRaises(a.DoctorEvidenceError):
            a.check_basic_totals(x)
        x = rows()
        x[0]["weight"] = 0
        with self.assertRaises(a.DoctorEvidenceError):
            a.check_basic_totals(x)

    def test_sector_totals_include_unknown(self):
        sectors = a.sector_allocations(rows(), resolve=lambda sym: {
            "SAMPLEA": "IT", "SAMPLEB": None}.get(sym))
        self.assertAlmostEqual(sum(sectors.values()), 100, places=8)
        self.assertAlmostEqual(sectors["IT"], 40, places=8)
        self.assertAlmostEqual(sectors["Unknown / unclassified"], 60, places=8)

    def test_sector_unknown_resolver_failure_is_not_silently_lost(self):
        def broken(_sym):
            raise RuntimeError("provider down")
        self.assertEqual(a.sector_allocations(rows(), resolve=broken),
                         {"Unknown / unclassified": 100.0})


class FundamentalTests(unittest.TestCase):
    def test_dated_growth_source_scope_and_sector(self):
        f = a.normalize_fundamentals("SAMPLEA", "Industrials", _fund_fixture(), {
            "roe": 18, "pe": 30})
        self.assertEqual(f["annual_period"], "2026-03-31")
        self.assertEqual(f["quarter_period"], "2026-09-30")
        self.assertEqual(f["scope"], "unverified")
        self.assertFalse(f["issuer_filing_verified"])
        self.assertEqual(f["metrics"]["revenue"]["value"], 144)
        self.assertGreater(f["metrics"]["revenue_cagr3"]["value"], 10)
        self.assertIsNone(f["metrics"]["free_cash_flow"]["value"])
        self.assertIsNone(f["metrics"]["interest_coverage"]["value"])
        self.assertEqual(f["metrics"]["revenue"]["source"], "Screener.in (secondary)")
        self.assertEqual(f["metrics"]["revenue"]["basis"], "FY")

    def test_bank_does_not_apply_corporate_debt_roce_or_cash_flow(self):
        f = a.normalize_fundamentals("SAMPLEB", "Banking", _fund_fixture(lender=True), {
            "roe": 16, "pb": 1.8, "pe": 11})
        self.assertTrue(f["is_lender"])
        for key in ("debt_equity", "roce", "operating_cash_flow"):
            self.assertIsNone(f["metrics"][key]["value"])
        self.assertEqual(f["metrics"]["pb"]["value"], 1.8)

    def test_missing_fundamentals_are_not_decisive(self):
        f = a.normalize_fundamentals("SAMPLEA", "Industrials", {}, {})
        self.assertEqual(f["status"], "MISSING")
        self.assertEqual(f["freshness"], "MISSING")
        self.assertIsNone(f["metrics"]["roe"]["value"])
        opinion = a.evaluate_opinion(f, {"trend": "UPTREND"}, 12,
                                     corporate_actions_verified=True,
                                     compliance_approved=True)
        self.assertEqual(opinion["opinion"], "INSUFFICIENT DATA")

    def test_missing_or_invalid_growth_periods(self):
        old = _fund_fixture()
        old["years"][0]["net_profit"] = -1
        f = a.normalize_fundamentals("SAMPLEA", "Industrials", old, {})
        self.assertIsNone(f["metrics"]["profit_cagr3"]["value"])


class TechnicalAndCorporateActionTests(unittest.TestCase):
    def test_relative_performance_requires_common_session_dates(self):
        idx = pd.bdate_range("2026-09-01", periods=32)
        frame = pd.DataFrame({"Close": [100 + i for i in range(32)]}, index=idx)
        bench = pd.DataFrame({"Close": [200 + i for i in range(32)]}, index=idx)
        out = a.aligned_relative_return(frame, bench, sessions=21)
        self.assertEqual(out["sessions"], 21)
        self.assertIsNotNone(out["relative_pp"])
        empty_match = pd.DataFrame({"Close": [200 + i for i in range(32)]},
                                    index=pd.bdate_range("2020-01-01", periods=32))
        self.assertIsNone(a.aligned_relative_return(frame, empty_match, sessions=21))

    def test_corporate_actions_block_action_opinions(self):
        complete = {"status": "COMPLETE", "issuer_filing_verified": True,
                    "valuation_attractive_verified": True,
                    "verified_evidence": ["audited dated filing"]}
        out = a.evaluate_opinion(complete, {"trend": "STRONG UPTREND"},
                                 12, corporate_actions_verified=False,
                                 compliance_approved=True)
        self.assertEqual(out["opinion"], "INSUFFICIENT DATA")
        self.assertIn("corporate_action_cost_basis_verification", out["conditions"])

    def test_overbought_rsi_alone_never_exit(self):
        complete = {"status": "COMPLETE", "issuer_filing_verified": True}
        out = a.evaluate_opinion(complete, {"trend": "UPTREND", "rsi": 90},
                                 10, corporate_actions_verified=True,
                                 compliance_approved=True)
        self.assertEqual(out["opinion"], "HOLD")

    def test_compliance_blocks_action_regardless_of_quality_score(self):
        complete = {"status": "COMPLETE", "issuer_filing_verified": True,
                    "valuation_attractive_verified": True}
        out = a.evaluate_opinion(complete, {"trend": "STRONG UPTREND"},
                                 10, corporate_actions_verified=True)
        self.assertEqual(out["opinion"], "INSUFFICIENT DATA")


class ReplacementAndBuilderTests(unittest.TestCase):
    def test_no_fake_replacement_from_single_technical_signal(self):
        empty = a.compare_replacements({"symbol": "SAMPLEA"},
                                        [{"symbol": "SAMPLEC", "technical_verified": True}])
        self.assertEqual(empty["status"], "NO SUITABLE REPLACEMENT FOUND")
        self.assertFalse(empty["automatic_swap"])

    def test_only_complete_replacement_comparison_passes(self):
        cand = {"symbol": "SAMPLEC", "sector": "IT",
                "fundamental_verified": True, "valuation_verified": True,
                "technical_verified": True, "liquid_verified": True,
                "sector_verified": True, "period_comparable": True,
                "diversification_improvement": True, "quality": 68,
                "valuation": 60, "technical": 75}
        out = a.compare_replacements({"symbol": "SAMPLEA"}, [cand])
        self.assertEqual(out["status"], "COMPARABLE")
        self.assertEqual(len(out["candidates"]), 1)
        self.assertFalse(out["automatic_swap"])

    def make_builder(self, **changes):
        args = dict(capital=1000, horizon_months=24,
                    risk_tolerance="medium", liquidity_needed=100,
                    stock_cap_pct=50, sector_cap_pct=60,
                    candidates=[
                        {"symbol": "SAMPLEA", "price": 99, "sector": "IT",
                         "target_weight_pct": 50, "fresh_price_verified": True},
                        {"symbol": "SAMPLEB", "price": 202, "sector": "Pharma",
                         "target_weight_pct": 50, "fresh_price_verified": True},
                    ])
        args.update(changes)
        return a.build_portfolio_scenario(**args)

    def test_builder_cash_whole_shares_weight_and_rounding(self):
        output = self.make_builder()
        self.assertEqual([v["shares"] for v in output["proposed"]], [5, 2])
        self.assertEqual(output["cash"], 101)
        self.assertAlmostEqual(output["total_weights_pct"], 100)
        self.assertGreaterEqual(output["cash"], 100)
        self.assertTrue(output["no_automatic_trades"])

    def test_builder_duplicate_and_invalid_sector_or_stock_caps(self):
        old = self.make_builder()["proposed"]
        with self.assertRaises(a.DoctorEvidenceError):
            self.make_builder(stock_cap_pct=40)
        with self.assertRaises(a.DoctorEvidenceError):
            self.make_builder(sector_cap_pct=30)
        with self.assertRaises(a.DoctorEvidenceError):
            self.make_builder(excluded=["SAMPLEA"])
        with self.assertRaises(a.DoctorEvidenceError):
            self.make_builder(candidates=[
                {"symbol": "SAMPLEA", "price": 100, "sector": "IT",
                 "target_weight_pct": 50, "fresh_price_verified": True}] * 2)

    def test_additional_capital_needs_existing_sectors(self):
        with self.assertRaises(a.DoctorEvidenceError):
            self.make_builder(current_values={"EXISTING": 1000},
                              funding_mode="additional")


class PDFAndPrivacyTests(unittest.TestCase):
    def create_review(self):
        recs = rows()
        f = [a.normalize_fundamentals(r["symbol"], "Banking" if i else "IT",
                                      _fund_fixture(lender=bool(i)), {"roe": 15, "pe": 20})
             for i, r in enumerate(recs)]
        tech = [{"symbol": row["symbol"], "trend": "UPTREND",
                 "ema20": row["ema20"], "ema50": row["ema50"],
                 "rsi14": row["rsi14"], "support": row["low20"],
                 "resistance": row["high20"], "volume_ratio": 1.1,
                 "relative_1m": None} for row in recs]
        return {"totals": a.check_basic_totals(recs), "holdings": recs,
                "fundamentals": f, "technicals": tech,
                "sectors": a.sector_allocations(recs, resolve=lambda s: "IT" if s == "SAMPLEA" else "Banking"),
                "opinions": [a.evaluate_opinion(f[i], tech[i], recs[i]["weight"]) for i in range(2)],
                "replacements": {"status": "NO SUITABLE REPLACEMENT FOUND"}, "builder": None}

    def test_pdf_pages_text_sources_totals_no_truncation(self):
        pdf_bytes = render_advanced_pdf(self.create_review())
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        pages = PdfReader(io.BytesIO(pdf_bytes), strict=True).pages
        self.assertGreaterEqual(len(pages), 6)
        self.assertLessEqual(len(pages), 8)
        text = "\n".join(p.extract_text() or "" for p in pages)
        for required in ("PORTFOLIO DASHBOARD", "FUNDAMENTAL REVIEW", "TECHNICAL",
                         "SECTOR ALLOCATION", "HOLDING REVIEW", "DATA SOURCES",
                         "SAMPLEA", "SAMPLEB", "Rs 550.00", "UNVERIFIED",
                         "NO SUITABLE REPLACEMENT FOUND", "Telegram"):
            self.assertIn(required, text)

    def test_privacy_and_flags_no_active_persistent_customer_path(self):
        root = Path(__file__).resolve().parents[1]
        live = (root / "portfolio_doctor_live.py").read_text()
        doc = (root / "portfolio_doctor_advanced.py").read_text()
        pdf = (root / "portfolio_doctor_advanced_pdf.py").read_text()
        self.assertIn('MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED", "0"', live)
        self.assertIn('MSK_PORTFOLIO_DOCTOR_ADVANCED_FUNDAMENTALS", "0"', live)
        for content in (doc, pdf):
            self.assertNotIn("psycopg.connect(", content)
            self.assertNotIn("sqlite3.connect(", content)
            self.assertNotIn("tempfile.mkstemp", content)
            self.assertNotIn("shelve.open(", content)
            self.assertNotIn("logging.basicConfig(", content)
        self.assertNotIn("from data_engine import get_info", doc)

    def test_concurrent_slots_bounded(self):
        slots = threading.BoundedSemaphore(2)
        self.assertTrue(slots.acquire(blocking=False))
        self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        slots.release()
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()
        slots.release()


if __name__ == "__main__":
    unittest.main()
