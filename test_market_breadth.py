"""Offline breadth regression tests; no live market calls."""
import io
import threading
import time
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

    def test_monday_must_not_republish_previous_friday_as_current(self):
        from market_breadth import expected_completed_session
        monday = datetime(2026, 10, 26, 20, 12, tzinfo=IST)
        friday_prices = frame()
        stale = calculate_breadth({'UP': friday_prices}, ['UP'], friday_prices, now=monday)
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['session'], '2026-10-23')
        self.assertEqual(stale['expected_session'], '2026-10-26')
        self.assertEqual(expected_completed_session(monday), date(2026, 10, 26))
        fresh_prices = frame()
        fresh_prices.index = pd.bdate_range(end='2026-10-26', periods=len(fresh_prices))
        current = calculate_breadth({'UP': fresh_prices}, ['UP'], fresh_prices, now=monday)
        self.assertFalse(current['stale'])
        self.assertEqual(current['session'], '2026-10-26')

    def test_strong_daily_breadth_but_weak_trend_is_not_a_reversal(self):
        benchmark = frame(up=False)
        series = {}
        for i in range(4):
            df = frame(up=False).copy()
            if i < 3:
                df.iloc[-1, df.columns.get_loc('Close')] = df['Close'].iloc[-2] * 1.008
            series[f'STOCK{i}'] = df
        r = calculate_breadth(series, list(series), benchmark, now=NOW)
        self.assertEqual((r['advances'], r['declines']), (3, 1))
        self.assertLess(r['ma20']['pct'], 40)
        self.assertLess(r['ma50']['pct'], 40)
        self.assertIn('positive DAILY', r['interpretation'])
        self.assertIn('NOT a confirmed medium-term reversal', r['trend_context'])
        r.update(source='Fixture', fetched='testing offline')
        html_text = format_breadth(r)
        self.assertIn('weak underlying trend', html_text)
        self.assertIn('NOT a confirmed', html_text)

    def test_no_false_trend_warning_when_daily_and_ma_agree(self):
        benchmark = frame(up=True)
        series = {}
        for i in range(4):
            df = frame(up=True).copy()
            if i == 3:
                df.iloc[-1, df.columns.get_loc('Close')] = df['Close'].iloc[-2] * .995
            series[f'STOCK{i}'] = df
        r = calculate_breadth(series, list(series), benchmark, now=NOW)
        self.assertEqual((r['advances'], r['declines']), (3, 1))
        self.assertGreater(r['ma20']['pct'], 40)
        self.assertGreater(r['ma50']['pct'], 40)
        self.assertIsNone(r['trend_context'])

    def test_publication_blocks_stale_data_even_when_coverage_is_high(self):
        import market_breadth as breadth
        import market_data
        past = frame()
        breadth._cache = None
        with patch.object(breadth, 'load_universe', return_value=(['UP'] + [f'EQ{i}' for i in range(499)], {}, 'fixture', False)), \
             patch.object(market_data, 'bulk_history', return_value={'UP': past}), \
             patch.object(market_data, 'index_history', return_value=past), \
             patch.object(breadth, 'expected_completed_session', return_value=date(2026, 10, 26)):
            # Live wall-clock in get_breadth need not match test: force its
            # calculate_breadth result to a verified stale sample.
            stale_result = calculate_breadth({'UP': past}, ['UP'], past,
                                              now=datetime(2026, 10, 26, 20, tzinfo=IST))
            with patch.object(breadth, 'calculate_breadth', return_value=stale_result):
                with self.assertRaisesRegex(ValueError, 'no stale breadth report'):
                    breadth._collect_breadth()
        breadth._cache = None

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


    def test_full_breadth_never_uses_unverified_sample(self):
        import market_breadth as b
        import market_data as md
        b._cache = None
        with patch.object(b, 'load_universe',
                          return_value=(['DUMMYHEG'], {}, 'Built-in sample', True)), \
             patch.object(md, 'bulk_history') as download:
            with self.assertRaisesRegex(ValueError, 'Official Nifty 500 CSV unavailable'):
                b._collect_breadth()
            download.assert_not_called()
        b._cache = None

    def test_breadth_bounded_fetch_and_single_retry(self):
        import market_breadth as b
        import market_data as md
        b._cache = None
        syms = ['SYM'+str(i) for i in range(500)]
        partial = {sym: frame() for sym in syms[:300]}
        additional = {sym: frame() for sym in syms[300:450]}
        valid = {'session': '2026-10-23', 'expected_session': '2026-10-23',
                 'stale': False, 'coverage': 90, 'valid': 450,
                 'interpretation': 'Verified'}
        with patch.object(b,'load_universe',return_value=(syms, {}, 'Official CSV', False)), \
             patch.object(md,'bulk_history',side_effect=[partial, additional]) as bulk, \
             patch.object(md,'index_history',return_value=frame()) as index, \
             patch.object(b,'calculate_breadth',return_value=valid):
            report=b._collect_breadth()
        self.assertEqual(report['coverage'],90)
        self.assertEqual(bulk.call_count,2)
        self.assertEqual(bulk.call_args_list[0].kwargs['max_fallbacks'],0)
        self.assertEqual(bulk.call_args_list[1].kwargs['retries'],0)
        self.assertEqual(len(bulk.call_args_list[1].args[0]),200)
        self.assertEqual(index.call_count,1)
        b._cache = None


    def test_nonblocking_request_and_verified_daily_cache(self):
        import market_breadth as b
        release = threading.Event()
        collected = threading.Event()
        good = {'session': '2026-10-23', 'stale': False,
                'coverage': 90, 'valid': 450, 'requested': 500}

        def slow_scan():
            collected.set()
            if not release.wait(3):
                raise AssertionError("Test worker did not finish")
            return good

        with patch.object(b, "expected_completed_session", return_value=date(2026, 10, 23)), \
             patch.object(b, "_collect_breadth", side_effect=slow_scan) as scan:
            b._cache, b._refresh_thread, b._last_error, b._next_retry_at = None, None, None, 0.0
            try:
                started = time.monotonic()
                pending = b.get_breadth()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(pending['state'], 'warming')
                self.assertTrue(collected.wait(1))
                self.assertEqual(b.get_breadth()['state'], 'warming')
                scan.assert_called_once()
                release.set()
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and b._refresh_thread is not None:
                    time.sleep(.01)
                self.assertIsNone(b._refresh_thread)
                self.assertIs(b.get_breadth(), good)
                scan.assert_called_once()
            finally:
                release.set()
                b._cache, b._refresh_thread, b._last_error, b._next_retry_at = None, None, None, 0.0

    def test_failed_scan_never_returns_false_market_numbers(self):
        import market_breadth as b
        with patch.object(b, "expected_completed_session", return_value=date(2026, 10, 23)), \
             patch.object(b, "_collect_breadth", side_effect=ValueError("bad provider")):
            b._cache, b._refresh_thread, b._last_error, b._next_retry_at = None, None, None, 0.0
            self.assertEqual(b.get_breadth()['state'], 'warming')
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and b._refresh_thread is not None:
                time.sleep(.01)
            self.assertIsNone(b._refresh_thread)
            state = b.get_breadth()
            self.assertEqual(state['state'], 'unavailable')
            self.assertNotIn('advances', state)
            self.assertTrue(format_breadth(state).startswith('⚠️'))
            b._cache, b._refresh_thread, b._last_error, b._next_retry_at = None, None, None, 0.0

    def test_previous_session_cache_not_released(self):
        import market_breadth as b
        release = threading.Event()
        old = {'session': '2026-10-22', 'stale': False, 'coverage': 100}
        with patch.object(b, "expected_completed_session", return_value=date(2026, 10, 23)), \
             patch.object(b, "_collect_breadth", side_effect=lambda: (release.wait(2), {'session': '2026-10-23', 'stale': False, 'coverage': 90, 'valid': 450})[1]):
            b._cache, b._refresh_thread, b._last_error, b._next_retry_at = old, None, None, 0.0
            try:
                result = b.get_breadth()
                self.assertEqual(result['state'], 'warming')
                self.assertTrue(format_breadth(result).startswith('⏳'))
            finally:
                release.set()
                if b._refresh_thread:
                    b._refresh_thread.join(timeout=2)
                b._cache, b._refresh_thread, b._last_error, b._next_retry_at = None, None, None, 0.0


if __name__ == '__main__':
    unittest.main()
