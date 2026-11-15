"""MSK intraday 15m / 150–200 completed-candle real-engine regression.

Every price bar below is explicitly SYNTHETIC for testing. Do not interpret
a successful test as a real market trade or an observed trading win rate.
"""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd

from msk_intraday_trade import (
    IST, _completed, _fetch_yahoo_15m, _indicators,
    build_intraday_trade_report, evaluate_instrument,
    format_intraday, market_session_open,
)

TRADING = datetime(2026, 11, 16, 10, 22, tzinfo=IST)
SUNDAY = datetime(2026, 11, 15, 17, 30, tzinfo=IST)


def candles(*, symbol="spot", current=True, volume=True,
            side="BUY", previous_sessions=9):
    """Realistic NSE 15m timestamps: 25 bars/session, 9 previous trading days."""
    days = pd.bdate_range(end="2026-11-13", periods=previous_sessions)
    historical = [
        at for day in days
        for at in pd.date_range(f"{day:%Y-%m-%d} 09:15",
                                periods=25, freq="15min", tz="Asia/Kolkata")
    ]
    current_bars = (
        list(pd.date_range("2026-11-16 09:15", periods=5,
                           freq="15min", tz="Asia/Kolkata"))
        if current else [])
    indexes = pd.DatetimeIndex(historical + current_bars)
    base, increment = ((25000., 8.) if symbol == "spot" else (250., .10))
    if side == "SELL":
        increment = -increment
    closes = [base + i * increment for i in range(len(indexes))]
    high_width = 8. if symbol == "spot" else .15
    low_width = 8. if symbol == "spot" else .15
    frame = pd.DataFrame({
        "Open": [c + (1 if side == "SELL" else -1) *
                 (0.5 if symbol == "spot" else .05) for c in closes],
        "High": [c + high_width for c in closes],
        "Low": [c - low_width for c in closes],
        "Close": closes,
        "Volume": [1000.] * len(indexes),
    }, index=indexes)
    if current and symbol != "spot":
        current_complete = frame.index[-2]  # 10:00, 10:15 is still forming.
        frame.at[current_complete, "Volume"] = 2000. if volume else 0.
    return frame


def bullish_indicators(data):
    last = float(data["Close"].iloc[-1])
    return dict(ema20=last-5, ema50=last-10,
                rsi=61.5, atr=16., adx=27.,
                plus_di=28., minus_di=12.)


def bearish_indicators(data):
    last = float(data["Close"].iloc[-1])
    return dict(ema20=last+5, ema50=last+10,
                rsi=38., atr=16., adx=27.,
                plus_di=12., minus_di=28.)


class Intraday15mRules(TestCase):
    def test_closed_sunday_never_fetches_or_issues_trade(self):
        provider = Mock(side_effect=AssertionError("no Sunday price fetch"))
        report = build_intraday_trade_report(now=SUNDAY, fetcher=provider)
        self.assertIn("MARKET CLOSED", report)
        self.assertIn("NO LIVE SIGNALS", report)
        self.assertIn("15-minute completed candles", report)
        self.assertIn("150–200 bars", report)
        self.assertNotIn("Reference entry:", report)
        provider.assert_not_called()

    def test_signal_window_starts_only_after_15m_orb_and_next_bar(self):
        for hr, minute in ((9, 30), (9, 45), (15, 17), (7, 30)):
            with self.subTest(hour=hr, minute=minute):
                self.assertFalse(market_session_open(
                    TRADING.replace(hour=hr, minute=minute)))
        self.assertTrue(market_session_open(
            TRADING.replace(hour=9, minute=46)))
        self.assertTrue(market_session_open(TRADING))
        self.assertFalse(market_session_open(SUNDAY))

    def test_requires_minimum_150_real_completed_candles(self):
        recent = candles()
        low_history = recent.tail(149)
        result = evaluate_instrument("NIFTY", low_history,
                                     candles(symbol="etf"), TRADING)
        self.assertEqual(result["status"], "NO TRADE")
        self.assertIn("150–200 verified", result["reason"])
        self.assertIn("received spot", result["reason"])

    def test_history_above_200_is_cut_to_latest_200(self):
        data = candles(previous_sessions=14)
        self.assertGreater(len(_completed(data, TRADING)), 200)
        with patch("msk_intraday_trade._indicators",
                   side_effect=bullish_indicators) as indicators:
            result = evaluate_instrument("NIFTY", data,
                                         candles(symbol="etf", previous_sessions=14),
                                         TRADING)
        self.assertEqual(result["status"], "WATCH")
        self.assertEqual(result["bars_used"], 200)
        self.assertEqual(len(indicators.call_args.args[0]), 200)

    def test_previous_session_data_cannot_be_sold_as_live(self):
        result = evaluate_instrument("NIFTY", candles(current=False),
                                     candles(symbol="etf", current=False),
                                     TRADING)
        self.assertEqual(result["status"], "NO TRADE")
        self.assertIn("Current NSE session", result["reason"])

    def test_source_delay_and_candle_mismatch_block_trade(self):
        late_time = TRADING.replace(hour=11, minute=0)
        delayed = evaluate_instrument("NIFTY", candles(),
                                      candles(symbol="etf"), late_time)
        self.assertEqual(delayed["status"], "NO TRADE")
        self.assertIn("delayed", delayed["reason"])
        etf = candles(symbol="etf")
        etf = etf.drop(etf.index[-2])  # Drop latest completed ETF bar.
        unaligned = evaluate_instrument("NIFTY", candles(), etf, TRADING)
        self.assertEqual(unaligned["status"], "NO TRADE")
        self.assertIn("not aligned", unaligned["reason"])

    def test_unknown_timezone_is_not_assumed_indian_session(self):
        frame = candles()
        frame.index = frame.index.tz_localize(None)
        self.assertTrue(_completed(frame, TRADING).empty)

    def test_no_real_etf_volume_blocks_vwap_confirmation(self):
        etf = candles(symbol="etf", volume=False)
        with patch("msk_intraday_trade._indicators",
                   side_effect=bullish_indicators):
            result = evaluate_instrument("NIFTY", candles(), etf, TRADING)
        self.assertEqual(result["status"], "NO TRADE")
        self.assertIn("ETF trading volume", result["reason"])

    def test_bullish_trade_is_watch_with_real_risk_geometry(self):
        with patch("msk_intraday_trade._indicators",
                   side_effect=bullish_indicators):
            result = evaluate_instrument("NIFTY", candles(),
                                         candles(symbol="etf"), TRADING)
        self.assertEqual(result["status"], "WATCH")
        self.assertEqual(result["direction"], "BUY")
        self.assertGreaterEqual(result["score"], 8)
        self.assertEqual(result["bars_used"], 200)
        self.assertLess(result["sl"], result["entry"])
        self.assertLess(result["entry"], result["t1"])
        self.assertLess(result["t1"], result["t2"])
        self.assertAlmostEqual((result["t1"]-result["entry"]) /
                               (result["entry"]-result["sl"]), 2)
        self.assertAlmostEqual((result["t2"]-result["entry"]) /
                               (result["entry"]-result["sl"]), 3)
        self.assertLessEqual(result["risk_pct"], .9)
        self.assertIn("15m spot", result["source"])

    def test_bearish_trade_reverse_entry_sl_and_targets(self):
        with patch("msk_intraday_trade._indicators",
                   side_effect=bearish_indicators):
            result = evaluate_instrument("BANK NIFTY",
                                         candles(side="SELL"),
                                         candles(symbol="etf", side="SELL"), TRADING)
        self.assertEqual(result["status"], "WATCH")
        self.assertEqual(result["direction"], "SELL")
        self.assertGreater(result["sl"], result["entry"])
        self.assertGreater(result["entry"], result["t1"])
        self.assertGreater(result["t1"], result["t2"])

    def test_low_adx_or_missing_etf_confirmation_is_no_trade(self):
        with patch("msk_intraday_trade._indicators",
                   side_effect=lambda x: {**bullish_indicators(x),
                                          "adx": 10.}):
            result = evaluate_instrument("NIFTY", candles(),
                                         candles(symbol="etf"), TRADING)
        self.assertEqual(result["status"], "NO TRADE")
        self.assertIn("not all confirmed", result["reason"])

    def test_future_or_unfinished_15m_candle_removed(self):
        frame = candles()
        real = _completed(frame, TRADING)
        self.assertEqual(real.index[-1].hour, 10)
        self.assertEqual(real.index[-1].minute, 0)
        future = frame.iloc[[-1]].copy()
        future.index = future.index + timedelta(minutes=15)
        future["Close"] = 999999
        future["High"] = 1000000
        final = _completed(pd.concat([frame, future]), TRADING)
        self.assertEqual(final.index[-1], real.index[-1])
        self.assertLess(final["Close"].iloc[-1], 900000)

    def test_real_15m_indicators_need_at_least_150(self):
        long_series = _completed(candles(), TRADING).tail(200)
        self.assertIsNotNone(_indicators(long_series))
        self.assertIsNone(_indicators(long_series.tail(149)))

    def test_api_failure_keeps_both_indices_as_no_trade(self):
        def unavailable(_symbol, _now):
            raise TimeoutError("offline fixture")
        report = build_intraday_trade_report(now=TRADING, fetcher=unavailable)
        self.assertIn("NIFTY 50", report)
        self.assertIn("BANK NIFTY", report)
        self.assertEqual(report.count("⛔ <b>NO TRADE</b>"), 2)
        self.assertNotIn("Reference entry:", report)

    def test_yahoo_chart_requests_exact_15m_and_30day_history(self):
        stamp = int(datetime(2026, 11, 16, 9, 15, tzinfo=IST).timestamp())
        price = {"open": [25000], "high": [25015], "low": [24985],
                 "close": [25005], "volume": [0]}
        blob = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "Asia/Kolkata"},
            "timestamp": [stamp],
            "indicators": {"quote": [price]},
        }]}}
        response = Mock(status_code=200, content=b"{}")
        response.json.return_value = blob
        response.raise_for_status.return_value = None
        with patch("msk_intraday_trade.requests.get",
                   return_value=response) as get:
            frame = _fetch_yahoo_15m("^NSEI", TRADING)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.index[0].hour, 9)
        self.assertEqual(frame["Close"].iloc[0], 25005)
        self.assertEqual(get.call_args.kwargs["timeout"], 6)
        self.assertEqual(get.call_args.kwargs["params"]["interval"], "15m")
        self.assertEqual(get.call_args.kwargs["params"]["range"], "30d")
        blob["chart"]["result"][0]["meta"]["exchangeTimezoneName"] = "UTC"
        with patch("msk_intraday_trade.requests.get",
                   return_value=response):
            with self.assertRaisesRegex(ValueError, "exchange_time"):
                _fetch_yahoo_15m("^NSEI", TRADING)

    def test_quality_score_never_claims_measured_accuracy(self):
        test_card = {
            "name": "NIFTY 50", "status": "WATCH", "direction": "BUY",
            "entry": 25000., "sl": 24950., "t1": 25100., "t2": 25150.,
            "risk_pct": .2, "score": 9, "adx": 25.1, "rsi": 60.,
            "volume_multiple": 1.55, "bars_used": 200,
            "asof": "16 Nov 10:00 IST",
            "source": "15m test fixture",
            "reason": "Synthetic test only.",
        }
        report = format_intraday(TRADING, [test_card])
        self.assertIn("15-minute completed candles", report)
        self.assertIn("150–200 bars", report)
        self.assertIn("Lookback: 200 verified 15m candles", report)
        self.assertIn("9/10 (NOT win-rate accuracy)", report)
        self.assertIn("NO AUTOMATIC BUY / SELL ORDERS", report)
        self.assertNotIn("70% accurate", report)
        self.assertNotIn("⏱️ 5-minute completed candles", report)


if __name__ == "__main__":
    import unittest
    unittest.main()
