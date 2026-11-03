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
        with patch.object(breadth, 'load_universe', return_value=(['UP'], {}, 'fixture', False)), \
             patch.object(market_data, 'bulk_history', return_value={'UP': past}), \
             patch.object(market_data, 'index_history', return_value=past), \
             patch.object(breadth, 'expected_completed_session', return_value=date(2026, 10, 26)):
            # Live wall-clock in get_breadth need not match test: force its
            # calculate_breadth result to a verified stale sample.
            stale_result = calculate_breadth({'UP': past}, ['UP'], past,
                                              now=datetime(2026, 10, 26, 20, tzinfo=IST))
            with patch.object(breadth, 'calculate_breadth', return_value=stale_result):
                with self.assertRaisesRegex(ValueError, 'no stale breadth report'):
                    breadth.get_breadth()
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



class MarketPulseTests(unittest.TestCase):
    """All examples are synthetic and require no network or Telegram."""

    def test_index_uses_only_expected_completed_nifty_session(self):
        from market_pulse import index_context
        valid = index_context(frame(), now=NOW)
        self.assertEqual(valid['session'], '2026-10-23')
        self.assertGreater(valid['close'], 0)
        self.assertIsNotNone(valid['atr'])
        self.assertEqual(valid['volatility'], 'NORMAL')
        stale = index_context(frame(), now=NOW + timedelta(days=17))
        self.assertIsNone(stale)

    def test_atr_elevated_uses_recent_baseline_not_price_change(self):
        from market_pulse import index_context
        wide = frame()
        wide.iloc[-1, wide.columns.get_loc('High')] += 40
        wide.iloc[-1, wide.columns.get_loc('Low')] -= 40
        result = index_context(wide, now=NOW)
        self.assertEqual(result['volatility'], 'ELEVATED')
        self.assertGreater(result['atr_ratio'], 1.25)

    def test_falls_back_without_fake_breadth_or_index(self):
        from market_pulse import format_market_pulse
        reply = format_market_pulse(None, None, None, 'unavailable: coverage', now=NOW)
        self.assertIn('verified completed-session quote unavailable', reply)
        self.assertIn('coverage', reply)
        self.assertIn('no timely source-verified headline', reply)
        self.assertNotIn('HIGHLY VOLATILE', reply)

    def test_news_requires_parseable_fresh_publisher_date(self):
        from market_pulse import verified_market_event
        class Response:
            content = (b'<rss><channel><item><title>'
                       b'Jio Platforms lists at 21 percent premium'
                       b'</title><pubDate>Wed, 28 Oct 2026 06:00:00 GMT</pubDate>'
                       b'</item></channel></rss>')
            def raise_for_status(self):
                return None
        fetch = lambda url, headers, timeout: Response()
        now = datetime(2026, 10, 28, 17, tzinfo=IST)
        event = verified_market_event(now=now, fetch=fetch)
        self.assertIn('Jio Platforms', event['title'])
        self.assertIn('28 Oct', event['published'])
        later = datetime(2026, 10, 31, 17, tzinfo=IST)
        self.assertIsNone(verified_market_event(now=later, fetch=fetch))

    def test_undated_news_is_never_presented_as_current(self):
        from market_pulse import verified_market_event
        class Undated:
            content = b'<rss><item><title>RBI cuts repo rate today</title></item></rss>'
            def raise_for_status(self):
                return None
        self.assertIsNone(verified_market_event(now=NOW,
                                                fetch=lambda *a, **k: Undated()))

    def test_headline_html_escaped_and_output_short(self):
        from market_pulse import format_market_pulse, index_context
        snapshot = index_context(frame(), now=NOW)
        news = {'title': 'Jio <bad> lists today', 'source': 'ET & Markets',
                'published': '23 Oct 11:30 IST'}
        message = format_market_pulse(snapshot, None, news, 'updating', now=NOW)
        self.assertIn('Jio &lt;bad&gt; lists today', message)
        self.assertIn('ET &amp; Markets', message)
        self.assertNotIn('<bad>', message)
        self.assertIn('ATR(14)', message)
        self.assertLess(len(message), 1150)

    def test_button_independent_of_500_stock_failure(self):
        import market_pulse as pulse
        import market_data
        with patch.object(market_data, 'index_history', return_value=None), \
             patch.object(pulse, '_breadth_result', return_value=(
                 None, 'unavailable (low quote coverage)')), \
             patch.object(pulse, 'verified_market_event', return_value=None):
            reply = pulse.build_market_pulse()
        self.assertIn('low quote coverage', reply)
        self.assertIn('Nifty close/ATR', reply)


if __name__ == '__main__':
    unittest.main()
