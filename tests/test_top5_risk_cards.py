"""Offline Top-5 stock ratings and risk/target safety regressions."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta,date
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

import top5_technical_ratings as technical
import top5_company_ratings as finance

IST=timezone(timedelta(hours=5,minutes=30))
NOW=datetime(2026,10,29,17,0,tzinfo=IST)
D=date(2026,10,29)


def candles(up=True,stale=False,days=160):
    t=np.arange(days,dtype=float)
    trend=(.3 if up else -.2)*t
    close=125+trend+1.9*np.sin(t*.7)
    dates=pd.bdate_range(end="2026-10-01" if stale else "2026-10-29",periods=days)
    return pd.DataFrame({"Close":close,"High":close+1.8,
                        "Low":close-1.8,"Volume":np.repeat(2500000.,days)},
                        index=dates)


STMT={
 "source":"Screener.in",
 "years":[
 {"label":"Mar 2023","net_profit":100,"revenue":400,"borrowings":60,"networth":300},
 {"label":"Mar 2024","net_profit":120,"revenue":460,"borrowings":65,"networth":330},
 {"label":"Mar 2025","net_profit":150,"revenue":540,"borrowings":55,"networth":360},
 {"label":"Mar 2026","net_profit":195,"revenue":640,"borrowings":50,"networth":420}],
 "quarters":[{"label":"Mar 2026"},{"label":"Jun 2026"}],
}
FV={"roe":21.0,"pe":22.0,"eps":10.0,"pb":3.0}


class TechWatchlistTests(unittest.TestCase):
    def test_sideways_or_bearish_still_rated_without_false_buy_signal(self):
        for up in (True,False):
            row=technical.technical_candidate("TEST",candles(up=up),"aggressive",NOW)
            self.assertIsNotNone(row)
            self.assertTrue(0 <= row["tech_score"] <= 100)
            self.assertLess(row["sl"],row["entry"])
            self.assertLess(row["entry"],row["t1"])
            self.assertLess(row["t1"],row["t2"])
            risk=row["entry"]-row["sl"]
            self.assertAlmostEqual((row["t1"]-row["entry"])/risk,2,places=2)
            self.assertAlmostEqual((row["t2"]-row["entry"])/risk,3,places=2)
            self.assertEqual(row["asof"],"2026-10-29")
        self.assertEqual(technical.technical_candidate("TEST",candles(False),"aggressive",NOW)["signal"],"WAIT")

    def test_stale_missing_or_invalid_prices_get_no_rating(self):
        self.assertIsNone(technical.technical_candidate("TEST",candles(stale=True),"aggressive",NOW))
        self.assertIsNone(technical.technical_candidate("TEST",candles(days=40),"aggressive",NOW))
        f=candles()
        f.iloc[-1,f.columns.get_loc("Close")]=float("nan")
        self.assertIsNone(technical.technical_candidate("TEST",f,"aggressive",NOW))

    def test_pre_1600_candle_is_excluded(self):
        row=technical.technical_candidate("TEST",candles(),"aggressive",datetime(2026,10,29,13,0,tzinfo=IST))
        self.assertEqual(row["asof"],"2026-10-28")


class FinanceRatingTests(unittest.TestCase):
    def test_fully_verified_quality_score_and_dated_evidence(self):
        row=finance.rate_financial_evidence("aggressive",FV,STMT,"Industrials",D)
        self.assertEqual(row["fund_status"],"VALIDATED")
        self.assertIsInstance(row["fund_score"],(int,float))
        self.assertEqual(row["quarter"],"2026-06-30")

    def test_earnings_missing_never_fabricated_but_research_card_can_show_ratios(self):
        fv=dict(FV,eps=None)
        row=finance.rate_financial_evidence("aggressive",fv,STMT,"Industrials",D)
        self.assertNotEqual(row["fund_status"],"VALIDATED")
        self.assertIsNone(row.get("eps"))
        self.assertEqual(row["fund_status"],"PRELIMINARY")
        self.assertEqual(row["fund_reason"],"earnings_missing_or_loss")
        self.assertGreater(row["fund_known"],2)

    def test_completely_missing_returns_unverified_not_zero_score(self):
        row=finance.rate_financial_evidence("aggressive",{},None,"Industrials",D)
        self.assertEqual(row["fund_status"],"UNVERIFIED")
        self.assertIsNone(row["fund_score"])
        self.assertIsNone(row["roe"])

    def test_bank_does_not_use_corporate_debt_equity(self):
        stm=deepcopy(STMT)
        stm["years"][-1]["borrowings"]=5000
        row=finance.rate_financial_evidence("moderate",FV,stm,"Banking",D)
        self.assertEqual(row["fund_status"],"VALIDATED")
        self.assertIsNone(row["de"])
        self.assertIsNotNone(row["pb"])


if __name__=="__main__":
    unittest.main()
