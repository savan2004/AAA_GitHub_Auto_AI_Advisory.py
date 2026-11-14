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

    def test_official_dummy_constituent_is_recorded_not_quoted_or_ranked(self):
        universe=make_universe("aggressive",250)
        members=list(universe["symbols"])
        members[-1]="DUMMYHEG"
        universe["symbols"]=tuple(members)
        universe["industries"]["DUMMYHEG"]="Capital Goods"
        with patch.object(n,"get_index_universe",return_value=universe), \
             patch("market_data.bulk_history",return_value={
                 sym:Mock(empty=False) for sym in members if sym!="DUMMYHEG"
             }) as price, \
             patch("top5_technical_ratings.technical_candidate",
                   side_effect=lambda sym,*a:technical(sym)), \
             patch("top5_company_ratings.fetch_company_evidence",
                   return_value=evidence("aggressive")) as financial:
            n.collect_profile("aggressive",self.store,self.day,delay_s=0)
        self.assertNotIn("DUMMYHEG",price.call_args.args[0])
        self.assertEqual(financial.call_count,249)
        row=self.store.row(self.day,"aggressive","DUMMYHEG")
        self.assertEqual(row["price_reason"],
                         "official_nse_nonstradable_placeholder")
        self.assertIsNone(row["technical"])
        self.assertEqual(row["fund_status"],"REJECTED")
        state,stats,body=n.render_snapshot(
            "aggressive",universe,self.store.rows(self.day,"aggressive"),self.day)
        self.assertEqual(stats["expected"],250)
        self.assertEqual(stats["price_fresh"],249)
        self.assertNotIn("DUMMYHEG</b>",body)

    def test_previously_unavailable_fundamentals_get_one_bounded_recovery(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,
                "fund_attempted":True,"fund_attempts":2,
                "fund_status":"UNVERIFIED",
                "fundamental":{"fund_reason":"provider_unavailable"},
                "technical":technical(sym)
            })
        with patch.object(n,"get_index_universe",return_value=universe), \
             patch("market_data.bulk_history") as bulk, \
             patch("top5_company_ratings.fetch_company_evidence",
                   return_value=evidence("conservative")) as fetch:
            n.collect_profile("conservative",self.store,self.day,delay_s=0)
            self.assertEqual(fetch.call_count,100)
            bulk.assert_not_called()
            n.collect_profile("conservative",self.store,self.day,delay_s=0)
            self.assertEqual(fetch.call_count,100)

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

    def test_changed_official_membership_revokes_stale_old_row_evidence(self):
        universe=make_universe("moderate",150)
        digest=n._member_hash(universe)
        old_digest="previous-official-membership"
        for i,sym in enumerate(universe["symbols"]):
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"trade_date":str(self.day),
                "membership_hash":old_digest if i==0 else digest,
                "fund_attempted":True,"fund_status":"VALIDATED",
                "fundamental":evidence(),"technical":technical(sym)
            })
        state,stats,msg=n.render_snapshot("moderate",universe,
                                          self.store.rows(self.day,"moderate"),self.day)
        self.assertEqual(state,"PARTIAL")
        self.assertEqual(stats["reviewed"],149)
        self.assertEqual(stats["price_fresh"],149)
        self.assertEqual(stats["validated"],149)
        self.assertNotIn("1. TEST",msg)
        self.assertIn("TOP 5 WITHHELD",msg.upper())

    def test_morning_readiness_checks_all_profiles_and_exact_session(self):
        for profile,nmembers in (("conservative",100),("moderate",150),
                                 ("aggressive",250)):
            universe=make_universe(profile,nmembers)
            stats={"expected":nmembers,"reviewed":nmembers,
                   "price_fresh":nmembers,
                   "membership_hash":n._member_hash(universe)}
            for sym in universe["symbols"]:
                self.store.write_row(self.day,profile,sym,{
                    "symbol":sym,"membership_hash":stats["membership_hash"],
                    "fund_attempted":True,"technical":technical(sym)
                })
            self.store.save_report(self.day,profile,"READY",stats,"SYNTHETIC READY")
        state=n.readiness_check(self.store,self.day)
        # A synthetic READY summary with no actual qualifying Smallcap pick
        # must not satisfy the aggregate morning publication gate.
        self.assertFalse(state["ready"])
        self.assertEqual([row["ready"] for row in state["profiles"]],
                         [True, True, False])
        self.assertEqual([row["expected"] for row in state["profiles"]],
                         [100,150,250])
        # Without any real records on the following session, a healthy DB
        # never promotes the previous reports to current readiness.
        tomorrow=date(2026,10,30)
        self.assertFalse(n.readiness_check(self.store,tomorrow)["ready"])

    def test_readiness_rejects_partial_low_coverage_and_wrong_membership(self):
        u=make_universe("moderate",150)
        good={"expected":150,"reviewed":150,"price_fresh":140,
              "membership_hash":n._member_hash(u)}
        for sym in u["symbols"]:
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"membership_hash":good["membership_hash"],
                "fund_attempted":True,
                "technical":technical(sym) if int(sym[-3:])<140 else None
            })
        for status,stat in (
            ("PARTIAL",good),
            ("READY",{**good,"reviewed":149}),
            ("READY",{**good,"price_fresh":120}),
            ("READY",{**good,"expected":28,"reviewed":28}),
            ("READY",{**good,"membership_hash":"MISSING"}),
        ):
            with self.subTest(status=status,stats=stat):
                self.store.save_report(self.day,"moderate",status,stat,"QA")
                outcome=n.readiness_check(self.store,self.day,("moderate",))
                self.assertFalse(outcome["ready"])
        self.store.save_report(self.day,"moderate","READY",good,"QA")
        self.assertTrue(n.readiness_check(
            self.store,self.day,("moderate",))["ready"])

    def test_readiness_rejects_orphan_summary_and_ledger_mismatch(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        stats={"expected":100,"reviewed":100,"price_fresh":100,
               "membership_hash":digest}
        self.store.save_report(self.day,"conservative","READY",stats,"QA")
        self.assertFalse(n.readiness_check(
            self.store,self.day,("conservative",))["ready"],
            "READY summary without company records must fail closed")
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "technical":technical(sym)
            })
        self.assertTrue(n.readiness_check(
            self.store,self.day,("conservative",))["ready"])
        bad=universe["symbols"][0]
        self.store.write_row(self.day,"conservative",bad,{
            "symbol":bad,"membership_hash":digest,"fund_attempted":False,
            "technical":technical(bad)
        })
        self.assertFalse(n.readiness_check(
            self.store,self.day,("conservative",))["ready"])

    def test_repaired_financials_invalidate_outdated_ready_report(self):
        universe=make_universe("moderate",150)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"UNVERIFIED","technical":technical(sym)
            })
        with patch.object(n,"get_index_universe",return_value=universe):
            initial=n.finalize_profile("moderate",self.store,self.day)
        self.assertTrue(n.readiness_check(
            self.store,self.day,("moderate",))["ready"])
        symbol=universe["symbols"][0]
        fixed=self.store.row(self.day,"moderate",symbol)
        fixed["fund_status"]="VALIDATED"
        fixed["fundamental"]=evidence()
        self.store.write_row(self.day,"moderate",symbol,fixed)
        self.assertFalse(n.readiness_check(
            self.store,self.day,("moderate",))["ready"])
        hidden=n.load_prepared_report("moderate",store=self.store,
                         now=datetime(2026,10,30,8,10,tzinfo=n.IST))
        self.assertIn("withheld",hidden.lower())
        with patch.object(n,"get_index_universe",return_value=universe):
            updated=n.finalize_profile("moderate",self.store,self.day)
        self.assertEqual(updated["stats"]["validated"],1)
        self.assertTrue(n.readiness_check(
            self.store,self.day,("moderate",))["ready"])

    def test_impossible_candidate_count_cannot_be_released(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"UNVERIFIED","technical":technical(sym)
            })
        stats={"expected":100,"reviewed":100,"price_fresh":100,
               "membership_hash":digest,"validated":0,"provisional":0,
               "candidate_count":5}
        self.store.save_report(self.day,"conservative","READY",stats,
                               "FALSE FIVE PICKS")
        self.assertFalse(n.readiness_check(
            self.store,self.day,("conservative",))["ready"])

    def test_telegram_withholds_orphan_ready_and_missing_company_evidence(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        stats={"expected":100,"reviewed":100,"price_fresh":100,
               "membership_hash":digest}
        self.store.save_report(self.day,"conservative","READY",stats,
                               "FALSE TOP 5 SUMMARY")
        now=datetime(2026,10,30,8,10,tzinfo=n.IST)
        message=n.load_prepared_report("conservative",store=self.store,now=now)
        self.assertIn("withheld",message.lower())
        self.assertNotIn("FALSE TOP 5 SUMMARY",message)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "technical":technical(sym)
            })
        self.assertEqual(
            n.load_prepared_report("conservative",store=self.store,now=now),
            "FALSE TOP 5 SUMMARY")
        self.store.write_row(self.day,"conservative",universe["symbols"][0],{
            "symbol":universe["symbols"][0],"membership_hash":digest,
            "fund_attempted":False,"technical":technical(universe["symbols"][0])
        })
        self.assertIn("withheld",n.load_prepared_report(
            "conservative",store=self.store,now=now).lower())

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

    def test_wait_watchlist_contains_reasons_but_never_buy_entries(self):
        universe=make_universe("moderate",150)
        digest=n._member_hash(universe)
        for i,sym in enumerate(universe["symbols"]):
            tech=technical(sym,signal="WAIT",risk=9.2 if i<3 else 17.2)
            tech["signal_reasons"]=["breakout_not_confirmed"]
            tech["tech_score"]=85 if i<3 else 50
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"VALIDATED" if i<3 else "UNVERIFIED",
                "fundamental":evidence(),
                "technical":tech
            })
        state,stats,body=n.render_snapshot(
            "moderate",universe,self.store.rows(self.day,"moderate"),self.day)
        self.assertEqual(state,"READY")
        self.assertEqual(stats["candidate_count"],0)
        self.assertIn("RESEARCH WATCHLIST ONLY",body)
        self.assertIn("breakout not confirmed",body)
        self.assertIn("planned stop risk 9.2%",body)
        self.assertNotIn("planned stop risk 17.2%",body)
        self.assertIn("TEST000",body)
        self.assertNotIn("Conditional entry",body)

    def test_excess_risk_waits_never_enter_watchlist(self):
        universe=make_universe("conservative",100)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            tech=technical(sym,signal="WAIT",risk=20.0)
            self.store.write_row(self.day,"conservative",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"VALIDATED","fundamental":evidence(),
                "technical":tech
            })
        state,stats,body=n.render_snapshot(
            "conservative",universe,self.store.rows(self.day,"conservative"),self.day)
        self.assertEqual(state,"READY")
        self.assertEqual(stats["candidate_count"],0)
        self.assertIn("Data review: <b>COMPLETE</b>", body)
        self.assertIn("Entry setups: <b>0/5 — NO ENTRY CONFIRMED</b>", body)
        self.assertNotIn("Report status: <b>READY</b>", body)
        self.assertIn("No research watchlist passes",body)
        self.assertNotIn("RESEARCH WATCHLIST ONLY",body)
        self.assertNotIn("Conditional entry",body)

    def test_no_picks_explains_wait_without_fabricating_buy(self):
        universe=make_universe("moderate",150)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            signal=technical(sym,signal="WAIT")
            self.store.write_row(self.day,"moderate",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"VALIDATED","fundamental":evidence(),
                "technical":signal
            })
        state,stats,body=n.render_snapshot(
            "moderate",universe,self.store.rows(self.day,"moderate"),self.day
        )
        self.assertEqual(state,"READY")
        self.assertEqual(stats["candidate_count"],0)
        self.assertIn("all 150 financially rule-qualified stocks currently show wait",body.lower())
        self.assertIn("No Top 5 issued",body)
        self.assertNotIn("Conditional entry",body)

    def test_smallcap_complete_but_zero_candidates_is_not_ready(self):
        universe=make_universe("aggressive",250)
        digest=n._member_hash(universe)
        for sym in universe["symbols"]:
            self.store.write_row(self.day,"aggressive",sym,{
                "symbol":sym,"membership_hash":digest,"fund_attempted":True,
                "fund_status":"VALIDATED","fundamental":evidence(),
                "technical":technical(sym,signal="WAIT")
            })
        with patch.object(n,"get_index_universe",return_value=universe):
            report=n.finalize_profile("aggressive",self.store,self.day)
        self.assertEqual(report["state"],"NO_CANDIDATES")
        self.assertEqual(report["stats"]["candidate_count"],0)
        self.assertFalse(n.readiness_check(
            self.store,self.day,("aggressive",))["ready"])
        msg=n.load_prepared_report(
            "aggressive",store=self.store,
            now=datetime(2026,10,30,8,10,tzinfo=n.IST))
        self.assertIn("SCREENING COMPLETE — NO QUALIFYING SETUP TODAY",msg)
        self.assertNotIn("1. TEST",msg)

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
        self.assertIn("SCREENING COMPLETE — NO QUALIFYING SETUP TODAY",ready["text"])
        self.assertIn("Data as of",ready["text"])
        self.assertIn("not ready",n.load_prepared_report("conservative",store=self.store,
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

    def test_button_does_not_show_yesterdays_old_quote_as_today(self):
        self.store.save_report("2026-10-28","moderate","READY",
          {"reviewed":150},"OLD SESSION: DO NOT SHOW AS LIVE")
        text=n.load_prepared_report("moderate",store=self.store,
            now=datetime(2026,10,30,8,5,tzinfo=n.IST))
        self.assertIn("expected completed NSE session 2026-10-29",text)
        self.assertNotIn("OLD SESSION: DO NOT SHOW AS LIVE",text)

    def test_target_session_previous_trading_weekday_without_invented_holiday(self):
        with patch.object(n,"datetime",wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,10,30,7,55,tzinfo=n.IST)
            self.assertEqual(n._session(),date(2026,10,29))
            clock.now.return_value=datetime(2026,11,2,7,40,tzinfo=n.IST)
            self.assertEqual(n._session(),date(2026,10,30))



@patch.dict(os.environ, {"MSK_RESEARCH_ONLY": "1"})
class IndependentFundamentalProviderTests(unittest.TestCase):
    def test_statement_failure_keeps_available_ratios_not_provider_unavailable(self):
        from top5_company_ratings import fetch_company_evidence
        ratios={"roe":18.5,"pe":22.1,"eps":12.3}
        with patch("fundamentals.get_fundamentals",return_value=ratios), \
             patch("research_data.get_statements",
                   side_effect=TimeoutError("provider offline")):
            result=fetch_company_evidence("SBIN","Financial Services",
                                          "moderate",date(2026,10,29))
        self.assertNotEqual(result["fund_reason"],"provider_unavailable")
        self.assertEqual(result["roe"],18.5)
        self.assertNotEqual(result["fund_status"],"VALIDATED")

    def test_ratio_failure_preserves_statement_provider_reason(self):
        from top5_company_ratings import fetch_company_evidence
        with patch("fundamentals.get_fundamentals",
                   side_effect=TimeoutError("ratios offline")), \
             patch("research_data.get_statements",return_value={
                 "source":"Screener.in","years":[],"quarters":[]}):
            result=fetch_company_evidence("SBIN","Financial Services",
                                          "moderate",date(2026,10,29))
        self.assertNotEqual(result["fund_reason"],"provider_unavailable")
        self.assertNotEqual(result["fund_status"],"VALIDATED")

    def test_both_providers_offline_remains_unverified(self):
        from top5_company_ratings import fetch_company_evidence
        with patch("fundamentals.get_fundamentals",
                   side_effect=TimeoutError("offline")), \
             patch("research_data.get_statements",
                   side_effect=TimeoutError("offline")):
            result=fetch_company_evidence("SBIN","Financial Services",
                                          "moderate",date(2026,10,29))
        self.assertEqual(result["fund_reason"],"provider_unavailable")
        self.assertEqual(result["fund_status"],"UNVERIFIED")


class ResearchWorkerConfigTests(unittest.TestCase):
    def test_finance_only_worker_does_not_require_telegram_secret(self):
        import subprocess
        import sys
        env=dict(os.environ)
        env.pop("TELEGRAM_TOKEN",None)
        env["MSK_RESEARCH_ONLY"]="1"
        outcome=subprocess.run(
            [sys.executable,"-c","import config; assert config.TELEGRAM_TOKEN == ''"],
            env=env,capture_output=True,text=True,timeout=15
        )
        self.assertEqual(outcome.returncode,0,outcome.stderr[:300])

    def test_normal_bot_requires_actual_telegram_secret(self):
        import subprocess
        import sys
        env=dict(os.environ)
        env.pop("TELEGRAM_TOKEN",None)
        env.pop("MSK_RESEARCH_ONLY",None)
        outcome=subprocess.run(
            [sys.executable,"-c","import config"],env=env,
            capture_output=True,text=True,timeout=15
        )
        self.assertNotEqual(outcome.returncode,0)
        self.assertIn("TELEGRAM_TOKEN",outcome.stderr)


class OptionalFallbackSafetyTests(unittest.TestCase):
    def test_no_telegram_secret_does_not_abort_bulk_price_download(self):
        import sys
        import market_data
        # A disabled legacy data engine cannot prevent valid Yahoo bulk rows
        # from being returned or a missing stock from being recorded.
        valid=Mock(empty=False)
        with patch.dict(sys.modules, {"data_engine": None}), \
             patch.object(market_data, "_download_chunk",
                          return_value={"RELIANCE.NS": valid}):
            result=market_data.bulk_history(
                ["RELIANCE", "DUMMYHEG"], "6mo", retries=0
            )
        self.assertIn("RELIANCE",result)
        self.assertNotIn("DUMMYHEG",result)


class TejFallbackTests(unittest.TestCase):
    def test_keyless_response_is_symbol_checked_and_session_checked(self):
        from overnight_index_sources import fetch_tej_history
        points=[]
        for i in range(99,-1,-1):
            day=date(2026,10,29)-n.timedelta(days=i)
            v=400+i*.3
            points.append(dict(date=day.isoformat(),open=v,high=v+3,
                               low=v-2,close=v+1,volume=85000))
        res=Mock(status_code=200)
        res.json.return_value={"meta":{"symbol":"BEL","exchange":"NSE"},"data":points}
        http=Mock(get=Mock(return_value=res))
        chart=fetch_tej_history("BEL",date(2026,10,29),http=http)
        self.assertIsNotNone(chart)
        self.assertEqual(len(chart),100)
        self.assertEqual(chart.index[-1].date().isoformat(),"2026-10-29")
        self.assertEqual(chart.iloc[-1]["Close"],401)
        self.assertTrue(http.get.call_args.args[0].endswith("/BEL"))
        res.json.return_value["meta"]["symbol"]="AAPL"
        self.assertIsNone(fetch_tej_history("BEL",date(2026,10,29),http=http))
        res.json.return_value["meta"]["symbol"]="BEL"
        res.json.return_value["data"][-1]["close"]=999999
        self.assertIsNone(fetch_tej_history("BEL",date(2026,10,29),http=http))

    def test_secondary_source_rejects_nan_and_infinite_prices(self):
        from overnight_index_sources import fetch_tej_history
        import math
        points=[]
        for i in range(99,-1,-1):
            day=date(2026,10,29)-n.timedelta(days=i)
            points.append(dict(date=day.isoformat(),open=100.,high=103.,
                               low=99.,close=101.,volume=5000))
        response=Mock(status_code=200)
        response.json.return_value={
            "meta":{"symbol":"BEL","exchange":"NSE"},"data":points}
        client=Mock(get=Mock(return_value=response))
        for field,nonfinite in (("close",float("nan")),
                                ("high",float("inf")),
                                ("volume",-float("inf"))):
            with self.subTest(field=field):
                points[-1][field]=nonfinite
                self.assertIsNone(fetch_tej_history("BEL",date(2026,10,29),http=client))
                points[-1][field]={"close":101.,"high":103.,"volume":5000}[field]

    def test_unavailable_primary_prices_get_secondary_EOD_without_provisional_topfive(self):
        universe=make_universe("conservative",100)
        with tempfile.TemporaryDirectory() as temp:
            with n.EvidenceStore("sqlite:///"+str(Path(temp)/"x.db")) as store:
                with patch.object(n,"get_index_universe",return_value=universe), \
                     patch.dict(os.environ,{"MSK_INDEX_TEJ_FALLBACK":"1"}), \
                     patch("market_data.bulk_history",return_value={}), \
                     patch("overnight_index_sources.fetch_tej_history",
                           return_value=Mock(empty=False)) as alternate, \
                     patch("top5_technical_ratings.technical_candidate",
                           side_effect=lambda sym,*args:technical(sym)), \
                     patch("top5_company_ratings.fetch_company_evidence",
                           return_value=evidence("conservative",status="PRELIMINARY")):
                    n.collect_profile("conservative",store,date(2026,10,29),delay_s=0)
                values=store.rows("2026-10-29","conservative")
                self.assertEqual(alternate.call_count,100)
                self.assertEqual(values["TEST000"]["price_source"],
                                 "TejHQ / NSE bhavcopy (raw EOD)")
                state,stats,msg=n.render_snapshot("conservative",universe,values,
                                                  date(2026,10,29))
                self.assertEqual(state,"READY")
                self.assertEqual(stats["reviewed"],100)
                self.assertEqual(stats["validated"],0)
                self.assertNotIn("1. TEST",msg)

if __name__=="__main__":
    unittest.main()
