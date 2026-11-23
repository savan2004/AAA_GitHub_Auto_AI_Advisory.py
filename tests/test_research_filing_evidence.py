"""Offline NSE-filing and trading-date provenance contract tests.

Uses official filing values observed on 2026-11-23; no network, no real
production order, and no inference that the full financial report is audited.
"""
from datetime import date
import unittest

import pandas as pd

from research_filing_evidence import _period, reconcile_official_quarter
from research_engine import _date_aligned_returns, secondary_trailing_eps


class NSEQuarterEvidenceTests(unittest.TestCase):
    @staticmethod
    def quarters():
        return [
            {"label": "Dec 2025", "eps": 13.15},
            {"label": "Mar 2026", "eps": 10.07},
            {"label": "Jun 2026", "eps": 13.19},
            {"label": "Sep 2026", "eps": 11.40,
             "revenue": 19644, "net_profit": 743},
        ]

    def test_period_parser_accepts_only_explicit_quarter_end(self):
        self.assertEqual(_period("Sep 2026"), "2026-09-30")
        self.assertEqual(_period("Mar 2026"), "2026-03-31")
        for label in ("TTM", "2026-09-30", "Nov 2026", None, ""):
            self.assertIsNone(_period(label))

    def test_primary_nse_crosscheck_is_dated_and_field_limited(self):
        out = reconcile_official_quarter("DMART", {"quarters": self.quarters()})
        self.assertEqual(out["status"], "partial_match")
        self.assertEqual([x["field"] for x in out["matched"]],
                         ["revenue", "net_profit", "eps"])
        self.assertEqual(out["scope"], "consolidated")
        self.assertEqual(out["assurance"], "unaudited / limited review")
        self.assertEqual(out["period_end"], "2026-09-30")
        self.assertEqual(out["filed"], "2026-10-10")
        self.assertEqual(out["exchange_debt_equity"], 0.16)
        self.assertIn("nsearchives.nseindia.com", out["source_url"])

    def test_any_material_disagreement_withholds_reconciliation(self):
        qs = self.quarters()
        qs[-1]["revenue"] = 19206.18  # Standalone, not consolidated.
        out = reconcile_official_quarter("DMART", {"quarters": qs})
        self.assertEqual(out["status"], "mismatch")
        self.assertEqual(out["mismatched"][0]["field"], "revenue")

    def test_future_or_unknown_quarters_cannot_pass_historical_snapshot(self):
        qs = self.quarters()
        qs[-1]["label"] = "Dec 2026"
        self.assertEqual(reconcile_official_quarter(
            "DMART", {"quarters": qs})["status"], "period_not_comparable")
        self.assertEqual(reconcile_official_quarter(
            "DMART", {})["status"], "secondary_unavailable")
        self.assertEqual(reconcile_official_quarter(
            "STRONGCO", {"quarters": qs})["status"], "not_registered")

    def test_trailing_eps_from_four_consecutive_reported_quarters(self):
        eps = secondary_trailing_eps(self.quarters(), last_price=3406.80,
                                    trailing_pe=71.20)
        self.assertEqual(eps, 47.81)
        qs = self.quarters()
        qs[2]["label"] = "Mar 2026"
        self.assertIsNone(secondary_trailing_eps(qs, last_price=3406.80,
                                                trailing_pe=71.20))
        self.assertIsNone(secondary_trailing_eps(self.quarters(), last_price=300,
                                                trailing_pe=71.20))
        qs = self.quarters()
        qs[0]["eps"] = None
        self.assertIsNone(secondary_trailing_eps(qs, last_price=3406.8,
                                                trailing_pe=71.2))

    def test_benchmark_returns_share_exact_completed_closing_dates(self):
        dates = pd.bdate_range(end="2026-11-20", periods=247)
        stock = pd.Series([100 + x for x in range(247)], index=dates)
        nifty = pd.Series([200 + x * 0.2 for x in range(246)], index=dates[1:])
        aligned = _date_aligned_returns(stock, nifty, 250)
        self.assertIsNotNone(aligned)
        self.assertAlmostEqual(aligned[0], round((346/101-1)*100, 1))
        self.assertAlmostEqual(aligned[1], round((249/200-1)*100, 1))
        self.assertAlmostEqual(aligned[2], round(aligned[0]-aligned[1], 1), delta=0.1)
        # Stale benchmark: NEVER compare stock's newest close to yesterday's index.
        self.assertIsNone(_date_aligned_returns(stock, nifty.iloc[:-1], 250))


if __name__ == "__main__":
    unittest.main()
