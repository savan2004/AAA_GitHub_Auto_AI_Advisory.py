"""Intraday Nifty/Bank Nifty: real-candle gates and no-fake-signals regression.

All market price fixtures are synthetic. Passing these tests is not a live
accuracy score or certification of the connected market feed.
"""
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd

from msk_intraday_trade import (
    IST, _completed, _fetch_yahoo_5m, build_intraday_trade_report,
    evaluate_instrument, format_intraday, market_session_open,
)

TRADING = datetime(2026, 11, 16, 10, 22, tzinfo=IST)
SUNDAY = datetime(2026, 11, 15, 16, 30, tzinfo=IST)


def candles(*, symbol="spot", current=True, volume=True):
    fri = pd.date_range("2026-11-13 09:15", periods=75,
                        freq="5min", tz="Asia/Kolkata")
    mon = pd.date_range("2026-11-16 09:15", periods=12,
                        freq="5min", tz="Asia/Kolkata") if current else []
    indexes = list(fri) + list(mon)
    base, step = (25000., 3.) if symbol == "spot" else (250., 0.1)
    closes = [base + i * step for i in range(len(indexes))]
    if symbol == "spot":
        hi, low = 8., 8.
    else:
        hi, low = 0.15, 0.15
    x = pd.DataFrame({
        "Open": [c - 0.5 for c in closes],
        "High": [c + hi for c in closes],
        "Low": [c - low for c in closes],
        "Close": closes,
        "Volume": [1000.] * len(indexes),
    }, index=pd.DatetimeIndex(indexes))
    if len(x) and current and symbol != "spot":
        x.iloc[-1, x.columns.get_loc("Volume")] = 2000. if volume else 0.
    return x


def strong_bullish(series):
    close = float(series["Close"].iloc[-1])
    return {
        "ema20": close - 5, "ema50": close - 10,
        "rsi": 61.5, "atr": 16.0, "adx": 27.0,
        "plus_di": 28.0, "minus_di": 12.0,
    }


class TradingRules(TestCase):
    def test_weekend_never_fetches_and_never_invents_trade(self):
        provider = Mock(side_effect=AssertionError("No Sunday API calls allowed"))
        report = build_intraday_trade_report(now=SUNDAY, fetcher=provider)
        self.assertIn("MARKET CLOSED", report)
        self.assertIn("NO LIVE SIGNALS", report)
        self.assertNotIn("Reference entry:", report)
        provider.assert_not_called()

    def test_before_0940_after_1515_and_weekends_block(self):
        for dayhour in ((9, 30), (15, 17), (7, 30)):
            with self.subTest(time=dayhour):
                tm = TRADING.replace(hour=dayhour[0], minute=dayhour[1])
                self.assertFalse(market_session_open(tm))
        self.assertTrue(market_session_open(TRADING))
        self.assertFalse(market_session_open(SUNDAY))

    def test_missing_current_session_is_not_resold(self):
        x = evaluate_instrument("NIFTY", candles(current=False),
                                candles(symbol="etf", current=False), TRADING)
        self.assertEqual(x["status"], "NO TRADE")
        self.assertIn("Current NSE session", x["reason"])

    def test_stale_delayed_data_blocks_signal(self):
        tm = TRADING.replace(hour=11, minute=0)
        x = evaluate_instrument("NIFTY", candles(),
                                candles(symbol="etf"), tm)
        self.assertEqual(x["status"], "NO TRADE")
        self.assertIn("delayed", x["reason"])

    def test_unknown_time_zone_never_becomes_fresh_quote(self):
        data = candles()
        data.index = data.index.tz_localize(None)
        self.assertTrue(_completed(data, TRADING).empty)

    def test_no_real_etf_volume_blocks_vwap(self):
        e = candles(symbol="etf", volume=False)
        with patch("msk_intraday_trade._indicators", side_effect=strong_bullish):
            x = evaluate_instrument("NIFTY", candles(), e, TRADING)
        self.assertEqual(x["status"], "NO TRADE")
        self.assertIn("ETF trading volume", x["reason"])

    def test_intraday_watch_only_with_all_completed_bullish_signals(self):
        with patch("msk_intraday_trade._indicators", side_effect=strong_bullish):
            x = evaluate_instrument("NIFTY", candles(),
                                    candles(symbol="etf"), TRADING)
        self.assertEqual(x["status"], "WATCH")
        self.assertEqual(x["direction"], "BUY")
        self.assertGreaterEqual(x["score"], 8)
        self.assertLess(x["sl"], x["entry"])
        self.assertLess(x["entry"], x["t1"])
        self.assertLess(x["t1"], x["t2"])
        self.assertAlmostEqual(
            (x["t1"] - x["entry"]) / (x["entry"] - x["sl"]), 2)
        self.assertAlmostEqual(
            (x["t2"] - x["entry"]) / (x["entry"] - x["sl"]), 3)
        self.assertLessEqual(x["risk_pct"], 0.9)
        self.assertIn("completed bars", x["source"])

    def test_adx_or_vwap_missing_returns_no_trade_not_zero(self):
        with patch("msk_intraday_trade._indicators", side_effect=lambda s: {
            **strong_bullish(s), "adx": 10.0,
        }):
            x = evaluate_instrument("NIFTY", candles(),
                                    candles(symbol="etf"), TRADING)
        self.assertEqual(x["status"], "NO TRADE")
        self.assertIn("not all confirmed", x["reason"])

    def test_forming_future_candle_is_excluded(self):
        x = candles()
        upcoming = x.iloc[[-1]].copy()
        upcoming.index = upcoming.index + timedelta(minutes=10)
        upcoming["Close"] = 999999
        merged = pd.concat([x, upcoming]).sort_index()
        valid = _completed(merged, TRADING)
        self.assertLess(valid["Close"].iloc[-1], 900000)
        self.assertEqual(valid.index[-1], x.index[-1])

    def test_both_instruments_displayed_even_when_feed_fails(self):
        def fail_provider(symbol, now):
            raise TimeoutError("test only")
        report = build_intraday_trade_report(now=TRADING, fetcher=fail_provider)
        self.assertIn("NIFTY 50", report)
        self.assertIn("BANK NIFTY", report)
        self.assertEqual(report.count("⛔ <b>NO TRADE</b>"), 2)
        self.assertNotIn("Reference entry:", report)

    def test_yahoo_chart_parser_validates_exchange_timezone(self):
        stamp = int(datetime(2026, 11, 16, 9, 15, tzinfo=IST).timestamp())
        quote = {"open": [25000], "high": [25015], "low": [24985],
                 "close": [25005], "volume": [0]}
        payload = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "Asia/Kolkata"},
            "timestamp": [stamp],
            "indicators": {"quote": [quote]},
        }]}}
        resp = Mock(status_code=200, content=b"{}")
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with patch("msk_intraday_trade.requests.get", return_value=resp) as get:
            frame = _fetch_yahoo_5m("^NSEI", TRADING)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.index[0].hour, 9)
        self.assertEqual(frame["Close"].iloc[0], 25005)
        self.assertEqual(get.call_args.kwargs["timeout"], 6)
        payload["chart"]["result"][0]["meta"]["exchangeTimezoneName"] = "America/New_York"
        with patch("msk_intraday_trade.requests.get", return_value=resp):
            with self.assertRaisesRegex(ValueError, "exchange_time"):
                _fetch_yahoo_5m("^NSEI", TRADING)

    def test_format_does_not_confuse_score_with_accuracy(self):
        decision = {"name": "NIFTY 50", "status": "WATCH",
                    "direction": "BUY", "entry": 25000., "sl": 24950.,
                    "t1": 25100., "t2": 25150., "risk_pct": 0.2,
                    "score": 9, "adx": 25.1, "rsi": 60.0,
                    "volume_multiple": 1.55,
                    "asof": "16 Nov 10:10 IST",
                    "source": "Verified example",
                    "reason": "Technicals aligned."}
        report = format_intraday(TRADING, [decision])
        self.assertIn("9/10 (NOT win-rate accuracy)", report)
        self.assertIn("NO AUTOMATIC BUY / SELL ORDERS", report)
        self.assertNotIn("70% accurate", report)
        self.assertNotIn("simulated live", report)


if __name__ == "__main__":
    import unittest
    unittest.main()
