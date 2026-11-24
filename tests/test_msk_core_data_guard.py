import os
os.environ.setdefault("MSK_RESEARCH_ONLY", "1")
import unittest
from datetime import datetime, timezone, date
from unittest.mock import patch
import numpy as np
import pandas as pd
import data_engine as de
import technical_indicators as ti
from msk_core_data_guard import reconcile_fact, wilder_rsi_series
from research_filing_evidence import reconcile_official_quarter

class CoreGuardTests(unittest.TestCase):
    def test_quote_identity_survives_cache(self):
        now = datetime(2026,11,23,5,30,tzinfo=timezone.utc).timestamp()
        raw = {"price":100,"provider_symbol":"SBIN.NS","quote_timestamp":now-60}
        with patch.object(de.time,"time",return_value=now), patch.object(de,"cached_get",return_value=None), patch.object(de,"cached_set") as save, patch.object(de,"_yahoo_v8_quote",return_value=raw):
            q = de.get_verified_quote("SBIN")
        self.assertEqual(q["provider_symbol"],"SBIN.NS")
        cached = save.call_args.args[1]
        with patch.object(de.time,"time",return_value=now), patch.object(de,"cached_get",return_value=cached), patch.object(de,"_yahoo_v8_quote") as fetch:
            self.assertEqual(de.get_verified_quote("SBIN"),q)
            fetch.assert_not_called()
        self.assertIsNone(de._verified_quote_payload(cached,"Yahoo Finance v8","TCS.NS",now_epoch=now))

    def test_preopen_quote_not_current_session(self):
        now = datetime(2026,11,23,3,50,tzinfo=timezone.utc).timestamp()
        raw = {"price":100,"provider_symbol":"SBIN.NS","quote_timestamp":now-600}
        self.assertIsNone(de._verified_quote_payload(raw,"Yahoo Finance v8","SBIN.NS",now_epoch=now))

    def test_same_rsi_and_missing_history(self):
        close = pd.Series([100+np.sin(i)*5+i/4 for i in range(90)])
        self.assertEqual(ti.calc_rsi(close),de.calc_rsi(close))
        self.assertTrue(np.isnan(ti.calc_rsi(close.iloc[:5])))
        self.assertEqual(ti.rsi_label(float("nan")),"UNAVAILABLE")
        self.assertEqual(ti.calc_rsi(pd.Series(range(1,40))),100)
        self.assertEqual(ti.calc_rsi(pd.Series([100.]*40)),50)
        self.assertTrue(np.isnan(ti.calc_rsi(pd.concat([close,pd.Series([np.nan,100])],ignore_index=True))))

    def test_primary_scope_guard(self):
        row = {"label":"Sep 2026","revenue":19644.01,"net_profit":742.98,"scope":"standalone"}
        self.assertEqual(reconcile_official_quarter("DMART",{"quarters":[row]})["status"],"scope_not_comparable")

    def test_field_evidence(self):
        p={"isin":"INE192R01011","metric":"revenue","period_start":"2026-07-01","period_end":"2026-09-30","published":"2026-10-10","unit":"INR crore","scope":"consolidated","source_url":"https://nsearchives.nseindia.com/corporate/example.html","value":"100"}
        today=date(2026,11,24)
        self.assertEqual(reconcile_fact(p,dict(p,value="100.1"),tolerance=".2",today=today),"MATCH")
        self.assertEqual(reconcile_fact(p,dict(p,value="101"),today=today),"MISMATCH")
        self.assertEqual(reconcile_fact(p,dict(p,unit="INR lakh"),today=today),"NOT_COMPARABLE")
        self.assertEqual(reconcile_fact(p,dict(p,value=True),today=today),"MISSING")
        self.assertEqual(reconcile_fact(dict(p,source_url="https://example.com/corporate/x"),p,today=today),"UNAPPROVED_SOURCE")

if __name__=="__main__":
    unittest.main()
