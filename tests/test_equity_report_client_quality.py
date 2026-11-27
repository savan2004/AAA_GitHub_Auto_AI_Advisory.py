"""MSK EIEL equity research publication quality regressions (offline, no credentials).

The user's nine-page EIEL PDF exposed a 48/100 CAUTIOUS mismatch, '100% data
coverage' despite absent financial fields, and no visible stop/targets in WAIT.
These tests execute the actual source functions via AST isolation.
"""
import ast
import math
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=(ROOT/"report_generator.py").read_text(encoding="utf-8")
ENGINE=(ROOT/"research_engine.py").read_text(encoding="utf-8")


def fn(text,name,env):
    tree=ast.parse(text)
    node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name==name)
    ns=dict(env)
    exec(compile(ast.Module(body=[node],type_ignores=[]),name,"exec"),ns)
    return ns[name]


def test_scores(values):
    weights={"Trend":.2,"Momentum":.15,"Quality":.2,
             "Valuation":.15,"Growth":.15,"Risk":.15}
    keys={"_trend_score":"Trend","_momentum_score":"Momentum","_quality_score":"Quality",
          "_valuation_score":"Valuation","_growth_score":"Growth","_risk_score":"Risk"}
    env={"WEIGHTS":weights,"_avg":lambda xs:sum(v for v in xs if v is not None)/sum(v is not None for v in xs)}
    env.update({name:lambda *_a,key=key:values[key] for name,key in keys.items()})
    return fn(ENGINE,"compute_scores",env)({},
        {},{"has_statements":True,"pe":16})


class EquityReportQuality(unittest.TestCase):
    def test_rounding_matches_declared_stance_bands(self):
        a=test_scores({"Trend":46,"Momentum":46,"Quality":50,
                       "Valuation":50,"Growth":46,"Risk":48})
        self.assertEqual(a["composite"],48)
        self.assertEqual(a["stance"],"NEUTRAL / WAIT")
        self.assertEqual(a["coverage"],100)
        b=test_scores({"Trend":46,"Momentum":46,"Quality":47,
                       "Valuation":47,"Growth":46,"Risk":48})
        self.assertEqual(b["composite"],47)
        self.assertEqual(b["stance"],"CAUTIOUS")

    def test_defensive_wait_price_plan_has_correct_risk_reward(self):
        build=fn(REPORT,"conditional_watch_levels",{"math":math})
        got=build({"atr":6.89},{"bull_trigger":167.80,"reclaim":189.25})
        self.assertEqual(got["entry"],189.25)
        self.assertEqual(got["sl"],175.47)
        risk=got["entry"]-got["sl"]
        self.assertAlmostEqual((got["t1"]-got["entry"])/risk,2,places=2)
        self.assertAlmostEqual((got["t2"]-got["entry"])/risk,3,places=2)
        self.assertEqual(got["rr1"],2)
        self.assertEqual(got["rr2"],3)

    def test_bad_atr_or_impossible_stop_suppresses_levels(self):
        build=fn(REPORT,"conditional_watch_levels",{"math":math})
        for atr,entry in [(None,150),(0,150),(float("nan"),150),(60,100)]:
            self.assertIsNone(build({"atr":atr},{"bull_trigger":entry,"reclaim":0}))

    def test_report_source_exposes_missing_data_and_no_unverified_buy(self):
        for required in (
            "Core financial ratios available:", "Model pillar weight covered:",
            "Price: EOD session", "MSK Bot Automated Research",
            "MODEL STANCE", "Financial figures and ratios were not independently reconciled",
            "WAIT-ONLY mathematical scenario", "not price forecasts",
            "1:2", "1:3", "SOURCE REVIEW REQUIRED",
        ):
            self.assertIn(required,REPORT)
        self.assertNotIn("Data coverage {sc['coverage']}%",REPORT)
        self.assertNotIn("Senior Market Analyst Desk",REPORT)
        self.assertIn("if plan[\"mode\"] != \"DEFENSIVE\":",REPORT)

    def test_pdf_does_not_turn_model_trend_into_active_trade(self):
        mode = fn(REPORT, "_report_plan_mode", {})
        self.assertEqual(mode({"mode":"ACTIVE"}), "CONDITIONAL")
        self.assertEqual(mode({"mode":"CONDITIONAL"}), "CONDITIONAL")
        self.assertEqual(mode({"mode":"DEFENSIVE"}), "DEFENSIVE")
        self.assertEqual(mode({"mode":"NO_TRADE"}), "NO_TRADE")
        self.assertIn('plan["mode"] = _report_plan_mode(plan)', REPORT)
        self.assertNotIn("Use staggered entries", REPORT)
        self.assertIn("Conditional model scenario — WAIT", REPORT)

    def test_summary_mentions_source_period_and_not_live_price(self):
        self.assertIn("price_session=price_session",REPORT)
        self.assertIn("price as of {esc(R.get('price_session')",REPORT)
        self.assertIn("availability is not independent accuracy verification",REPORT)

    def test_eod_gate_rejects_partial_current_candle_and_stale_series(self):
        from datetime import date
        check = fn(REPORT, "_require_latest_completed_eod", {})
        expected = date(2026, 11, 20)
        self.assertEqual(check({"candle_date": "2026-11-20"}, expected), "2026-11-20")
        for invalid in (None, {}, {"candle_date": "2026-11-23"},
                        {"candle_date": "2026-11-19"}):
            with self.subTest(market=invalid), self.assertRaisesRegex(ValueError, "latest completed"):
                check(invalid, expected)
        self.assertIn("verified_pdf_history", REPORT)

    def test_unsourced_headlines_and_unverified_probability_are_suppressed(self):
        news = fn(REPORT, "_real_news", {"List": list, "logger": type("Log", (), {"info": lambda *a: None})()})
        self.assertEqual(news("DMART"), [])
        self.assertNotIn('if R["news"]:', REPORT)
        self.assertNotIn("about 68% of outcomes", REPORT)
        self.assertNotIn("Verified quarterly visual trends", REPORT)
        self.assertIn("no comparable peers with usable sourced", REPORT)

    def test_technical_veto_does_not_claim_valuation_is_safe(self):
        scores = test_scores({"Trend":10, "Momentum":52, "Quality":82,
                              "Valuation":12, "Growth":68, "Risk":66})
        self.assertEqual(scores["stance"], "NEUTRAL / WAIT")
        self.assertTrue(scores["veto"])
        self.assertIn("valuation is scored separately", scores["action"])
        self.assertNotIn("valuation are not the problem", scores["action"])



class QuarterlyFilingRepair(unittest.TestCase):
    def test_isolated_eps_conflict_uses_official_basis_without_mutating_source(self):
        from research_filing_evidence import repair_verified_quarter_eps
        raw = {"source": "Screener.in", "quarters": [
            {"label": "Jun 2026", "revenue": 5607, "net_profit": 263, "eps": 9.5},
            {"label": "Sep 2026", "revenue": 7002, "net_profit": 382, "eps": 13.77},
        ]}
        repaired, proof = repair_verified_quarter_eps("APLAPOLLO", raw)
        self.assertEqual(raw["quarters"][-1]["eps"], 13.77)
        self.assertEqual(repaired["quarters"][-1]["eps"], 13.62)
        self.assertEqual(repaired["quarters"][0], raw["quarters"][0])
        self.assertEqual(proof["status"], "partial_match")
        self.assertEqual(len(proof["matched"]), 3)
        self.assertEqual(proof["eps_correction"]["reported"], 13.77)

    def test_other_conflicts_or_unmatched_period_never_override(self):
        from research_filing_evidence import repair_verified_quarter_eps
        for changes, expected in [
            ({"revenue": 6900}, "mismatch"),
            ({"net_profit": None}, "mismatch"),
            ({"label": "Jun 2026"}, "period_not_comparable"),
        ]:
            raw = {"quarters": [dict({"label": "Sep 2026", "revenue": 7002,
                                      "net_profit": 382, "eps": 13.77}, **changes)]}
            result, proof = repair_verified_quarter_eps("APLAPOLLO", raw)
            self.assertIs(result, raw)
            self.assertEqual(proof["status"], expected)
            self.assertNotIn("eps_correction", proof)

    def test_correct_source_and_unregistered_symbol_unchanged(self):
        from research_filing_evidence import repair_verified_quarter_eps
        raw = {"quarters": [{"label": "Sep 2026", "revenue": 7002,
                             "net_profit": 382, "eps": 13.62}]}
        for symbol, status in [("APLAPOLLO", "partial_match"), ("SBIN", "not_registered")]:
            result, proof = repair_verified_quarter_eps(symbol, raw)
            self.assertIs(result, raw)
            self.assertEqual(proof["status"], status)
            self.assertNotIn("eps_correction", proof)

    def test_reconciliation_precedes_calculation_and_visuals(self):
        self.assertLess(REPORT.index("statements, filing_evidence = repair_verified_quarter_eps"),
                        REPORT.index("fv = eng.compute_fundamentals"))
        self.assertIn('fund = {**fund, "eps": None}', REPORT)
        self.assertIn('fv["implied_peer_value"] = None', REPORT)
        self.assertIn('render_quarterly_chart((inp.get("stm")', REPORT)

class PublicPDFActualEvidence(unittest.TestCase):
    def test_reference_fallback_only_missing_exact_symbols(self):
        from datetime import date
        from unittest.mock import Mock
        from research_pdf_price_guard import public_pdf_reference
        day = date(2026, 11, 26)
        primary = {"SBIN": {"Close": 100}}
        fetch = Mock(return_value=primary)
        archive = Mock(return_value={"DMART.NS": {"Close": 200},
                                     "SBIN.NS": {"Close": 999}})
        result = public_pdf_reference(day, ["SBIN", "DMART"], fetch=fetch, archive=archive)
        self.assertEqual(result, {"SBIN": {"Close": 100}, "DMART": {"Close": 200}})
        fetch.assert_called_once_with(day, ("SBIN", "DMART"))
        archive.assert_called_once_with(day, ["DMART.NS"])

    def test_real_report_price_requires_exact_session_and_ohlcv(self):
        from datetime import date
        from research_pdf_price_guard import verify_public_pdf_price, PublicPDFEvidenceError
        day = date(2026, 11, 26)
        row = dict(Open=98., High=102., Low=97., Close=100., Volume=1000.)
        ref = {"SBIN": row.copy()}
        evidence = dict(symbol="SBIN", price_session="26-Nov-2026",
                        historical_close=100., price_bar=row.copy())
        self.assertEqual(verify_public_pdf_price("SBIN", day, evidence, ref), (day, 100.))
        for changes, reason in [
            ({"symbol": "TCS"}, "REPORT_SYMBOL_MISMATCH"),
            ({"price_session": "25-Nov-2026"}, "REPORT_SESSION_MISMATCH"),
            ({"historical_close": float("nan")}, "REPORT_PRICE_INVALID"),
            ({"historical_close": 100.20}, "REPORT_RENDER_PRICE_MISMATCH"),
            ({"price_bar": dict(row, Volume=1001.)}, "OFFICIAL_OHLCV_MISMATCH"),
            ({"price_bar": dict(row, Close=100.20), "historical_close": 100.20},
             "OFFICIAL_CLOSE_MISMATCH"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(PublicPDFEvidenceError, reason):
                verify_public_pdf_price("SBIN", day, dict(evidence, **changes), ref)
        with self.assertRaisesRegex(PublicPDFEvidenceError, "OFFICIAL_REFERENCE_MISSING"):
            verify_public_pdf_price("SBIN", day, evidence, {})


if __name__=="__main__":
    unittest.main()
