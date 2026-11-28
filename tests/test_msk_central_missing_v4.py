import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from msk_central_missing_v4 import build_recovery_bank, write_recovery_bank

DAY = date(2026,11,27)
ROW = {"isin":"INE192R01011","history_shape_valid":True,
       "latest_raw_ohlcv_match":True,"technical_calculated":True,
       "financial_status":"fresh_secondary_candidate",
       "official_quarter_comparison":"partial_match"}


class RecoveryBankTests(unittest.TestCase):
    def test_verified_technical_not_requeued_but_financial_pending(self):
        bank=build_recovery_bank({"stocks":{"DMART":ROW}},DAY)
        self.assertEqual(bank["pending_items"],1)
        self.assertEqual(bank["items"][0]["dataset"],"fundamental_evidence")
        self.assertFalse(bank["items"][0]["publish_allowed"])

    def test_missing_stock_does_not_remove_other_stocks(self):
        bad={**ROW,"isin":"INE002A01018","history_shape_valid":False,
             "latest_raw_ohlcv_match":False,"technical_calculated":False,
             "financial_status":"missing","official_quarter_comparison":"mismatch"}
        bank=build_recovery_bank({"stocks":{"DMART":ROW,"RELIANCE":bad}},DAY)
        self.assertEqual(bank["observed_stock_count"],2)
        self.assertEqual(bank["pending_items"],5)
        self.assertEqual(len([x for x in bank["items"] if x["symbol"]=="RELIANCE"]),4)
        self.assertTrue(all(not x["publish_allowed"] for x in bank["items"]))

    def test_empty_report_is_explicit_blocker(self):
        bank=build_recovery_bank({"target":10,"stocks":{}},DAY)
        self.assertEqual(bank["collection_blocker"],"roster_or_report_unavailable")
        self.assertEqual(bank["observed_stock_count"],0)

    def test_write_json_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"recovery.json"
            bank=build_recovery_bank({"stocks":{"DMART":ROW}},DAY)
            write_recovery_bank(p,bank)
            self.assertEqual(json.loads(p.read_text()),bank)
            self.assertEqual(list(Path(d).iterdir()),[p])


if __name__=="__main__":
    unittest.main()
