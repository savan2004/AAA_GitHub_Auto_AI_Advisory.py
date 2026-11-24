import os
os.environ.setdefault("MSK_RESEARCH_ONLY","1")
import unittest
from datetime import date
from unittest.mock import patch
import fundamentals as f
import research_data as rd
from financial_quality import normalize_dividend_percent

class FundamentalGuardTests(unittest.TestCase):
    def test_explicit_dividend_units(self):
        self.assertEqual(normalize_dividend_percent(.008,"fraction"),.8)
        self.assertEqual(normalize_dividend_percent(.8,"percent"),.8)
        self.assertEqual(normalize_dividend_percent(0,"fraction"),0)
        self.assertIsNone(normalize_dividend_percent(.8,None))

    def test_dated_revenue_beats_undated_and_invalid_amounts_are_missing(self):
        info={"totalRevenue":999999999,"market_cap":float("inf"),
              "dividend_yield":.008,"dividend_yield_unit":"fraction"}
        sc={"rev":130000000,"_revenue_period_end":"2026-03-31",
            "_screener_scope":"consolidated"}
        with patch.object(f,"_get_cached",return_value=None),patch.object(f,"_set_cached"),patch("data_engine.get_info",return_value=info),patch.object(f,"_fetch_screener",return_value=sc),patch.object(f,"_fetch_finnhub",return_value=None),patch.object(f,"_fetch_yfinance",return_value={"marketCap":True,"trailingEps":-2}):
            result=f.get_fundamentals("DMART")
        self.assertEqual(result["rev"],130000000)
        self.assertEqual(result["_field_provenance"]["rev"]["period_end"],"2026-03-31")
        self.assertEqual(result["div_y"],.8)
        self.assertEqual(result["eps"],-2)
        self.assertIsNone(result["mcap"])
        self.assertFalse(result["_issuer_filing_verified"])

    def test_financial_columns_must_align_and_nonfinite_is_missing(self):
        header,rows=rd._table_to_rows([["","Mar 2025","Mar 2026"],["Sales","100"],["Net Profit","5","inf"]])
        self.assertNotIn("Sales",rows)
        self.assertEqual(rows["Net Profit"],[5,None])
        self.assertIsNone(rd._cr(float("inf")))

    def test_future_and_conflicting_rows_not_selected(self):
        old={"label":"Jun 2026","revenue":100,"net_profit":10}
        future={"label":"Dec 2027","revenue":999,"net_profit":100}
        current={"label":"Sep 2026","revenue":120,"net_profit":12}
        with patch.object(rd,"_cget",return_value=None),patch.object(rd,"_cset"),patch.object(rd,"get_screener_extras",return_value={"quarters":[old,future]}),patch.object(rd,"_yahoo_statements",return_value={"years":[],"quarters":[current]}):
            result=rd.get_statements("DMART",today=date(2026,11,24))
        self.assertEqual(result["quarters"],[current])
        self.assertEqual(result["sources"]["quarters"],"Yahoo Finance")
