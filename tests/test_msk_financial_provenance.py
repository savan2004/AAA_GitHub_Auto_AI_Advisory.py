"""No-network financial-period/source audit regressions for MSK Bot.

Synthetic P&L HTML; no paid API, Telegram secrets or actual company filings.
Tests must never infer a filing revision, fiscal scope, or issuer approval.
"""
import ast
import json
import logging
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def financial_table(*, headings=("Mar 2025", "Mar 2026", "TTM"),
                    values=("100", "130", "180")):
    head = "".join("<th>" + value + "</th>" for value in ("", *headings))
    cells = "".join("<td>" + value + "</td>" for value in ("Sales", *values))
    return (f'<section id="profit-loss"><table><thead><tr>{head}</tr></thead>'
            f'<tbody><tr>{cells}</tr></tbody></table></section>'
            f'<section id="balance-sheet"></section>')


class AnnualRevenueBasisTests(unittest.TestCase):
    def test_undated_ttm_value_never_replaces_march_fy(self):
        from financial_quality import annual_sales_from_table
        html = financial_table()
        result = annual_sales_from_table(html, today=date(2026, 11, 12))
        self.assertEqual(result["rev"], 1_300_000_000)
        self.assertEqual(result["period_end"], "2026-03-31")
        self.assertEqual(result["period_basis"], "FY")
        self.assertEqual(result["units"], "INR")
        self.assertNotEqual(result["rev"], 1_800_000_000)

    def test_unmatched_columns_undated_and_old_periods_fail_closed(self):
        from financial_quality import annual_sales_from_table
        self.assertIsNone(annual_sales_from_table(
            financial_table(headings=("TTM",), values=("300",)),
            today=date(2026, 11, 12)))
        self.assertIsNone(annual_sales_from_table(
            financial_table(headings=("Mar 2026",), values=("130", "999")),
            today=date(2026, 11, 12)))
        self.assertIsNone(annual_sales_from_table(
            financial_table(headings=("Mar 2023",), values=("130",)),
            today=date(2026, 11, 12)))
        self.assertIsNone(annual_sales_from_table(
            financial_table(headings=("Mar 2027",), values=("130",)),
            today=date(2026, 11, 12)))
        self.assertIsNone(annual_sales_from_table(
            financial_table(headings=("Mar 2026",), values=("-20",)),
            today=date(2026, 11, 12)))
        self.assertIsNone(annual_sales_from_table(
            "<table><tr><td>Revenue</td><td>123</td></tr></table>",
            today=date(2026, 11, 12)))

    def test_screener_revenue_requires_date_and_scope_is_explicit(self):
        import fundamentals as f
        class Response:
            def __init__(self, status_code, text=""):
                self.status_code = status_code
                self.text = text
                self.ok = status_code == 200
        with patch.object(f.requests, "get",
                          return_value=Response(200, financial_table())):
            consolidated = f._fetch_screener("SBIN")
        self.assertEqual(consolidated["_screener_scope"], "consolidated")
        self.assertEqual(consolidated["_revenue_period_end"], "2026-03-31")
        self.assertEqual(consolidated["rev"], 1_300_000_000)
        with patch.object(f.requests, "get", side_effect=[
                Response(404), Response(200, financial_table())]):
            standalone = f._fetch_screener("SBIN")
        self.assertEqual(standalone["_screener_scope"], "standalone")
        self.assertEqual(standalone["rev"], 1_300_000_000)
        with patch.object(f.requests, "get", return_value=Response(
                200, financial_table(headings=("TTM",), values=("300",)))):
            self.assertIsNone(f._fetch_screener("SBIN"))


class FieldOriginTests(unittest.TestCase):
    def test_fundamentals_keep_provider_scope_and_period_per_field(self):
        import fundamentals as f
        info = {"roe": 0.12, "eps": None, "totalRevenue": None}
        screener = {
            "rev": 1_300_000_000,
            "_screener_scope": "consolidated",
            "_revenue_period_end": "2026-03-31",
        }
        with patch.object(f, "_get_cached", return_value=None), \
             patch.object(f, "_set_cached") as cache, \
             patch("data_engine.get_info", return_value=info), \
             patch.object(f, "_fetch_screener", return_value=screener), \
             patch.object(f, "_fetch_finnhub", return_value={"eps": 12.5}), \
             patch.object(f, "_fetch_yfinance", return_value={}):
            result = f.get_fundamentals("SBIN")
        self.assertEqual(result["roe"], 12.0)
        self.assertEqual(result["rev"], 1_300_000_000)
        self.assertEqual(result["eps"], 12.5)
        origins = result["_field_provenance"]
        self.assertEqual(origins["roe"], {
            "source": "data_engine (mixed)",
            "scope": "unknown", "period_end": None})
        self.assertEqual(origins["eps"]["source"], "Finnhub")
        self.assertEqual(origins["rev"], {
            "source": "Screener.in", "scope": "consolidated",
            "period_end": "2026-03-31"})
        self.assertIs(result["_issuer_filing_verified"], False)
        self.assertTrue(cache.call_args.args[0].startswith("fund_provenance_v1_"))

    def test_telegram_summary_never_claims_issuer_filing_certification(self):
        from financial_quality import financial_source_summary
        sample = {
            "rev": 1_300_000_000, "eps": 12.5,
            "_field_provenance": {
                "rev": {"source": "Screener.in", "scope": "standalone",
                        "period_end": "2026-03-31"},
                "eps": {"source": "Finnhub", "scope": "unknown",
                        "period_end": None},
            }
        }
        summary = financial_source_summary(sample)
        self.assertIn("Revenue: Screener.in (2026-03-31, standalone)", summary)
        self.assertIn("EPS: Finnhub (period unknown, scope unknown)", summary)
        self.assertIn("NOT NSE filing-audited", summary)
        self.assertIn("must not be treated as directly comparable", summary)
        self.assertIn("not verified", financial_source_summary({}))

    def test_fundamental_and_full_analysis_cards_display_verification_warning(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name in ("build_adv", "build_fundamental_text"):
            fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                      and x.name == name)
            source_text = ast.get_source_segment(source, fn)
            with self.subTest(name=name):
                self.assertIn("financial_source_summary(fund)", source_text)


if __name__ == "__main__":
    unittest.main()
