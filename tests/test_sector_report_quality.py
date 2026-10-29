"""Focused, source-isolated tests for MSK sector report (no Telegram credentials).

Tests execute actual functions extracted from sector_report.py rather than
mocking their algorithm, keeping numerical dependencies minimal in CI.
"""
import ast
import logging
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SOURCE = Path(__file__).resolve().parents[1].joinpath("sector_report.py").read_text(encoding="utf-8")


def load_functions(*names, globals_=None):
    tree = ast.parse(SOURCE)
    found = {x.name: x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names}
    missing = set(names) - set(found)
    if missing:
        raise AssertionError(f"missing functions: {missing}")
    ns = {"Dict": Dict, "List": List, "Optional": Optional, "Tuple": Tuple}
    ns.update(globals_ or {})
    exec(compile(ast.Module(body=[found[name] for name in names], type_ignores=[]),
                 "sector_report.py", "exec"), ns)
    return ns


def bars(periods=220):
    dates = pd.bdate_range(end="2026-10-29", periods=periods)
    close = pd.Series(np.linspace(98, 115, periods), index=dates)
    return pd.DataFrame({"Close": close, "High": close+1,
                         "Low": close-1, "Volume": 1000.}, index=dates)


class ReportCalculations(unittest.TestCase):
    def test_52week_high_uses_intraday_high_not_only_closing_prices(self):
        frame = bars()
        frame.iloc[-30, frame.columns.get_loc("High")] = 170
        ns = load_functions("_ret", "index_metrics", globals_={
            "pd": pd, "np": np, "logger": logging.getLogger(__name__),
            "calc_rsi": lambda x: 55.0})
        row = ns["index_metrics"](frame, frame["Close"])
        expected = round((float(frame.Close.iloc[-1]) / 170-1)*100,1)
        self.assertEqual(row["from_high"], expected)
        self.assertLess(expected, -20)

    def test_different_index_session_is_dropped_not_mixed_with_nifty(self):
        frame = bars()
        stale = frame.iloc[:-1].copy()
        class Buzz:
            @staticmethod
            def market_regime(_):
                return {"text": "Nifty trend is weak", "supportive": False}
        class Engine:
            @staticmethod
            def compute_technicals(*a):
                return {"trend": "down"}
            @staticmethod
            def compute_risk(*a):
                return {}
        globals_ = {"pd": pd, "np": np, "logging": logging,
                    "logger": logging.getLogger(__name__),
                    "datetime": datetime, "IST": timezone(timedelta(hours=5,minutes=30)),
                    "INDICES": [
                        ("Nifty 50","^NSEI",None,"broad"),
                        ("Nifty Bank","^NSEBANK",None,"broad"),
                        ("Nifty IT","^CNXIT",None,"sector"),
                        ("Nifty Auto","^CNXAUTO",None,"sector"),
                        ("Nifty Pharma","^CNXPHARMA",None,"sector"),
                        ("Nifty FMCG","^CNXFMCG",None,"sector"),
                        ("Nifty Energy","^CNXENERGY",None,"sector"),
                        ("Nifty Realty","^CNXREALTY",None,"sector")],
                    "BENCH": "^NSEI", "SECTOR_STOCKS": {},
                    "stocks_buzz": Buzz, "eng": Engine,
                    "calc_rsi": lambda *_:55.0,
                    "rg": types.SimpleNamespace(strip_emoji=str)}
        ns = load_functions("_ret","index_metrics","_pctile","stock_metrics",
                            "_s","_takeaways","_sector_risk",
                            "_last_completed_session","_completed_bars",
                            "analyze",globals_=globals_)
        data = {"^NSEI":frame, "^NSEBANK":frame, "^CNXIT":stale,
                "^CNXAUTO":frame, "^CNXPHARMA":frame,"^CNXFMCG":frame,
                "^CNXENERGY":frame,"^CNXREALTY":frame}
        test_now = datetime(2026,10,30,17,0,tzinfo=globals_["IST"])
        report = ns["analyze"](data, now=test_now)
        self.assertEqual(len(report["idx"]),7)
        self.assertNotIn("Nifty IT",report["idx"])
        self.assertIn("Nifty Auto",report["idx"])
        self.assertEqual(report["index_coverage"],(7,8))
        self.assertIn("Nifty IT",report["missing_indices"])

    def test_neutral_lagging_metal_not_described_as_sector_leader(self):
        ns=load_functions("_s","_takeaways",globals_={
            "rg":types.SimpleNamespace(strip_emoji=str)})
        sector={}
        for name,score,stance,quad in [
            ("Nifty IT",88,"OVERWEIGHT","Leading"),
            ("Nifty Pharma",76,"OVERWEIGHT","Leading"),
            ("Nifty Metal",51,"NEUTRAL","Lagging"),
            ("Nifty Realty",3,"UNDERWEIGHT","Lagging"),
        ]:
            sector[name]={"name":name,"score":score,"stance":stance,
                          "quad":quad,"r1m":-1.,"r3m":-4.}
        R={"sec":sector,"regime":{"text":"market uncertain"},"p50_all":None,
           "p200_all":None,"breadth":{}}
        takeaways,heading=ns["_takeaways"](R)
        leaders=next(t for t in takeaways if "Sector leaders" in t)
        self.assertIn("IT",leaders)
        self.assertIn("Pharma",leaders)
        self.assertNotIn("Metal",leaders)
        self.assertIn("IT and Pharma lead",heading)

    def test_intraday_and_weekend_daily_bars_are_not_used(self):
        ns = load_functions("_last_completed_session","_completed_bars",globals_={
            "pd":pd,"datetime":datetime,"IST":timezone(timedelta(hours=5,minutes=30)),
            "logger":logging.getLogger(__name__)})
        frame=bars()
        tomorrow=pd.DataFrame({"Close":[140.],"High":[141.],"Low":[139.],
                               "Volume":[100.]},index=pd.DatetimeIndex(["2026-10-30"]))
        stacked=pd.concat([frame,tomorrow])
        self.assertEqual(ns["_last_completed_session"](
            datetime(2026,10,30,12,0,tzinfo=timezone(timedelta(hours=5,minutes=30)))),
            datetime(2026,10,29).date())
        out=ns["_completed_bars"]({"^NSEI":stacked},
                                  datetime(2026,10,30,12,0,
                                           tzinfo=timezone(timedelta(hours=5,minutes=30))))
        self.assertEqual(str(out["^NSEI"].index[-1].date()),"2026-10-29")

    def test_relative_3month_difference_uses_shared_trading_dates(self):
        ns=load_functions("_ret","index_metrics",globals_={
            "np":np,"pd":pd,"logger":logging.getLogger(__name__),
            "calc_rsi":lambda *_:55.0})
        frame=bars()
        nifty=frame["Close"]*2
        # Remove three dates from the benchmark's interior: a raw 63-bar
        # Nifty lookback now refers to a DIFFERENT date than the sector.
        sparse=nifty.drop(nifty.index[[40,50,60]])
        result=ns["index_metrics"](frame,sparse)
        common=frame["Close"].reindex(sparse.index).dropna()
        expected=round(ns["_ret"](common,63)-ns["_ret"](sparse,63),1)
        self.assertEqual(result["rel3m"],expected)

    def test_risk_is_rule_based_and_not_a_win_probability(self):
        ns=load_functions("_sector_risk")
        self.assertEqual(ns["_sector_risk"]({"trend":"Strong down","vol20":18},{"p50":72}),"HIGH")
        self.assertEqual(ns["_sector_risk"]({"trend":"Uptrend","vol20":23},{"p50":31}),"ELEVATED")
        self.assertEqual(ns["_sector_risk"]({"trend":"Uptrend","vol20":19},{"p50":71}),"MODERATE")

    def test_tied_sector_metrics_have_identical_percentiles(self):
        ns=load_functions("_pctile",globals_={"np":np})
        result=ns["_pctile"]({"IT":4.2,"Auto":4.2,"FMCG":-2.5})
        self.assertEqual(result["IT"],result["Auto"])
        self.assertEqual(result["FMCG"],0.0)
        self.assertEqual(ns["_pctile"]({"ONLY":3})["ONLY"],50.0)

    def test_equal_benchmark_relative_strength_is_neutral_not_leading(self):
        ns=load_functions("_ret","index_metrics",globals_={
            "pd":pd,"np":np,"logger":logging.getLogger(__name__),
            "calc_rsi":lambda *_:55.0})
        frame=bars()
        row=ns["index_metrics"](frame,frame["Close"])
        self.assertEqual(row["quad"],"Neutral")
        self.assertAlmostEqual(row["rs_ratio"],100,delta=.1)
        self.assertAlmostEqual(row["rs_mom"],100,delta=.1)

    def test_200ema_coverage_denominator_and_min_sector_guard_present(self):
        self.assertIn('"n200_stocks": count200',SOURCE)
        self.assertIn("with enough history above the 200-day EMA",SOURCE)
        self.assertIn("if sector_count < 5:",SOURCE)

    def test_movers_are_full_width_and_report_explicitly_warns_on_freshness(self):
        self.assertIn('("Top gainers", R["gainers"], True)', SOURCE)
        self.assertIn('("Top decliners", R["losers"], False)', SOURCE)
        self.assertNotIn('Table([[tbl(R["gainers"]', SOURCE)
        self.assertIn('22 * mm, 65 * mm, 28 * mm, 29 * mm, 29 * mm', SOURCE)
        self.assertIn('DATA DATE WARNING', SOURCE)
        self.assertIn('<b>Data quality:</b>', SOURCE)


if __name__ == "__main__":
    unittest.main()
