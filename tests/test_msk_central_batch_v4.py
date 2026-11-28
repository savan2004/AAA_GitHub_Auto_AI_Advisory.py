import unittest
from scripts.msk_central_100_pilot import plan_batches,summarize

class BatchStageTests(unittest.TestCase):
    def setUp(self):
        self.symbols=[f"S{i:03}" for i in range(100)]
        self.roster={s:"identity" for s in self.symbols}
    def test_ten_batches_without_dropped_members(self):
        batches=plan_batches(self.symbols,self.roster)
        self.assertEqual([len(b) for b in batches],[10]*10)
        self.assertEqual(set(s for b in batches for s in b),set(self.symbols))
    def test_missing_or_duplicate_members_fail_closed(self):
        for symbols,roster in ((self.symbols[:-1],self.roster),
                               (self.symbols[:-1]+self.symbols[:1],self.roster),
                               (self.symbols,{s:"id" for s in self.symbols[:-1]})):
            with self.assertRaises(ValueError):
                plan_batches(symbols,roster)
    def test_partial_never_reported_as_complete(self):
        row={"history_shape_valid":True,"latest_raw_ohlcv_match":True,
             "technical_calculated":True,"financial_status":"fresh_secondary_candidate"}
        rows={s:dict(row) for s in self.symbols}
        self.assertEqual(summarize(rows,completed=100)[1],"pilot_observed")
        rows[self.symbols[0]]["technical_calculated"]=False
        counts,status=summarize(rows,completed=100)
        self.assertEqual(counts["technical_calculated"],99)
        self.assertEqual(status,"pilot_incomplete")
        self.assertEqual(summarize(rows,completed=99)[1],"pilot_incomplete")

class IndependentRecoveryTests(unittest.TestCase):
    def test_only_independent_usable_candidate_is_returned(self):
        from scripts.msk_central_10_pilot import recover_independent_history
        from unittest.mock import Mock
        from datetime import date
        day=date(2026,11,27)
        recovery=object()
        verified=object()
        source=Mock(return_value=verified)
        result=recover_independent_history("TECHM",day,recovery,
                    usable=lambda frame:frame is verified,alternate=source)
        self.assertIs(result,verified)
        source.assert_called_once_with("TECHM",day,recovery)
        self.assertIsNone(recover_independent_history("VAML",day,recovery,
                    usable=lambda frame:False,alternate=source))
        self.assertIsNone(recover_independent_history("TECHM",day,recovery,
                    usable=lambda frame:False,alternate=Mock(side_effect=ValueError("source unavailable"))))
