"""Offline regression: fundamental quality must precede every Top-5 pick."""
from copy import deepcopy
from datetime import date
import unittest
from unittest.mock import Mock, patch

import top5_fundamental_gate as gate
import index_top5 as top5

TODAY = date(2026, 10, 29)
FUND = {"roe": 19.0, "pe": 24.0, "eps": 22.5, "pb": 3.0}
YEAR = [
    {"label": "Mar 2023", "net_profit": 100, "revenue": 650, "borrowings": 100, "networth": 400},
    {"label": "Mar 2024", "net_profit": 120, "revenue": 750, "borrowings": 90, "networth": 420},
    {"label": "Mar 2025", "net_profit": 147, "revenue": 850, "borrowings": 80, "networth": 455},
    {"label": "Mar 2026", "net_profit": 185, "revenue": 1000, "borrowings": 60, "networth": 530},
]
STATEMENTS = {"source": "Screener.in",
              "sources": {"years": "Screener.in", "quarters": "Screener.in"},
              "years": YEAR, "quarters": [
                  {"label": "Dec 2025", "net_profit": 40},
                  {"label": "Mar 2026", "net_profit": 45},
                  {"label": "Jun 2026", "net_profit": 55},
              ]}


class FundamentalRules(unittest.TestCase):
    def test_strong_corporate_passes_large_mid_small(self):
        for profile in gate.POLICY:
            result, why = gate.assess_fundamentals(profile, FUND, STATEMENTS, "Engineering", TODAY)
            self.assertEqual(why, "qualified")
            self.assertIsNotNone(result)
            self.assertEqual(result["quarter"], "2026-06-30")
            self.assertEqual(result["annual"], "2026-03-31")
            self.assertGreater(result["profit_growth"], 15)
            self.assertGreater(result["sales_growth"], 10)
            self.assertAlmostEqual(result["de"], 60 / 530, places=2)
            self.assertTrue(0 <= result["fund_score"] <= 100)

    def test_missing_and_stale_quarters_rejected(self):
        s = deepcopy(STATEMENTS)
        s["quarters"] = []
        self.assertIn("quarterly_missing", gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1])
        s["quarters"] = [{"label": "Dec 2025"}]
        self.assertIn("quarterly_stale", gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1])

    def test_reject_missing_loss_debt_expensive_and_weak_growth(self):
        for key in ("roe", "pe", "eps"):
            f = dict(FUND, **{key: None})
            self.assertIsNone(gate.assess_fundamentals("aggressive", f, STATEMENTS, "IT", TODAY)[0])
        self.assertIsNone(gate.assess_fundamentals("aggressive", dict(FUND, pe=220), STATEMENTS, "IT", TODAY)[0])
        s = deepcopy(STATEMENTS)
        s["years"][-1]["net_profit"] = -3
        self.assertIsNone(gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[0])
        s = deepcopy(STATEMENTS)
        s["years"][-1]["borrowings"] = 2100
        self.assertEqual(gate.assess_fundamentals("aggressive", FUND, s, "IT", TODAY)[1], "debt_too_high")
        s = deepcopy(STATEMENTS)
        s["years"][-1]["borrowings"] = None
        self.assertEqual(gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1], "debt_or_equity_missing")
        s = deepcopy(STATEMENTS)
        s["years"][-1]["revenue"] = 650
        self.assertEqual(gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1],
                         "sales_growth_missing_or_weak")

    def test_bank_requires_pb_not_industrial_debt(self):
        s = deepcopy(STATEMENTS)
        s["years"][-1]["borrowings"] = 25000  # bank deposits/debt not comparable
        ok, why = gate.assess_fundamentals("conservative", FUND, s, "Private Banks", TODAY)
        self.assertEqual(why, "qualified")
        self.assertTrue(ok["fin"])
        self.assertIsNone(ok["de"])
        self.assertIsNotNone(ok["pb"])
        no_pb, why = gate.assess_fundamentals("conservative", dict(FUND, pb=None), s,
                                             "Private Banks", TODAY)
        self.assertIsNone(no_pb)
        self.assertEqual(why, "financial_pb_missing_or_expensive")

    def test_reject_short_annual_history_and_undated_ttm(self):
        s = deepcopy(STATEMENTS)
        s["years"] = s["years"][-2:]
        self.assertEqual(gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1],
                         "annual_history_missing")
        s["years"].append({"label": "TTM", "net_profit": 999999})
        self.assertEqual(gate.assess_fundamentals("moderate", FUND, s, "IT", TODAY)[1],
                         "annual_history_missing")

    def test_free_source_exception_fails_closed_without_fake_numbers(self):
        with patch("fundamentals.get_fundamentals", side_effect=RuntimeError("blocked")):
            row, why = gate.fetch_and_assess("TESTCO", "IT", "moderate", TODAY)
        self.assertIsNone(row)
        self.assertEqual(why, "provider_unavailable")


class FullIndexIntegration(unittest.TestCase):
    def setUp(self):
        self.universe = {
            "symbols": tuple(f"TEST{i:03d}" for i in range(100)),
            "cap": "Large Cap", "name": "Nifty 100",
            "source": "Official NSE constituent CSV",
            "asof": "29 Oct 2026", "industries": {
                f"TEST{i:03d}": f"Industry {i % 5}" for i in range(100)
            },
        }
        self.history = {s: Mock(empty=False) for s in self.universe["symbols"]}

    def _technical(self, symbol, frame, asof, sector_override=None):
        i = int(symbol[4:])
        if i >= 25:
            return None
        return {"sym": symbol, "sector": sector_override, "score": 90-i,
                "ltp": 185, "chg": 2.1, "rsi": 63, "atr_pct": 2.0, "asof": "2026-10-28"}

    def test_investment_never_bypasses_fundamentals(self):
        with patch.object(top5, "get_index_universe", return_value=self.universe), patch(
                "market_data.bulk_history", return_value=self.history), patch.object(
                top5, "evaluate_conservative", side_effect=self._technical), patch.object(
                top5, "fetch_and_assess", return_value=(None, "provider_unavailable")) as verify:
            output = top5._build_index_top5("conservative")
        self.assertEqual(verify.call_count, gate.MAX_CHECKS)
        self.assertIn("Financial checks attempted: 18", output)
        self.assertIn("0/5 qualified", output)
        self.assertNotIn("1. TEST", output)

    def test_five_picks_require_proven_financial_fields(self):
        financial = {"fund_score": 80, "roe": 18, "pe": 23, "de": .25,
                     "pb": None, "fin": False, "profit_growth": 20,
                     "sales_growth": 14, "quarter": "2026-06-30", "annual": "2026-03-31"}
        with patch.object(top5, "get_index_universe", return_value=self.universe), patch(
                "market_data.bulk_history", return_value=self.history), patch.object(
                top5, "evaluate_conservative", side_effect=self._technical), patch.object(
                top5, "fetch_and_assess", return_value=(financial, "qualified")) as verify:
            output = top5._build_index_top5("conservative")
        self.assertEqual(verify.call_count, gate.MAX_CHECKS)
        self.assertIn("5/5 qualified technical + fundamental", output)
        self.assertEqual(output.count("📘 ROE"), 5)
        self.assertIn("Results 2026-06-30", output)
        self.assertIn("not independently reconciled", output)


if __name__ == "__main__":
    unittest.main()
