"""No provider calls: deterministic on-demand Telegram report acceptance."""
import re
import unittest
from copy import deepcopy
from datetime import datetime

from index_constituents import IST
from msk_daily_reports import NAMES, parse_request, format_report


def sample(i, profile="conservative"):
    symbols = ("SBIN", "AXISBANK", "EICHERMOT", "INFY", "SUNPHARMA", "TCS")
    return {
        "symbol": symbols[i],
        "industry": ["Financial Services", "Financial Services", "Auto",
                     "IT", "Healthcare", "IT"][i],
        "membership_hash": "1234567890abcdef",
        "fund_status": "VALIDATED",
        "price_reason": "ok",
        "fundamental": {
            "pe": 12 + i * 4, "roe": 16 + i, "profit_growth": 15 + i,
        },
        "technical": {
            "asof": "2026-11-13", "signal": "WAIT",
            "entry": 100 + i * 20, "sl": (100+i*20) * 0.87,
            "t1": (100+i*20) * 1.26, "t2": (100+i*20) * 1.39,
            "ltp": 99 + i * 20, "risk_pct": 13.0,
            "adx": 24 + i, "rsi": 60 + i, "volx": 1.2,
            "trend_confirmed": True, "breakout_confirmed": False,
            "signal_reasons": ["breakout_not_confirmed", "stop_risk_above_profile_limit"],
        }
    }


class RoutingTests(unittest.TestCase):
    def test_all_aliases_and_pdf_is_not_hijacked(self):
        for text in ("TOP5", "top 5", "report", "/report", "lc",
                     "large cap report", "🏦 Large Cap Top 5"):
            self.assertEqual(parse_request(text), ("conservative",))
        for text in ("TOP5 MID", "mid cap report", "mc", "⚖️ Mid Cap Top 5"):
            self.assertEqual(parse_request(text), ("moderate",))
        for text in ("TOP5 SMALL", "small cap", "sc", "🚀 Small Cap Top 5"):
            self.assertEqual(parse_request(text), ("aggressive",))
        for text in ("ALL", "full report", "top 5 all", "/all"):
            self.assertEqual(parse_request(text), tuple(NAMES))
        self.assertEqual(parse_request("/report DLF"), ())
        self.assertEqual(parse_request("SBIN"), ())
        self.assertEqual(parse_request("📄 Report"), ())

    def test_same_evidence_always_same_rank_and_no_invented_buy(self):
        records = {sample(i)["symbol"]: sample(i) for i in range(6)}
        stats = {"membership_hash": "1234567890abcdef"}
        now = datetime(2026, 11, 15, 11, tzinfo=IST)
        breadth = {"session": "2026-11-13", "benchmark_change": -.61,
                   "advances": 161, "declines": 335, "valid": 497}
        for profile in NAMES:
            output = format_report(profile, records, stats, "2026-11-13",
                                   now=now, breadth=breadth)
            self.assertEqual(output.count(" | Score "), 5)
            self.assertEqual(output.count(" | SL: "), 5)
            self.assertEqual(output.count(" | T2: "), 5)
            self.assertEqual(output.count(" | Status: <b>WAIT</b>"), 5)
            self.assertEqual(output.count("R:R = 1:2.0"), 5)
            self.assertIn("SCREENING COMPLETE — 5 RESEARCH CANDIDATES", output)
            self.assertIn("As of 2026-11-13 — next update after market close", output)
            self.assertIn("Nifty 50: -0.61%", output)
            self.assertEqual(output, format_report(profile, records, stats,
                                                     "2026-11-13", now=now, breadth=breadth))
            self.assertLessEqual(len(output), 4096)
            self.assertNotIn("NOT CLAIMED", output)
            self.assertNotIn("Report status: READY", output)

    def test_stale_prepared_buy_is_reduced_to_wait(self):
        rows = {sample(i)["symbol"]: sample(i) for i in range(5)}
        item = rows["SBIN"]
        item["technical"].update(signal="CONDITIONAL BUY", risk_pct=13.0,
                                  breakout_confirmed=True, trend_confirmed=True)
        text = format_report("aggressive", rows, {"membership_hash":"1234567890abcdef"},
                             "2026-11-13", now=datetime(2026,11,15,tzinfo=IST))
        self.assertIn("1. SBIN", text)
        self.assertNotIn("Status: <b>BUY</b>", text)

    def test_quality_missing_sl_does_not_invent_price(self):
        rows = {sample(i)["symbol"]: sample(i) for i in range(5)}
        rows["SBIN"]["technical"]["sl"] = None
        msg = format_report("conservative", rows, {"membership_hash":"1234567890abcdef"},
                            "2026-11-13", now=datetime(2026,11,15,tzinfo=IST))
        self.assertIn("NO QUALIFYING SETUP TODAY", msg)
        self.assertIn("4/5", msg)
        self.assertNotIn("₹None", msg)
        self.assertNotIn("SBIN | Score", msg)

    def test_membership_mismatch_and_bad_valuation_fail_closed(self):
        rows = {sample(i)["symbol"]: sample(i) for i in range(5)}
        rows["SBIN"]["membership_hash"] = "invalid"
        rows["AXISBANK"]["fundamental"]["pe"] = None
        out = format_report("conservative", rows, {"membership_hash":"1234567890abcdef"},
                            "2026-11-13", now=datetime(2026,11,15,tzinfo=IST))
        self.assertIn("3/5", out)
        self.assertNotIn("SBIN | Score", out)
        self.assertNotIn("AXISBANK | Score", out)

    def test_unsafe_html_escaped(self):
        rows = {sample(i)["symbol"]: sample(i) for i in range(5)}
        rows["SBIN"]["industry"] = "<script>AA</script>"
        out = format_report("conservative", rows, {"membership_hash":"1234567890abcdef"},
                            "2026-11-13", now=datetime(2026,11,15,tzinfo=IST))
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)


if __name__ == "__main__":
    unittest.main()
