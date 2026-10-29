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

    def test_summary_mentions_source_period_and_not_live_price(self):
        self.assertIn("price_session=price_session",REPORT)
        self.assertIn("price as of {esc(R.get('price_session')",REPORT)
        self.assertIn("availability is not independent accuracy verification",REPORT)


if __name__=="__main__":
    unittest.main()
