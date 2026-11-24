import os
os.environ.setdefault("MSK_RESEARCH_ONLY", "1")
import unittest
from datetime import datetime, timezone, date
from unittest.mock import patch
import numpy as np
import pandas as pd
import data_engine as de
import technical_indicators as ti
from msk_core_data_guard import reconcile_fact, wilder_rsi_series, wilder_rsi_values
from portfolio_doctor_calculations import _rsi
from research_filing_evidence import reconcile_official_quarter

class CoreGuardTests(unittest.TestCase):
    def test_disk_cache_promotion_does_not_extend_original_lifetime(self):
        with patch.object(de, "_mem", {}), patch.object(de, "_disk_get",
                return_value={"val": 7, "ts": 80, "ttl": 30}):
            with patch.object(de.time, "time", return_value=100):
                self.assertEqual(de.cached_get("x", 60), 7)
                self.assertEqual(de._mem_get("x", 60), 7)
            with patch.object(de.time, "time", return_value=110):
                self.assertIsNone(de._mem_get("x", 60))

    def test_memory_cache_is_bounded_and_rejects_future_entries(self):
        with patch.object(de, "_mem", {}), patch.object(de, "_MEM_MAX_ENTRIES", 2):
            with patch.object(de.time, "time", return_value=100):
                de._mem_set("a", 1, 60)
                de._mem_set("b", 2, 60)
                de._mem_set("c", 3, 60)
                de._mem_set("future", 4, 60, stored_at=101)
                self.assertEqual(set(de._mem), {"b", "c"})
                self.assertIsNone(de._mem_get("b", 0))

    def test_shared_indicator_series_matches_public_wrappers(self):
        from msk_indicator_math import ema, macd, directional_series, bollinger
        from portfolio_doctor_calculations import portfolio_weights
        close = pd.Series([100+np.sin(i)*5+i/4 for i in range(120)])
        df = pd.DataFrame({"High":close+2,"Low":close-2,"Close":close})
        atr,adx,pdi,mdi=directional_series(df.High,df.Low,df.Close)
        self.assertEqual(ti.calc_ema(close,20),round(float(ema(close,20).iloc[-1]),2))
        self.assertEqual(ti.calc_atr(df),round(float(atr.iloc[-1]),2))
        self.assertEqual(ti.calc_adx(df),tuple(round(float(v.iloc[-1]),1) for v in (adx,pdi,mdi)))
        self.assertEqual(ti.calc_macd(close),tuple(round(float(v.iloc[-1]),2) for v in macd(close)))
        self.assertEqual(ti.calc_bollinger(close),tuple(round(float(v.iloc[-1]),2) for v in bollinger(close)))
        self.assertTrue(np.isnan(directional_series(df.High[:5],df.Low[:5],df.Close[:5])[0].iloc[-1]))
        self.assertEqual(portfolio_weights([{"qty":2,"close":100},{"qty":1,"close":200}]),[50,50])
        with self.assertRaises(ValueError):
            portfolio_weights([{"qty":-1,"close":100},{"qty":2,"close":200}])

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

    def test_shared_rsi_prefix_and_doctor(self):
        closes = [100+np.sin(i)*5+i/4 for i in range(90)]
        full = wilder_rsi_values(closes)
        self.assertTrue(all(v is None for v in full[:14]))
        for n in (15, 30, 60, 90):
            self.assertAlmostEqual(_rsi(closes[:n]), full[n-1], places=10)
            self.assertAlmostEqual(wilder_rsi_series(pd.Series(closes[:n])).iloc[-1], full[n-1], places=10)
        self.assertIsNone(_rsi(closes[:10]))
        self.assertEqual(_rsi([100]*30),50)
        self.assertEqual(_rsi(list(range(40,0,-1))),0)
        self.assertIsNone(_rsi(closes+[None,100]))
        self.assertEqual(_rsi(closes+[None]+[100]*15),50)
        self.assertIsNone(_rsi(closes+[True]))
        self.assertIsNone(_rsi(closes+[float("inf")]))

    def test_strategy_rsi_call_sites(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for name in ("swing_trades.py","msk_intraday_trade.py","investment_screener.py"):
            tree = ast.parse((root/name).read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="wilder_rsi_series"]
            self.assertEqual(len(calls),1,name)

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
