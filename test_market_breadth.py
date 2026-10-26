"""Offline breadth regression tests; no live market calls."""
import io
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import numpy as np
import pandas as pd
from market_breadth import calculate_breadth, completed_prices, format_breadth, IST

def frame(n=300, up=True):
    idx = pd.bdate_range(end='2026-10-23',periods=n)
    close = np.linspace(100,200,n) if up else np.linspace(200,100,n)
    return pd.DataFrame(dict(Open=close,High=close+1,Low=close-1,Close=close,Volume=1000),index=idx)

NOW = datetime(2026,10,24,18,tzinfo=IST)

class BreadthTests(unittest.TestCase):
    def test_participation_denominators_and_short_history(self):
        f=frame();down=frame(up=False);short=frame(30)
        r=calculate_breadth({'UP':f,'DOWN':down,'SHORT':short,'STALE':f.iloc[:-1]},
                            ['UP','DOWN','SHORT','STALE','MISSING'],f,now=NOW)
        self.assertEqual((r['advances'],r['declines'],r['valid']),(2,1,3))
        self.assertEqual(r['coverage'],60)
        self.assertEqual(r['ma200']['count'],2)
        self.assertEqual(r['ma200']['pct'],50)
        self.assertEqual(r['ma20']['count'],3)
        self.assertEqual(r['high_low_count'],2)
        self.assertEqual((r['highs'],r['lows']),(1,1))
        self.assertEqual(r['trend_count'],3)
        self.assertIn('Insufficient coverage',r['interpretation'])

    def test_partial_session_excluded_before_buffer(self):
        f=frame();now=datetime(2026,10,23,15,45,tzinfo=IST)
        self.assertEqual(completed_prices(f,now).index[-1].date(),date(2026,10,22))
        self.assertEqual(completed_prices(f,now.replace(hour=16)).index[-1].date(),date(2026,10,23))

    def test_missing_common_session_and_invalid_close_excluded(self):
        f=frame();bad=f.copy();bad.iloc[-1,bad.columns.get_loc('Close')]=np.nan
        r=calculate_breadth({'BAD':bad,'OLD':f.iloc[:-1]},['BAD','OLD'],f,now=NOW)
        self.assertEqual(r['valid'],0)
        self.assertIsNone(r['ma20']['pct'])

    def test_divergence_and_stale_session(self):
        f=frame();d=frame(up=False)
        r=calculate_breadth({'A':d,'B':d,'C':f},['A','B','C'],f,now=NOW)
        self.assertIn('Index rising',r['interpretation'])
        old=calculate_breadth({'A':f},['A'],f,now=NOW+timedelta(days=15))
        self.assertTrue(old['stale']);self.assertIn('Stale',old['interpretation'])

    def test_five_session_fixed_cohort_and_html(self):
        f=frame();hole=f.copy();hole.iloc[-4,hole.columns.get_loc('Close')]=np.nan
        r=calculate_breadth({'A':f,'B':hole},['A','B'],f,{'A':'A&B'},now=NOW)
        self.assertEqual(r['trend_count'],1)
        self.assertEqual(r['trend_delta'],0)
        r.update(source='Sample <test>',fetched='now')
        self.assertIn('Sample &lt;test&gt;',format_breadth(r))

    def test_partial_download_retries_only_missing_symbols_serially(self):
        import market_data as md
        with patch('yfinance.download',return_value=pd.DataFrame()) as download,patch.object(md,'_split_multi',side_effect=[{'A.NS':frame()},{'B.NS':frame()}]),patch.object(md.time,'sleep'):
            got=md._download_chunk(['A.NS','B.NS'],'1mo',retries=1,adjusted=True)
        self.assertEqual(set(got),{'A.NS','B.NS'})
        self.assertEqual(download.call_args_list[1].kwargs['tickers'],'B.NS')
        self.assertFalse(download.call_args_list[1].kwargs['threads'])
    def test_adjusted_cache_separated(self):
        import market_data as md
        md._cache.clear()
        with patch.object(md,'_download_chunk',return_value={'A.NS':frame()}) as fetch:
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=True)
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=True)
            self.assertEqual(fetch.call_count,1)
            md.bulk_history(['A'],'2y',cache_ttl=900,adjusted=False)
            self.assertEqual(fetch.call_count,2)
        md._cache.clear()


if __name__ == '__main__':
    unittest.main()
