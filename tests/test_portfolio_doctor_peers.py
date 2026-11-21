"""Nifty 500 Doctor peer research: membership, fresh data, disclosure and PDF."""
import io
import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
from pypdf import PdfReader

from portfolio_doctor_live import IST
from portfolio_doctor_peers import official_nifty500_industries, research_industry_peers
from portfolio_doctor_report import build_pdf_bytes


NOW = datetime(2026, 11, 18, 15, 4, tzinfo=IST)


def members():
    # Synthetic 500-stock fixture, not a claim about actual constituents.
    result = {"SAMPLE{:03d}".format(i): "Other" for i in range(496)}
    result.update(SJVN="Power", NTPC="Power", POWERGRID="Power", JSWENERGY="Power")
    return result


def candles():
    dates = pd.bdate_range(end="2026-11-17", periods=130)
    return pd.DataFrame({
        "Open": [100.0] * 130, "High": [101.0] * 130,
        "Low": [99.0] * 130, "Close": [100.0] * 130,
        "Volume": [1000000.0] * 130,
    }, index=dates)


class Nifty500ResearchTests(unittest.TestCase):
    def test_official_index_roster_requires_strict_schema_unique_members(self):
        rows = ["Symbol,Industry,Company Name"] + [
            "SAMPLE{:03d},Power,Verified illustrative company number {}".format(i, i)
            for i in range(500)]
        csv = ("\n".join(rows) + "\n").encode()
        self.assertGreater(len(csv), 10000)
        get = lambda *a, **kw: SimpleNamespace(ok=True, content=csv)
        roster = official_nifty500_industries(get=get)
        self.assertEqual(len(roster), 500)
        self.assertEqual(roster["SAMPLE000"], "Power")
        self.assertEqual(official_nifty500_industries(
            get=lambda *a, **kw: SimpleNamespace(ok=True, content=
                csv.replace(b"SAMPLE499", b"SAMPLE000"))), {})
        self.assertEqual(official_nifty500_industries(
            get=lambda *a, **kw: SimpleNamespace(ok=False, content=csv)), {})
        self.assertEqual(official_nifty500_industries(
            get=lambda *a, **kw: SimpleNamespace(ok=True, content=b"<html>oops</html>")), {})

    def test_same_industry_candidates_must_be_fresh_verified_and_not_held(self):
        checked = []
        def history_fetcher(symbols):
            checked.extend(symbols)
            result = {sym: candles() for sym in symbols}
            # One invalid/stale history must be excluded, not assumed bearish.
            result["POWERGRID"] = candles().iloc[:-1]
            return result
        def evaluator(sym, frame, profile, asof):
            self.assertEqual(profile, "moderate")
            return {"sym": sym, "score": 85 if sym == "NTPC" else 72,
                    "ltp": 100.0, "rsi": 57.0, "adx": 25.0,
                    "asof": "2026-11-17"}
        result = research_industry_peers(
            [{"symbol": "SJVN"}], members=members(),
            history_fetcher=history_fetcher, evaluator=evaluator, now=NOW)
        self.assertEqual(result["status"], "SCREENED")
        self.assertEqual(result["universe"], 500)
        self.assertEqual(result["pool"], 3)
        self.assertEqual(result["screened"], 3)
        self.assertEqual(result["fresh"], 2)
        self.assertEqual(result["qualified"], 2)
        self.assertEqual([r["symbol"] for r in result["candidates"]], ["NTPC", "JSWENERGY"])
        self.assertNotIn("SJVN", checked)
        self.assertNotIn("POWERGRID", [r["symbol"] for r in result["candidates"]])
        self.assertEqual(result["comparisons"], [])
        for candidate in result["candidates"]:
            self.assertEqual(candidate["date"], "2026-11-17")
            self.assertEqual(len(candidate["return_window"]), 21)
            self.assertEqual(candidate["return_window"][-1], candidate["date"])
            self.assertIsNotNone(candidate["return20"])

    def test_no_index_membership_means_no_fabricated_candidates_or_fetch(self):
        def prohibited(*a, **kw):
            self.fail("unverified membership triggered market downloads")
        result = research_industry_peers(
            [{"symbol": "SJVN"}], members={}, history_fetcher=prohibited, now=NOW)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertFalse(result["candidates"])
        self.assertFalse(result["comparisons"])

    def test_report_discloses_partial_screen_and_no_swap_signal(self):
        holding = {"symbol": "SJVN", "qty": 2, "buy": 40, "close": 55,
                   "date": "2026-11-17", "ema20": 54, "ema50": 52,
                   "rsi14": 61, "high20": 57, "low20": 49}
        peers = {"status": "SCREENED", "universe": 500, "pool": 25,
                 "screened": 8, "fresh": 6, "qualified": 2,
                 "candidates": [{"symbol": "NTPC", "industry": "Power",
                                 "score": 85, "close": 320,
                                 "date": "2026-11-17"}]}
        data = build_pdf_bytes([holding], peers=peers)
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
        self.assertIn("NIFTY 500 INDUSTRY-PEER WATCHLIST", text)
        self.assertIn("NTPC", text)
        self.assertIn("Screened: 8", text)
        self.assertIn("NOT a ranking of all 500 companies", text)
        self.assertIn("no automatic replacement or order", text)

    def test_report_still_returns_pdf_when_peer_data_unavailable(self):
        holding = {"symbol": "SJVN", "qty": 2, "buy": 40, "close": 55,
                   "date": "2026-11-17", "ema20": 54, "ema50": 52,
                   "rsi14": 61, "high20": 57, "low20": 49}
        data = build_pdf_bytes([holding])
        self.assertTrue(data.startswith(b"%PDF-"))
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
        self.assertIn("Unavailable: official membership", text)


if __name__ == "__main__":
    unittest.main()
