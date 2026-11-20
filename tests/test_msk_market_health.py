"""Offline regression for dated institutional flows, derivatives and macro health."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import msk_market_health as h


SESSION = date(2026, 11, 13)

BREADTH = {
    "session": "2026-11-13", "valid": 499, "requested": 501,
    "coverage": 99.6, "advances": 162, "declines": 336, "unchanged": 1,
    "ma20": {"pct": 82.0, "count": 499}, "benchmark_change": -0.61,
    "trend_delta": -11.6,
}
FII_ROWS = [
    {"date": "13-Nov-2026", "category": "FII/FPI",
     "buyValue": "18,339.57", "sellValue": "12,603.02", "netValue": "5,736.55"},
    {"date": "13-Nov-2026", "category": "DII",
     "buyValue": "11,745.95", "sellValue": "13,999.53", "netValue": "-2,253.58"},
]


class MarketHealthFactorsTest(unittest.TestCase):
    def test_nse_cash_flow_requires_both_buy_and_sell_and_same_session(self):
        data = h.parse_fii_cash(FII_ROWS, SESSION)
        self.assertEqual(data["fii"]["net_cr"], 5736.55)
        self.assertEqual(data["dii"]["net_cr"], -2253.58)
        self.assertIsNone(h.parse_fii_cash(FII_ROWS[:1], SESSION))
        self.assertIsNone(h.parse_fii_cash(FII_ROWS, date(2026, 11, 12)))
        bad = [dict(row) for row in FII_ROWS]
        bad[0]["sellValue"] = None
        self.assertIsNone(h.parse_fii_cash(bad, SESSION))
        bad = [dict(row) for row in FII_ROWS]
        bad[0]["netValue"] = 0
        self.assertIsNone(h.parse_fii_cash(bad, SESSION))

    def test_fii_futures_is_positioning_not_cash_flow(self):
        csv_text = ("F&O - PARTICIPANT OPEN INTEREST\n"
                    "Client Type,Future Index Long,Future Index Short,"
                    "Future Stock Long,Future Stock Short\n"
                    "Client,172887,110311,4,8\n"
                    "FII,62546,118466,10,20\n"
                    "DII,54379,28098,13,16\n"
                    "TOTAL,289812,256485,12,18\n")
        parsed = h.parse_participant_oi(csv_text, SESSION)
        self.assertEqual(parsed["net"], -55920)
        self.assertEqual(parsed["long"], 62546)
        self.assertEqual(parsed["short"], 118466)
        self.assertEqual(parsed["ls_ratio"], 0.53)
        self.assertIn("not Nifty-only", parsed["scope"])
        self.assertIsNone(h.parse_participant_oi(csv_text.replace("FII,", "OTHER,"), SESSION))
        self.assertIsNone(h.parse_participant_oi("Access Denied", SESSION))

    def test_pcr_is_same_expiry_and_requires_genuine_timestamp(self):
        options = []
        for i in range(11):
            options.append({
                "expiryDate": "19-Nov-2026", "strikePrice": 25000 + i * 50,
                "CE": {"openInterest": 200}, "PE": {"openInterest": 100},
            })
        options.append({
            "expiryDate": "26-Nov-2026", "strikePrice": 24000,
            "CE": {"openInterest": 1000000}, "PE": {"openInterest": 1},
        })
        payload = {"records": {"timestamp": "13-Nov-2026 15:30:00", "data": options}}
        result = h.parse_option_pcr(payload, SESSION, "19-Nov-2026")
        self.assertEqual(result["pcr"], 0.5)
        self.assertEqual(result["strikes"], 11)
        self.assertEqual(result["call_oi"], 2200)
        self.assertIsNone(h.parse_option_pcr(payload, SESSION, "26-Nov-2026"))
        self.assertIsNone(h.parse_option_pcr(payload, date(2026, 11, 12),
                                               "19-Nov-2026"))
        options.append(dict(options[0]))
        self.assertIsNone(h.parse_option_pcr(payload, SESSION, "19-Nov-2026"))
        options.pop()
        options[0]["CE"]["openInterest"] = None
        result = h.parse_option_pcr(payload, SESSION, "19-Nov-2026")
        self.assertEqual(result["strikes"], 10)

    def test_official_policy_rate_parsing_fails_closed(self):
        rbi = ("<html><body>" + ("x" * 350) +
               "<td>Policy Repo Rate</td><td>: 5.50%</td></body></html>")
        fed = ("<html><body>" + ("x" * 350) +
               "Federal Funds Rate Target Range <span>3.75% to 4.00%</span>"
               "</body></html>")
        self.assertEqual(h.parse_official_rate(rbi, "rbi")["rate"], 5.5)
        self.assertEqual(h.parse_official_rate(fed, "fed")["upper"], 4.0)
        self.assertIsNone(h.parse_official_rate("403 Forbidden", "fed"))
        self.assertIsNone(h.parse_official_rate(rbi.replace("5.50%", "55.0%"), "rbi"))

    def test_last_close_and_five_session_crude_change_not_guessed(self):
        class FakeFrame:
            empty = False
            def iterrows(self):
                for i, close in enumerate([91, 92, 93, 94, 95, 96]):
                    yield (datetime(2026, 11, 6 + i, tzinfo=timezone.utc),
                           {"Close": close})
        result = h.parse_commodity_history(FakeFrame(), SESSION)
        self.assertEqual(result["price"], 96)
        self.assertEqual(result["change_5d"], 5.49)
        self.assertIsNone(h.parse_commodity_history(FakeFrame(),
                                                     date(2026, 12, 1)))

    def test_market_health_uses_all_valid_sources_without_buy_prediction(self):
        factors = {
            "session": SESSION.isoformat(),
            "cash": h.parse_fii_cash(FII_ROWS, SESSION),
            "futures": h.parse_participant_oi(
                "Client Type,Future Index Long,Future Index Short\n"
                "FII,62546,118466\n", SESSION),
            "pcr": {"session": SESSION.isoformat(), "pcr": 0.84,
                    "expiry": "19-Nov-2026", "strikes": 50},
            "rates": {"fed": {"lower": 3.75, "upper": 4},
                      "rbi": {"rate": 5.50}},
            "brent": {"price": 96.29, "session": SESSION.isoformat(),
                      "change_5d": 4.0},
            "fx": {"price": 94.20, "session": SESSION.isoformat(),
                   "change_5d": 0.5},
        }
        msg = h.format_market_health(BREADTH, factors)
        for expected in (
            "MSK TOTAL MARKET HEALTH | INDIA",
            "Verdict: 🟠 CAUTIOUS",
            "FII/FPI net: ₹+5,737 Cr",
            "DII net: ₹-2,254 Cr",
            "Net: -55,920 contracts",
            "Nifty OI PCR: 0.84",
            "US Fed target range: 3.75–4.00%",
            "RBI repo rate: 5.50%",
            "Brent futures: $96.29/bbl",
            "USD/INR: ₹94.20/USD",
            "Nifty 500: 67.3% declining",
            "5-session change in advance share: -11.6 pp",
        ):
            self.assertIn(expected, msg)
        self.assertNotIn("Status: BUY", msg)
        self.assertLess(len(msg), 3500)

    def test_403_or_missing_sources_do_not_turn_into_zero_values(self):
        msg = h.format_market_health(BREADTH, {"session": SESSION.isoformat()})
        for expected in ("FII/DII: Unavailable",
                         "positioning: Unavailable",
                         "put-call OI ratio: Unavailable",
                         "RBI repo rate: Latest source update unavailable",
                         "Brent crude futures: Recent dated quote unavailable"):
            self.assertIn(expected, msg)
        self.assertNotIn("₹+0 Cr", msg)
        self.assertNotIn("0.00 OI PCR", msg)
        self.assertNotIn("Net: +0 contracts", msg)
        self.assertLess(len(msg), 3500)

    def test_mismatched_session_never_converts_last_week_to_today(self):
        msg = h.format_market_health(BREADTH, {"session": "2026-11-12"})
        self.assertIn("sessions do not match", msg)

    def test_newer_pcr_must_be_from_today(self):
        now = datetime(2026, 11, 16, 11, tzinfo=h.IST)
        factors = {"session": SESSION.isoformat(),
                   "pcr": {"session": "2026-11-16", "pcr": .8, "strikes": 50,
                           "expiry": "19-Nov-2026"}}
        self.assertIn("newer than the breadth close", h.format_market_health(BREADTH, factors, now=now))
        for other_day in ("2026-11-15", "2026-11-17"):
            factors["pcr"]["session"] = other_day
            self.assertIn("put-call OI ratio: Unavailable", h.format_market_health(BREADTH, factors, now=now))

    def test_invalid_participation_withholds_verdict(self):
        factors = {"session": SESSION.isoformat()}
        for invalid in (dict(BREADTH, advances=999), dict(BREADTH, unchanged=0),
                        dict(BREADTH, ma20={"pct": 101})):
            report = h.format_market_health(invalid, factors)
            self.assertIn("inconsistent", report)
            self.assertNotIn("Verdict:", report)

    def test_brief_provider_error_does_not_prevent_fully_dated_breadth(self):
        with patch.object(h, "fetch_fii_cash", return_value=None), \
             patch.object(h, "fetch_participant_oi", return_value=None), \
             patch.object(h, "fetch_option_pcr", return_value=None), \
             patch.object(h, "fetch_policy_rates", return_value=None), \
             patch.object(h, "fetch_brent", return_value=None), \
             patch.object(h, "fetch_usdinr", return_value=None):
            h._CACHED, h._CACHE_AT = None, 0
            report = h.get_factors(SESSION)
            result = h.format_market_health(BREADTH, report)
        self.assertIn("67.3% declining", result)
        self.assertIn("FII/DII: Unavailable", result)
        self.assertEqual(report["session"], SESSION.isoformat())
        h._CACHED, h._CACHE_AT = None, 0


if __name__ == "__main__":
    unittest.main()
