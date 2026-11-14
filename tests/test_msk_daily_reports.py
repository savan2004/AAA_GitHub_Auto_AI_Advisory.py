"""No provider calls: deterministic on-demand Telegram report acceptance."""
import re
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from index_constituents import IST
from msk_daily_reports import NAMES, parse_request, format_report, _intraday_quotes, _score


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
            if profile == "conservative":
                self.assertEqual(output.count("🟡 <b>Status: WAIT</b>"), 5)
            else:
                self.assertEqual(output.count(" | Status: <b>WAIT</b>"), 5)
            self.assertEqual(output.count("R:R = 1:2.0"), 5)
            self.assertIn("SCREENING COMPLETE — 5 RESEARCH CANDIDATES", output)
            if profile == "conservative":
                self.assertIn("🕒 As of 13 November 2026 close", output)
                self.assertIn("Nifty 50: −0.61%", output)
            else:
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

    def test_approved_large_cap_print_uses_real_session_evidence(self):
        # Five values taken from the dated 13 Nov 2026 NSE-universe research
        # records. Golden contract: match the owner-approved Telegram layout.
        facts = [
            ("PNB", 7.2, 13.0, 76.5, 36.1, 81.5, 2.02, 21.07,
             142.81, 144.09, 113.73, 204.81, 235.18),
            ("CANBK", 5.8, 16.1, 20.2, 22.0, 64.6, 0.8, 14.42,
             130.91, 133.57, 114.30, 172.09, 191.36),
            ("AXISBANK", 13.9, 13.1, 34.5, 26.5, 65.9, 0.58, 12.41,
             1331, 1363.12, 1194.01, 1701.34, 1870.45),
            ("SBIN", 11.7, 15.4, 14.5, 40, 72.3, 1.12, 13.76,
             1080, 1091.18, 941.09, 1391.36, 1541.45),
            ("UNIONBANK", 7.0, 15.7, 31.7, 28.9, 73.1, 0.75, 18.46,
             203, 204.81, 167.0, 280.43, 318.23),
        ]
        rows = {}
        for sym, pe, roe, growth, adx, rsi, volume, risk, close, entry, sl, t1, t2 in facts:
            rows[sym] = {
                "membership_hash": "084cdef817c34af6",
                "fund_status": "VALIDATED", "price_reason": "ok",
                "industry": "Financial Services",
                "fundamental": {"pe": pe, "roe": roe, "profit_growth": growth},
                "technical": {
                    "asof": "2026-11-13", "signal": "WAIT",
                    "entry": entry, "sl": sl, "t1": t1, "t2": t2,
                    "ltp": close, "risk_pct": risk, "adx": adx,
                    "rsi": rsi, "volx": volume,
                    "trend_confirmed": True, "breakout_confirmed": False,
                    "signal_reasons": [
                        "breakout_not_confirmed", "stop_risk_above_profile_limit"
                    ],
                },
            }
        breadth = {
            "session": "2026-11-13", "benchmark_change": -0.61,
            "valid": 499, "advances": 162, "declines": 336,
            "ma20": {"pct": 82.0, "count": 499},
        }
        output = format_report(
            "conservative", rows, {"membership_hash": "084cdef817c34af6"},
            "2026-11-13", now=datetime(2026, 11, 15, 0, 53, tzinfo=IST),
            breadth=breadth,
        )
        for line in (
            "📊 <b>MSK MARKET UPDATE</b>",
            "<b>LARGE CAP | Nifty 100</b>",
            "📅 15 November 2026",
            "🕒 As of 13 November 2026 close",
            "🎯 <b>TOP 5 RANKED CANDIDATES</b>",
            "<b>1. PNB | Score 77.5/100</b>",
            "Entry: ₹144.09–₹144.81 | SL: ₹113.73",
            "T1: ₹204.81 | T2: ₹235.18",
            "<b>2. CANBK | Score 76.5/100</b>",
            "<b>3. AXISBANK | Score 76.3/100</b>",
            "<b>4. SBIN | Score 75.8/100</b>",
            "<b>5. UNIONBANK | Score 73.8/100</b>",
            "R:R = 1:2.0 (T1) | 1:3.0 (T2)",
            "🔴 Nifty 500: 67.3% stocks declined.",
            "📊 82.0% of covered stocks remain above their 20-day average.",
            "All five candidates belong to financial services",
            "Watch PNB above ₹144.09, CANBK ₹133.57",
            "and UNIONBANK ₹204.81.",
            "<b>No BUY is active.</b>",
            "SCREENING COMPLETE — 5 RESEARCH CANDIDATES",
        ):
            self.assertIn(line, output)
        self.assertEqual(output.count(" | Score "), 5)
        self.assertEqual(output.count("🟡 <b>Status: WAIT</b>"), 5)
        self.assertEqual(output.count("R:R = 1:2.0 (T1) | 1:3.0 (T2)"), 5)
        self.assertNotIn("NOT CLAIMED", output)
        self.assertNotIn("READY", output)
        self.assertLessEqual(len(output), 4096)

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

    def test_real_liquidity_changes_only_risk_component(self):
        row = sample(0)
        financial = row["fundamental"]
        technical = row["technical"]
        old = _score(financial, technical)
        technical["turnover_20d"] = 20000000
        low = _score(financial, technical)
        technical["turnover_20d"] = 200000000
        high = _score(financial, technical)
        self.assertIsNotNone(old)
        self.assertLess(low, high)

    def test_intraday_quotes_require_live_session_and_current_timestamp(self):
        now = datetime.fromisoformat("2026-11-16T10:30:00+05:30")
        quotes = {
            "SBIN": {"quote_status": "recent_observation",
                     "quote_source": "NSE India", "price": 1111.2,
                     "quote_asof_ist": "2026-11-16T10:27:00+05:30"},
            "AXISBANK": {"quote_status": "recent_observation",
                         "quote_source": "Yahoo Finance v8", "price": 1500,
                         "quote_asof_ist": "2026-11-16T09:30:00+05:30"},
        }
        fake = types.ModuleType("data_engine")
        fake.get_verified_quote = lambda sym: quotes.get(sym)
        with patch.dict(sys.modules, {"data_engine": fake}):
            result = _intraday_quotes(["SBIN", "AXISBANK"], now)
            closed = _intraday_quotes(["SBIN"], datetime.fromisoformat(
                "2026-11-15T10:30:00+05:30"))
        self.assertIn("SBIN", result)
        self.assertNotIn("AXISBANK", result)
        self.assertEqual(closed, {})

        records = {sample(i)["symbol"]: sample(i) for i in range(5)}
        rendered = format_report("conservative", records,
                                 {"membership_hash":"1234567890abcdef"},
                                 "2026-11-13", now=now, quotes=result)
        self.assertIn("Quote ₹1,111.20 at 10:27 IST", rendered)
        self.assertIn("Live quotes checked 10:27 IST", rendered)
        self.assertNotIn("<b>Status: BUY</b>", rendered)
        self.assertIn("entry levels use the last completed close", rendered)

    def test_unsafe_html_escaped(self):
        rows = {sample(i)["symbol"]: sample(i) for i in range(5)}
        rows["SBIN"]["industry"] = "<script>AA</script>"
        out = format_report("conservative", rows, {"membership_hash":"1234567890abcdef"},
                            "2026-11-13", now=datetime(2026,11,15,tzinfo=IST))
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)


if __name__ == "__main__":
    unittest.main()
