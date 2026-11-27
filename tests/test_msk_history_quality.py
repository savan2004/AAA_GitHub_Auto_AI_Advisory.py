import unittest
from unittest.mock import patch
import pandas as pd
from msk_history_quality import clean_daily_history
import market_data as md

def frame():
    return pd.DataFrame({"Open":[100.,101.],"High":[103.,104.],"Low":[99.,100.],"Close":[102.,103.],"Volume":[1000.,1100.]},index=pd.date_range("2026-11-19",periods=2))

class HistoryQualityTests(unittest.TestCase):
    def test_conflicting_duplicates_fail_closed(self):
        a=frame()
        b=a.iloc[:1].copy()
        b.loc[:,"Close"]=101.
        self.assertIsNone(clean_daily_history(pd.concat([a,b])))
        self.assertEqual(len(clean_daily_history(pd.concat([a,a]))),2)

    def test_impossible_bar_removed(self):
        a=frame()
        a.iloc[0,a.columns.get_loc("High")]=50.
        self.assertEqual(len(clean_daily_history(a)),1)

    def test_adjusted_never_recovers_from_unadjusted_source(self):
        with patch.object(md,"_download_chunk",return_value={}), patch.object(md,"_recover_missing_symbol") as fallback:
            self.assertEqual(md.bulk_history(["SBIN"],adjusted=True,retries=0),{})
            fallback.assert_not_called()

    def test_aliases_share_download_not_mutable_result(self):
        with patch.object(md,"_download_chunk",return_value={"SBIN.NS":frame()}) as fetch, patch.object(md,"_recover_missing_symbol") as fallback:
            out=md.bulk_history(["SBIN","SBIN.NS"],retries=0)
            self.assertEqual(set(out),{"SBIN","SBIN.NS"})
            self.assertEqual(fetch.call_args.args[0],["SBIN.NS"])
            fallback.assert_not_called()
            out["SBIN"].iloc[0,0]=999
            self.assertEqual(out["SBIN.NS"].iloc[0,0],100)
