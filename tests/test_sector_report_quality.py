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

import numpy as np
import pandas as pd

SOURCE = Path(__file__).resolve().parents[1].joinpath("sector_report.py").read_text(encoding="utf-8")


def load_functions(*names, globals_=None):
    tree = ast.parse(SOURCE)
    found = {x.name: x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names}
    missing = set(names) - set(found)
    if missing:
        raise AssertionError(f"missing functions: {missing}")
    ns = dict(globals_ or {})
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
            "pd": pd, "logger": logging.getLogger(__name__),
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
                        ("Nifty Pharma","^CNXPHARMA",None,"sector")],
                    "BENCH": "^NSEI", "SECTOR_STOCKS": {},
                    "stocks_buzz": Buzz, "eng": Engine,
                    "calc_rsi": lambda *_:55.0,
                    "rg": types.SimpleNamespace(strip_emoji=str)}
        ns = load_functions("_ret","index_metrics","_pctile","stock_metrics",
                            "_s","_takeaways","analyze",globals_=globals_)
        data = {"^NSEI":frame, "^NSEBANK":frame, "^CNXIT":stale,
                "^CNXAUTO":frame, "^CNXPHARMA":frame}
        report = ns["analyze"](data)
        self.assertEqual(len(report["idx"]),4)
        self.assertNotIn("Nifty IT",report["idx"])
        self.assertIn("Nifty Auto",report["idx"])
        self.assertEqual(report["index_coverage"],(4,5))
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

    def test_movers_are_full_width_and_report_explicitly_warns_on_freshness(self):
        self.assertIn('("Top gainers", R["gainers"], True)', SOURCE)
        self.assertIn('("Top decliners", R["losers"], False)', SOURCE)
        self.assertNotIn('Table([[tbl(R["gainers"]', SOURCE)
        self.assertIn('22 * mm, 65 * mm, 28 * mm, 29 * mm, 29 * mm', SOURCE)
        self.assertIn('DATA DATE WARNING', SOURCE)
        self.assertIn('Index coverage:', SOURCE)


if __name__ == "__main__":
    unittest.main()
