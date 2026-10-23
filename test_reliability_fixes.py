"""Offline regression tests. Run: TELEGRAM_TOKEN=fake WEBHOOK_URL=https://example.com python -m unittest test_reliability_fixes"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("WEBHOOK_URL", "https://example.com")
from market_data import _clean_frame
from data_validation import validate_fundamental, get_stock_news_robust
from research_reasoning import build_reasoning
import research_engine as engine


class ReliabilityFixTests(unittest.TestCase):
    def test_no_synthetic_candles(self):
        partial = pd.DataFrame({"Close": [100.0, 110.0]})
        self.assertIsNone(_clean_frame(partial))

    def test_negative_and_inconsistent_ohlcv_removed(self):
        df = pd.DataFrame({
            "Open": [100., 100., 100.], "High": [110., 90., 110.],
            "Low": [95., 95., 95.], "Close": [105., 105., -1.],
            "Volume": [100., 100., 100.]})
        got = _clean_frame(df)
        self.assertEqual(len(got), 1)
        self.assertEqual(float(got.iloc[0]["Close"]), 105.0)

    def test_null_volume_rejected(self):
        df = pd.DataFrame({"Open": [100], "High": [105], "Low": [95],
                           "Close": [101], "Volume": [None]})
        self.assertIsNone(_clean_frame(df))

    def test_real_zero_fundamental_is_valid(self):
        self.assertEqual(validate_fundamental('ABC','roe',0),0.0)
        for x in ("N/A",float('nan'),float('inf'),None,"-",True):
            self.assertIsNone(validate_fundamental('ABC','roe',x))

    def test_missing_news_no_fake_optimism(self):
        # market_news dependency may not be installed; an exception must not cause invented news
        with patch.dict(sys.modules, {'market_news': None}):
            self.assertEqual(get_stock_news_robust('ABC'), '')

    def test_missing_fundamentals_veto_even_if_technicals_excel(self):
        fake = {'Trend': 95, 'Momentum': 97, 'Quality': None,
                'Valuation': None, 'Growth': None, 'Risk': 90}
        with patch.object(engine,'_trend_score',return_value=fake['Trend']),\
             patch.object(engine,'_momentum_score',return_value=fake['Momentum']),\
             patch.object(engine,'_quality_score',return_value=None),\
             patch.object(engine,'_valuation_score',return_value=None),\
             patch.object(engine,'_growth_score',return_value=None),\
             patch.object(engine,'_risk_score',return_value=fake['Risk']):
            scores=engine.compute_scores({}, {}, {})
        self.assertEqual(scores['stance'],'INSUFFICIENT EVIDENCE')
        self.assertTrue(scores['veto'])
        self.assertTrue(scores['insufficient_evidence'])
        self.assertEqual(scores['fundamental_pillars'],0)

    def test_two_fundamental_pillars_allow_scoring(self):
        with patch.object(engine,'_trend_score',return_value=80),\
             patch.object(engine,'_momentum_score',return_value=80),\
             patch.object(engine,'_quality_score',return_value=80),\
             patch.object(engine,'_valuation_score',return_value=80),\
             patch.object(engine,'_growth_score',return_value=None),\
             patch.object(engine,'_risk_score',return_value=80):
            scores=engine.compute_scores({}, {}, {"has_statements":True,"pe":20})
        self.assertFalse(scores['insufficient_evidence'])
        self.assertEqual(scores['fundamental_pillars'],2)
        self.assertGreaterEqual(scores['coverage'],70)

    def test_missing_benchmark_reports_no_verified_regime(self):
        r = build_reasoning(None, {'returns': {}}, {}, {'insufficient_evidence': True}, 'Utilities')
        self.assertEqual(r['market_regime'],'NOT VERIFIED')
        self.assertFalse(r['publication_eligible'])
        self.assertTrue(r['data_gaps'])

    def test_topdown_and_bottomup_have_evidence_and_countercase(self):
        n = pd.DataFrame({'Close':[float(i+100) for i in range(210)]})
        t = {'returns': {'3M': {'rel': 8.2}}}
        f = {'roe': 18.0, 'ocf_pat': 0.25, 'de': 1.4, 'pe': 24., 'peer_pe': 20., 'has_statements': True}
        r = build_reasoning(n,t,f,{'insufficient_evidence': False},'Power')
        self.assertEqual(r['market_regime'],'BENCHMARK UPTREND')
        self.assertTrue(any('outperformed' in x for x in r['top_down_evidence']))
        self.assertTrue(any('ROE' in x for x in r['bottom_up_evidence']))
        self.assertTrue(any('Debt/equity' in x for x in r['counter_thesis']))
        self.assertFalse(r['publication_eligible'])

if __name__ == '__main__':
    unittest.main()
