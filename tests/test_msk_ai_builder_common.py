import unittest
from dataclasses import replace
from unittest.mock import patch
import pandas as pd
from msk_ai_builder_common import financial_review,technical_review,review_companies
from msk_ai_builder_evidence import EvidenceError
from test_msk_ai_builder_bank import company,NOW,CLOSE
from test_msk_ai_builder_evidence import fixture

def collector(symbol):
    raw=fixture()
    raw["_source_url"]=f"https://www.screener.in/company/{symbol}/consolidated/"
    return raw,{"roe":18,"pe":25,"pb":2,"_screener_scope":"consolidated"}

class CommonTests(unittest.TestCase):
    def test_shared_financial_gate(self):
        c=company()
        result=financial_review(c,now=NOW,collector=collector)
        self.assertEqual(result["status"],"SECONDARY_ONLY")
        self.assertEqual(result["pe"],25)
        self.assertEqual(set(result["profiles"]),{"conservative","moderate","aggressive"})
        self.assertEqual(result["ratio_basis"],"PROVIDER_REPORTED_NOT_PRICE_DATE_RECONCILED")
        def wrong(symbol):
            raw,ratios=collector(symbol)
            ratios["_screener_scope"]="standalone"
            return raw,ratios
        with self.assertRaisesRegex(EvidenceError,"RATIO_SCOPE"):
            financial_review(c,now=NOW,collector=wrong)
        def changed(symbol):
            raw,ratios=collector(symbol)
            raw["quarters"][-1]["net_profit"]+=1
            return raw,ratios
        with self.assertRaisesRegex(EvidenceError,"STATEMENTS_CHANGED"):
            financial_review(c,now=NOW,collector=changed)
    def test_technical_reuses_top5_and_matches_eod(self):
        c=company()
        dates=pd.bdate_range(end=CLOSE,periods=100)
        close=[100+i*(23.45/99) for i in range(100)]
        frame=pd.DataFrame({"Close":close,"High":[x+1 for x in close],
              "Low":[x-1 for x in close],"Volume":[300000]*100},index=dates)
        result=technical_review(c,frame,now=NOW)
        self.assertEqual(result["asof"],CLOSE.isoformat())
        self.assertIn("technical_score",result)
        self.assertNotIn("signal",result)
        self.assertNotIn("entry",result)
        with self.assertRaisesRegex(EvidenceError,"HISTORY_CLOSE_MISMATCH"):
            technical_review(replace(c,close_rupees=100),frame,now=NOW)
        with self.assertRaisesRegex(EvidenceError,"HISTORY_DATE_UNVERIFIED"):
            technical_review(c,frame.iloc[:-1],now=NOW)
        self.assertEqual(review_companies([c],{},now=NOW,collector=collector)["companies"][c.member.symbol]["technical"]["status"],"WITHHELD")
