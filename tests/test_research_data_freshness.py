"""Offline quality checks for Indian financial statement date selection."""
import unittest
from datetime import date
from research_data import freshness, _fresher

TODAY = date(2026, 10, 29)


class QuarterlyFreshnessTests(unittest.TestCase):
    def test_trailing_ttm_does_not_hide_valid_june_quarter(self):
        rows = [{"label": "Mar 2026"}, {"label": "Jun 2026"}, {"label": "TTM"}]
        out = freshness(rows, TODAY)
        self.assertEqual(out["status"], "FRESH")
        self.assertEqual(out["latest"], date(2026, 6, 30))
        self.assertEqual(out["label"], "Jun 2026")

    def test_out_of_order_quarters_use_newest_valid_date(self):
        rows = [{"label": "Jun 2026"}, {"label": "Dec 2025"}, {"label": "Mar 2026"}]
        out = freshness(rows, TODAY)
        self.assertEqual(out["latest"], date(2026, 6, 30))
        self.assertEqual(out["status"], "FRESH")

    def test_future_or_unparseable_period_never_counts_as_current(self):
        rows = [{"label": "Dec 2025"}, {"label": "Jun 2027"}, {"label": "TTM"}]
        out = freshness(rows, TODAY)
        self.assertEqual(out["latest"], date(2025, 12, 31))
        self.assertIn(out["status"], ("LAGGING", "STALE"))
        self.assertNotEqual(out["status"], "FRESH")
        absent = freshness([{"label": "TTM"}, {"label": "Jun 2027"}], TODAY)
        self.assertEqual(absent["status"], "MISSING")

    def test_source_selection_does_not_choose_stale_yahoo_over_screener_ttm(self):
        screener = [{"label": "Mar 2026"}, {"label": "Jun 2026"}, {"label": "TTM"}]
        yahoo = [{"label": "Mar-26"}]
        rows, source = _fresher(screener, yahoo)
        self.assertEqual(source, "a")
        self.assertEqual(rows, screener)

    def test_choose_genuinely_newer_source(self):
        screener = [{"label": "Mar 2026"}, {"label": "TTM"}]
        yahoo = [{"label": "Jun-26"}]
        rows, source = _fresher(screener, yahoo)
        self.assertEqual(source, "b")
        self.assertEqual(rows, yahoo)

    def test_no_quarter_evidence_remains_missing(self):
        self.assertEqual(freshness([], TODAY)["status"], "MISSING")


if __name__ == "__main__":
    unittest.main()
