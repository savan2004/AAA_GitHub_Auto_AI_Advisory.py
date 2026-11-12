"""Regression: synthetic only, no market API or production portfolio access."""
import ast
import json
import logging
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_legacy_manager():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioManager")
    ctx = {"os": os, "json": json, "threading": threading, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ctx)
    return ctx["PortfolioManager"]


class PortfolioSafetyTests(unittest.TestCase):
    def test_missing_holdings_fail_closed_and_never_overwrite(self):
        cls = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portfolio_data.json"
            store = cls(str(path))
            self.assertTrue(store._recovery_required)
            with self.assertRaisesRegex(RuntimeError, "restore required"):
                store.get(100)
            with self.assertRaisesRegex(RuntimeError, "restore required"):
                store.add(100, "SBIN", 1, 100)
            self.assertFalse(path.exists())
            initialized = cls(str(path), allow_empty_init=True)
            self.assertEqual(initialized.get(100), {})
            self.assertEqual(json.loads(path.read_text()), {})

    def test_atomic_save_rolls_back_on_replace_failure(self):
        cls = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portfolio_data.json"
            store = cls(str(path), allow_empty_init=True)
            store.add(100, "SBIN", 2, 500)
            before = store.get(100)
            saved = path.read_bytes()
            with patch("os.replace", side_effect=OSError("test disk outage")):
                with self.assertRaisesRegex(RuntimeError, "could not be saved"):
                    store.add(100, "SBIN", 1, 600)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(store.get(100), before)
            self.assertEqual(cls(str(path)).get(100), before)

    def test_corrupt_json_aborts_instead_of_resetting_to_empty(self):
        cls = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portfolio_data.json"
            path.write_text('{"broken":', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "manual recovery required"):
                cls(str(path))
            self.assertEqual(path.read_text(), '{"broken":')

    def test_deep_copy_and_invalid_numeric_input(self):
        cls = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            p = cls(str(Path(folder) / "holdings.json"), allow_empty_init=True)
            p.add(100, "SBIN", 2, 500)
            ref = p.get(100)
            ref["SBIN"]["qty"] = 999
            self.assertEqual(p.get(100)["SBIN"]["qty"], 2)
            for bad in (float("nan"), float("inf"), float("-inf"), 0, -10, True):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    p.add(100, "SBIN", 1, bad)

    def test_remove_is_atomic(self):
        cls = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            p = cls(str(Path(folder) / "holdings.json"), allow_empty_init=True)
            p.add(100, "SBIN", 2, 500)
            with patch("os.replace", side_effect=OSError("test failure")):
                with self.assertRaises(RuntimeError):
                    p.remove(100, "SBIN")
            self.assertEqual(p.get(100)["SBIN"]["qty"], 2)

    def test_unpriced_holding_shows_partial_not_fabricated_zero(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(src).body
                    if isinstance(n, ast.FunctionDef) and n.name == "build_portfolio_card")
        ctx = {
            "date": date,
            "logger": logging.getLogger(__name__),
            "portfolio": type("Store", (), {"get": lambda self, uid: {
                "SBIN": {"qty": 2, "avg": 500},
                "TCS": {"qty": 3, "avg": 1200},
            }})(),
            "get_verified_quote": lambda sym: ({
                "price": 550, "quote_source": "NSE India",
                "quote_asof_ist": "2026-11-12T10:00:00+05:30",
                "quote_status": "recent_observation",
            } if sym == "SBIN" else None),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ctx)
        card = ctx["build_portfolio_card"](100)
        self.assertIn("Partial P&L: ₹+100.00", card)
        self.assertIn("P&L: Unavailable", card)
        self.assertNotIn("Total P&L:", card)
        self.assertIn("priced holdings: ₹1,000.00", card)
        self.assertIn("NSE India | 2026-11-12 10:00 IST", card)
        self.assertIn("recent observation", card)
        self.assertNotIn("LTP:₹", card)
        ctx["get_verified_quote"] = lambda sym: None
        self.assertIn("P&L unavailable", ctx["build_portfolio_card"](100))

    def test_portfolio_labels_last_session_and_rejects_undated_price(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
                    and n.name == "build_portfolio_card")
        quote = {"price": 700, "quote_source": "Yahoo Finance v8",
                 "quote_asof_ist": "2026-11-13T15:30:00+05:30",
                 "quote_status": "last_observed_session"}
        ns = {"date": date, "logger": logging.getLogger(__name__),
              "portfolio": type("Store", (), {"get": lambda self, uid: {
                  "SBIN": {"qty": 2, "avg": 650}}})(),
              "get_verified_quote": lambda sym: quote}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ns)
        card = ns["build_portfolio_card"](100)
        self.assertIn("last observed session", card)
        self.assertIn("Yahoo Finance v8 | 2026-11-13 15:30 IST", card)
        self.assertIn("P&L is indicative from dated quotes", card)
        ns["get_verified_quote"] = lambda sym: {"price": 700}
        unsafe = ns["build_portfolio_card"](100)
        self.assertIn("P&L unavailable", unsafe)
        self.assertNotIn("₹+100.00", unsafe)

    def test_opt_in_postgres_never_connects_without_cutover(self):
        from postgres_portfolio import build_portfolio_backend, PortfolioStorageError
        calls = []
        def factory():
            calls.append("legacy")
            return "legacy"
        self.assertEqual(build_portfolio_backend(factory, environ={}), "legacy")
        self.assertEqual(calls, ["legacy"])
        with self.assertRaises(PortfolioStorageError):
            build_portfolio_backend(factory, environ={"PORTFOLIO_BACKEND": "postgres"})
        self.assertEqual(calls, ["legacy"])


class FundamentalUnitSafetyTests(unittest.TestCase):
    """Financial-ratio units must agree in the provider and Telegram layers."""

    def test_yahoo_percent_scaled_debt_equity_becomes_unitless_ratio(self):
        from data_engine import normalized_debt_equity_ratio as norm
        self.assertEqual(norm({"debtToEquity": 125}), 1.25)
        self.assertEqual(norm({"debtToEquity": 0}), 0)
        self.assertEqual(norm({"debt_equity_ratio": 1.25,
                               "debtToEquity": 125}), 1.25)
        self.assertEqual(norm({"debt_equity_ratio": 0,
                               "debtToEquity": 125}), 0)
        for val in (float("nan"), float("inf"), -1, True, "not numeric"):
            with self.subTest(value=val):
                self.assertIsNone(norm({"debtToEquity": val}))
        self.assertIsNone(norm({}))

    def test_aggregator_normalizes_primary_yahoo_debt_equity(self):
        import fundamentals as fund
        raw_info = {"pe": 18, "roe": 0.12, "eps": 30,
                    "market_cap": 900_000_000_000,
                    "totalRevenue": 300_000_000_000,
                    "debtToEquity": 125, "debt_equity_ratio": 1.25}
        with patch.object(fund, "_get_cached", return_value=None), \
             patch.object(fund, "_set_cached"), \
             patch("data_engine.get_info", return_value=raw_info), \
             patch.object(fund, "_fetch_screener", return_value=None), \
             patch.object(fund, "_fetch_finnhub", return_value=None), \
             patch.object(fund, "_fetch_yfinance", return_value={}):
            result = fund.get_fundamentals("SBIN")
        self.assertEqual(result["de"], 1.25)
        self.assertEqual(result["pe"], 18)

    def test_aggregator_normalizes_yfinance_fallback_but_not_screener_ratio(self):
        import fundamentals as fund
        with patch.object(fund, "_get_cached", return_value=None), \
             patch.object(fund, "_set_cached"), \
             patch("data_engine.get_info", return_value={}), \
             patch.object(fund, "_fetch_screener", return_value=None), \
             patch.object(fund, "_fetch_finnhub", return_value=None), \
             patch.object(fund, "_fetch_yfinance",
                          return_value={"debtToEquity": 240}):
            self.assertEqual(fund.get_fundamentals("SBIN")["de"], 2.4)
        with patch.object(fund, "_get_cached", return_value=None), \
             patch.object(fund, "_set_cached"), \
             patch("data_engine.get_info", return_value={}), \
             patch.object(fund, "_fetch_screener", return_value={"de": 1.7}), \
             patch.object(fund, "_fetch_finnhub", return_value=None), \
             patch.object(fund, "_fetch_yfinance", return_value={}):
            self.assertEqual(fund.get_fundamentals("SBIN")["de"], 1.7)

    def test_fundamental_button_shows_ratio_not_percent_and_preserves_zero(self):
        import data_engine as engine
        import fundamentals as fund
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(src).body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "build_fundamental_text")
        ns = {
            "get_info": lambda sym: {"debtToEquity": 125},
            "normalized_debt_equity_ratio": engine.normalized_debt_equity_ratio,
            "safe_val": lambda d, k: d.get(k),
            "frow": lambda label, val, suffix="": f"{label}: {val}{suffix}",
            "fmt_mcap": lambda val: str(val),
            "_fmt_revenue": lambda val, mcap: str(val),
            "financial_source_summary": lambda fund: "NOT NSE filing-audited",
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "main.py", "exec"), ns)
        with patch.object(fund, "get_fundamentals", return_value={}):
            output = ns["build_fundamental_text"]("SBIN")
        self.assertIn("Debt/Equity: 1.25x", output)
        self.assertNotIn("Debt/Equity: 125", output)
        with patch.object(fund, "get_fundamentals", return_value={"de": 0}):
            output = ns["build_fundamental_text"]("SBIN")
        self.assertIn("Debt/Equity: 0x", output)
        self.assertNotIn("Debt/Equity: 1.25", output)

class EquityPriceSafetyTests(unittest.TestCase):
    def test_fundamentals_cache_must_not_reuse_hour_old_price(self):
        import data_engine as de
        old = {"price": 100.0, "pe": 17.5, "name": "SBIN"}
        cached_quote = {"price": 125.0,
                        "quote_timestamp": datetime.now(timezone.utc).timestamp(),
                        "quote_source": "Yahoo Finance v8",
                        "provider_symbol": "SBIN.NS"}
        def cache(key, ttl):
            return old if key.startswith("info_") else cached_quote
        with patch.object(de, "cached_get", side_effect=cache), \
             patch.object(de, "_yahoo_v10_fundamentals") as fund, \
             patch.object(de, "_yahoo_v8_quote") as yahoo:
            card = de.get_info("SBIN")
            self.assertEqual(card["price"], 125.0)
            self.assertEqual(card["pe"], 17.5)
            self.assertEqual(old["price"], 100.0)  # Cached object is untouched.
            self.assertEqual(card["quote_source"], "Yahoo Finance v8")
            fund.assert_not_called()
            yahoo.assert_not_called()

    def test_no_current_quote_removes_stale_price_but_keeps_ratios(self):
        import data_engine as de
        cached_info = {"price": 99.0, "pe": 15.0}
        def cache(key, ttl):
            return cached_info if key.startswith("info_") else None
        with patch.object(de, "cached_get", side_effect=cache), \
             patch.object(de, "_yahoo_v8_quote", return_value=None), \
             patch.object(de, "_nse_quote", return_value=None):
            card = de.get_info("SBIN")
            self.assertNotIn("price", card)
            self.assertEqual(card["pe"], 15.0)
            self.assertEqual(cached_info["price"], 99.0)

    def test_invalid_v10_price_gets_current_nse_quote(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v10_fundamentals",
                          return_value={"price": float("nan"), "pe": 12}), \
             patch.object(de, "_yahoo_v8_quote", return_value={"price": float("nan")}), \
             patch.object(de, "_nse_quote", return_value={
                 "price": "1,010.50", "provider_symbol": "SBIN",
                 "quote_timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d-%b-%Y %H:%M:%S")}), \
             patch.object(de, "cached_set"):
            card = de.get_info("SBIN")
            self.assertEqual(card["price"], 1010.5)
            self.assertEqual(card["pe"], 12)

    def test_index_info_cache_uses_five_minute_ttl(self):
        import data_engine as de
        safe_index = {"price": 25000, "quote_source": "Yahoo Finance v8",
                      "provider_symbol": "^NSEI",
                      "quote_timestamp": datetime.now(timezone.utc).timestamp()}
        with patch.object(de, "cached_get", return_value=safe_index) as cache, \
             patch.object(de, "_yahoo_v8_quote") as quote:
            self.assertEqual(de.get_info("^NSEI")["price"], 25000)
            cache.assert_any_call("info_v3_roe_unit_^NSEI", de.TTL_PRICE)
            cache.assert_any_call("price_^NSEI", de.TTL_PRICE)
            quote.assert_not_called()

    def test_nan_or_zero_quote_uses_valid_nse_fallback(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value={"price": float("nan")}), \
             patch.object(de, "_nse_quote", return_value={
                 "price": "1,234.50", "provider_symbol": "SBIN",
                 "quote_timestamp": datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d-%b-%Y %H:%M:%S")}), \
             patch.object(de, "cached_set") as save:
            self.assertEqual(de.get_live_price("SBIN"), 1234.5)
            self.assertTrue(save.called)

    def test_intraday_quote_reports_provenance_and_exchange_timestamp(self):
        import data_engine as de
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime(2026, 11, 12, 10, 0, tzinfo=ist)
        raw = {"price": 600.25, "quote_timestamp": (now - timedelta(minutes=12)).timestamp(),
               "provider_symbol": "SBIN.NS"}
        with patch.object(de.time, "time", return_value=now.timestamp()), \
             patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value=raw), \
             patch.object(de, "_nse_quote") as nse, \
             patch.object(de, "cached_set") as cache:
            verified = de.get_verified_quote("SBIN")
            self.assertEqual(verified["price"], 600.25)
            self.assertEqual(verified["quote_source"], "Yahoo Finance v8")
            self.assertEqual(verified["quote_status"], "recent_observation")
            self.assertEqual(verified["quote_asof_ist"], "2026-11-12T09:48:00+05:30")
            self.assertEqual(cache.call_args.args[1]["quote_timestamp"],
                             raw["quote_timestamp"])
            nse.assert_not_called()

    def test_in_market_stale_quote_and_old_numeric_cache_fail_closed(self):
        import data_engine as de
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime(2026, 11, 12, 10, 0, tzinfo=ist)
        stale = {"price": 600, "provider_symbol": "SBIN.NS",
                 "quote_timestamp": (now - timedelta(hours=2)).timestamp()}
        with patch.object(de.time, "time", return_value=now.timestamp()), \
             patch.object(de, "cached_get", return_value=600), \
             patch.object(de, "_yahoo_v8_quote", return_value=stale), \
             patch.object(de, "_nse_quote", return_value=None), \
             patch.object(de, "cached_set") as cache:
            self.assertIsNone(de.get_live_price("SBIN"))
            cache.assert_not_called()

    def test_known_nse_ist_timestamp_fallback_with_provenance(self):
        import data_engine as de
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime(2026, 11, 12, 10, 0, tzinfo=ist)
        with patch.object(de.time, "time", return_value=now.timestamp()), \
             patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value={"price": 1234}), \
             patch.object(de, "_nse_quote", return_value={
                 "price": "1,250.50", "provider_symbol": "SBIN",
                 "quote_timestamp": "12-Nov-2026 09:55:00"}), \
             patch.object(de, "cached_set"):
            quote = de.get_verified_quote("SBIN")
            self.assertEqual(quote["price"], 1250.5)
            self.assertEqual(quote["quote_source"], "NSE India")
            self.assertEqual(quote["quote_asof_ist"], "2026-11-12T09:55:00+05:30")

    def test_missing_future_wrong_ticker_invalid_time_and_boolean_fail(self):
        import data_engine as de
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime(2026, 11, 12, 10, 0, tzinfo=ist).timestamp()
        invalid = (
            {"price": 500, "provider_symbol": "SBIN.NS"},
            {"price": 500, "provider_symbol": "TCS.NS", "quote_timestamp": now},
            {"price": 500, "quote_timestamp": now + 600},
            {"price": True, "quote_timestamp": now},
            {"price": 500, "quote_timestamp": float("nan")},
            {"price": 500, "quote_timestamp": "not-a-timestamp"},
        )
        for data in invalid:
            with self.subTest(data=data):
                self.assertIsNone(de._verified_quote_payload(
                    data, "Yahoo Finance v8", "SBIN.NS", now_epoch=now
                ))

    def test_closed_market_quote_is_last_observed_not_realtime(self):
        import data_engine as de
        ist = timezone(timedelta(hours=5, minutes=30))
        friday = datetime(2026, 11, 13, 15, 30, tzinfo=ist)
        sunday = datetime(2026, 11, 15, 10, 0, tzinfo=ist)
        value = {"price": 700, "provider_symbol": "SBIN.NS",
                 "quote_timestamp": friday.timestamp()}
        result = de._verified_quote_payload(value, "Yahoo Finance v8",
                                            "SBIN.NS", now_epoch=sunday.timestamp())
        self.assertEqual(result["quote_status"], "last_observed_session")
        following_week = sunday + timedelta(days=8)
        self.assertIsNone(de._verified_quote_payload(
            value, "Yahoo Finance v8", "SBIN.NS",
            now_epoch=following_week.timestamp()
        ))

    def test_provider_parsers_preserve_source_update_fields(self):
        import data_engine as de
        yahoo = type("Response", (), {
            "status_code": 200, "ok": True,
            "json": lambda self: {"chart": {"result": [{
                "meta": {"regularMarketPrice": 400, "regularMarketTime": 1234567890,
                         "symbol": "SBIN.NS"}}]}}
        })()
        nse = type("Response", (), {
            "ok": True,
            "json": lambda self: {"priceInfo": {"lastPrice": 400},
                                  "metadata": {"lastUpdateTime": "12-Nov-2026 10:00:00",
                                               "symbol": "SBIN"}}
        })()
        with patch.object(de, "_wait_for_rate_slot"), \
             patch.object(de.requests, "get", return_value=yahoo):
            self.assertEqual(de._yahoo_v8_quote("SBIN.NS")["quote_timestamp"],
                             1234567890)
        session = type("Session", (), {"get": lambda self, *args, **kwargs: nse})()
        with patch.object(de, "_get_nse_session", return_value=session):
            self.assertEqual(de._nse_quote("SBIN")["quote_timestamp"],
                             "12-Nov-2026 10:00:00")

    def test_no_provider_returns_no_price_not_zero(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value={"price": -2}), \
             patch.object(de, "_nse_quote", return_value={"price": "NaN"}), \
             patch.object(de, "cached_set") as save:
            self.assertIsNone(de.get_live_price("SBIN"))
            save.assert_not_called()

    def test_index_symbol_not_given_ns_suffix(self):
        import pandas as pd
        import data_engine as de
        df = pd.DataFrame({"Open":[100], "High":[105], "Low":[95],
                           "Close":[102], "Volume":[1000]},
                          index=pd.date_range("2026-10-30", periods=1))
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_hist", return_value=df) as yahoo, \
             patch.object(de, "_nse_hist") as nse, \
             patch.object(de, "cached_set"):
            result = de.get_hist("^NSEI", period="1y")
            self.assertEqual(len(result), 1)
            self.assertEqual(yahoo.call_args[0][0], "^NSEI")
            nse.assert_not_called()

    def test_get_info_does_not_suffix_index_or_request_equity_fundamentals(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value={
                 "price": 25000, "provider_symbol": "^NSEI",
                 "quote_timestamp": datetime.now(timezone.utc).timestamp()}) as quote, \
             patch.object(de, "_yahoo_v10_fundamentals") as fundamentals, \
             patch.object(de, "_nse_quote") as nse, \
             patch.object(de, "cached_set"):
            self.assertEqual(de.get_info("^NSEI")["price"], 25000)
            quote.assert_called_once_with("^NSEI")
            fundamentals.assert_not_called()
            nse.assert_not_called()

    def test_fallback_reaches_later_chunks_without_false_progress(self):
        import pandas as pd
        import market_data as md
        df = pd.DataFrame({"Open": [10], "High": [12], "Low": [9],
                           "Close": [11], "Volume": [100]},
                          index=pd.date_range("2026-10-30", periods=1))
        progress = []
        with patch.object(md, "_download_chunk", return_value={}), \
             patch.object(md, "_recover_missing_symbol", return_value=df) as recover:
            result = md.bulk_history([f"SYM{i}" for i in range(30)],
                                     chunk_size=10, retries=0,
                                     progress_cb=lambda done, total: progress.append((done, total)))
            self.assertEqual(len(result), 30)
            self.assertEqual(recover.call_count, 30)
            self.assertEqual(progress, [(10, 30), (20, 30), (30, 30)])

    def test_bounded_bulk_recovers_missing_stock(self):
        import pandas as pd
        import market_data as md
        df = pd.DataFrame({"Open":[100], "High":[110], "Low":[90],
                           "Close":[105], "Volume":[1000]},
                          index=pd.date_range("2026-10-30", periods=1))
        with patch.object(md, "_download_chunk", return_value={"SBIN.NS": df}), \
             patch.object(md, "_recover_missing_symbol", return_value=df) as recover:
            result = md.bulk_history(["SBIN", "TCS"], chunk_size=10, retries=0)
            self.assertEqual(set(result), {"SBIN", "TCS"})
            recover.assert_called_once_with("TCS", "1y")

    def test_bounded_fallback_never_fabricates_missing_candles(self):
        import market_data as md
        with patch.object(md, "_download_chunk", return_value={}), \
             patch.object(md, "_recover_missing_symbol", return_value=None) as recover:
            result = md.bulk_history([f"S{i}" for i in range(30)], chunk_size=10, retries=0)
            self.assertEqual(result, {})
            self.assertEqual(recover.call_count, 30)


if __name__ == "__main__":
    unittest.main()
