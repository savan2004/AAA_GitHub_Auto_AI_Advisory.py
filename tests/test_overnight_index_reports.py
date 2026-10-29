"""Offline regression suite for full-universe overnight index reports."""
import os
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import Mock, patch

import overnight_index_reports as n
import index_top5 as live


def make_universe(profile="moderate",count=150):
    return {
        "name":{"conservative":"Nifty 100","moderate":"Nifty Midcap 150",
                "aggressive":"Nifty Smallcap 250"}[profile],
        "cap":{"conservative":"Large Cap","moderate":"Mid Cap",
               "aggressive":"Small Cap"}[profile],
        "symbols":tuple("TEST%03d"%i for i in range(count)),
        "industries":{"TEST%03d"%i:"Sector%d"%(i%7) for i in range(count)},
        "source":"Official NSE constituent CSV", "asof":"29 Oct 2026"
    }


def evidence(profile="moderate",status="VALIDATED"):
    return dict(fund_status=status, fund_score=80 if status=="VALIDATED" else 91,
                fund_known=5, fund_reason="qualified" if status=="VALIDATED" else "eps_missing",
                roe=18.2, pe=24.3, de=.13, eps=8.1, quarter="2026-09-30")


def technical(sym,session="2026-10-29",signal="CONDITIONAL BUY",risk=5.):
    return dict(sym=sym,asof=session, tech_score=82, score=82,
                ltp=500.,chg=1.2,adx=27.1,rsi=61.,volx=1.8,
                risk_pct=risk,signal=signal,entry=505.,sl=480.,t1=555.,t2=580.)


class OvernightLedgerTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.store=n.EvidenceStore("sqlite:///"+str(Path(self.t.name)/"ledger.db"))
        self.day=date(2026,10,29)
    def tearDown(self):
        self.store.db.close()
        self.t.cleanup()

    def test_mandatory_full_100_150_250_fundamental_attempts(self):
        for profile,count in (("conservative",100),("moderate",150),("aggressive",250)):
            universe=make_universe(profile,count)
            with patch.object(n,"get_index_universe",return_value=universe), \
                 patch("market_data.bulk_history",return_value={
                    sym:Mock(empty=False) for sym in universe["symbols"]}) as bulk, \
                 patch("top5_technical_ratings.technical_candidate",
                       side_effect=lambda sym,*args:technical(sym)) as candle, \
                 patch("top5_company_ratings.fetch_company_evidence",
                       side_effect=lambda *args:evidence(profile)) as fin:
                result=n.collect_profile(profile,self.store,self.day,delay_s=0)
            self.assertEqual(result["expected"],count)
            self.assertEqual(result["examined"],count)
            self.assertEqual(fin.call_count,count,
                             "Must NOT silently stop at 28 fundamentals")
            self.assertEqual(candle.call_count,count)
            self.assertEqual(len(bulk.call_args.args[0]),count)
            self.assertEqual(len(self.store.rows(self.day,profile)),count)
            with patch.object(n,"get_index_universe",return_value=universe), \
                 patch("market_data.bulk_history") as repeat, \
                 patch("top5_company_ratings.fetch_company_evidence") as duplicated:
                result=n.collect_profile(profile,self.store,self.day,delay_s=0)
                self.assertEqual(result["examined"],count)
                duplicated.assert_not_called()
                repeat.assert_not_called()

    def test_no_ranked_top5_if_even_one_member_not_reviewed(self):
        universe=make_universe("moderate",150)
        digest=n._member_hash(universe)
        for sym in universe["symbols"][:-1]:
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"trade_date":str(self.day),"membership_hash":digest,
                "fund_attempted":True,"fund_status":"VALIDATED",
                "fundamental":evidence(),"technical":technical(sym)
            })
        state,counts,body=n.render_snapshot("moderate",universe,
                                          self.store.rows(self.day,"moderate"),self.day)
        self.assertEqual(state,"PARTIAL")
        self.assertEqual(counts["reviewed"],149)
        self.assertIn("TOP 5 WITHHELD",body.upper())
        self.assertNotIn("1. TEST",body)
        self.assertIn("Not reviewed: 1",body)

    def test_missing_eps_is_unverified_and_never_ranked(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"PRELIMINARY",
                "fundamental":evidence(status="PRELIMINARY"),
                "technical":technical(sym)
            })
        state,stats,body=n.render_snapshot("conservative",universe,
                                             self.store.rows(self.day,"conservative"),self.day)
        self.assertEqual(state,"READY")
        self.assertEqual(stats["validated"],0)
        self.assertIn("No fully rule-validated conditional setups",body)
        self.assertNotIn("1. TEST",body)
        self.assertIn("Provisional 100",body)

    def test_current_complete_rule_gated_watchlist_and_risk_limit(self):
        universe=make_universe("aggressive",250)
        h=n._member_hash(universe)
        for i,sym in enumerate(universe["symbols"]):
            self.store.write_row(self.day,"aggressive",sym,{
                "symbol":sym,"membership_hash":h,"fund_attempted":True,
                "fund_status":"VALIDATED" if i<10 else "PRELIMINARY",
                "fundamental":evidence(status="VALIDATED" if i<10 else "PRELIMINARY"),
                "technical":technical(sym,risk=25. if i==0 else 6.)
            })
        state,stats,body=n.render_snapshot("aggressive",universe,
                                         self.store.rows(self.day,"aggressive"),self.day)
        self.assertEqual(state,"READY")
        self.assertEqual(stats["reviewed"],250)
        self.assertEqual(stats["price_fresh"],250)
        self.assertGreaterEqual(stats["eligible_count"],7)
        self.assertNotIn("TEST000</b>",body) # 25% stop too wide
        self.assertIn("Conditional entry",body)
        self.assertIn("Issuer filing independently audited",body)
        self.store.save_report(self.day,"aggressive",state,stats,body)
        shown=n.load_prepared_report("aggressive",store=self.store,
              now=datetime(2026,10,30,8,10,tzinfo=n.IST))
        self.assertIn("Nifty Smallcap 250",shown)
        self.assertIn("250/250",shown)
        self.assertEqual(self.store.get_report("aggressive")["state"],"READY")

    def test_stale_market_candles_cannot_generate_overnight_ready(self):
        universe=make_universe("conservative",100)
        with patch.object(n,"get_index_universe",return_value=universe), \
             patch("market_data.bulk_history",return_value={
               sym:Mock(empty=False) for sym in universe["symbols"]}), \
             patch("top5_technical_ratings.technical_candidate",
                   side_effect=lambda sym,*a:technical(sym,"2026-10-28")), \
             patch("top5_company_ratings.fetch_company_evidence",
                   return_value=evidence()):
            n.collect_profile("conservative",self.store,self.day,delay_s=0)
        with patch.object(n,"get_index_universe",return_value=universe):
            ready=n.finalize_profile("conservative",self.store,self.day)
        self.assertEqual(ready["state"],"PARTIAL")
        self.assertEqual(ready["stats"]["price_fresh"],0)
        self.assertIn("100/100",ready["text"])
        self.assertIn("WITHHELD",ready["text"])
        self.assertIn("stale",n.load_prepared_report("conservative",store=self.store,
              now=datetime(2026,11,12,10,tzinfo=n.IST)).lower())

    def test_cleaning_nan_and_store_documented_no_connection(self):
        self.assertIsNone(n._clean(float("nan")))
        self.assertEqual(n._clean({"x":float("inf")}),{"x":None})
        with self.assertRaises(RuntimeError):
            n.EvidenceStore("")
        with self.assertRaises(ValueError):
            n.EvidenceStore("ftp://unknown")

    def test_buttons_fail_closed_on_missing_prepared_report_not_old_scan(self):
        with patch.dict(os.environ,{"MSK_INDEX_PREPARED_ONLY":"1"}), \
             patch.object(n,"load_prepared_report",return_value="⏳ Next EOD report is being prepared") as ready, \
             patch.object(live,"_build_index_top5") as heavy:
            answer=live.request_index_top5("moderate",42,lambda *a:None)
            self.assertIn("being prepared",answer)
            ready.assert_called_once_with("moderate")
            heavy.assert_not_called()
        self.assertEqual(live.request_index_top5("invalid",42,lambda *a:None),"❌ Unknown index profile.")

    def test_target_session_previous_trading_weekday_without_invented_holiday(self):
        with patch.object(n,"datetime",wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,10,30,7,55,tzinfo=n.IST)
            self.assertEqual(n._session(),date(2026,10,29))
            clock.now.return_value=datetime(2026,11,2,7,40,tzinfo=n.IST)
            self.assertEqual(n._session(),date(2026,10,30))


if __name__=="__main__":
    unittest.main()
