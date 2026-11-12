"""Regression: synthetic only, no market API or production portfolio access."""
import ast
import json
import logging
import os
import tempfile
import threading
import unittest
from datetime import date
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
            "get_live_price": lambda sym: 550 if sym == "SBIN" else None,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ctx)
        card = ctx["build_portfolio_card"](100)
        self.assertIn("Partial P&L: ₹+100.00", card)
        self.assertIn("P&L: Unavailable", card)
        self.assertNotIn("Total P&L:", card)
        self.assertIn("priced holdings: ₹1,000.00", card)
        ctx["get_live_price"] = lambda sym: None
        self.assertIn("P&L unavailable", ctx["build_portfolio_card"](100))

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


class EquityPriceSafetyTests(unittest.TestCase):
    def test_fundamentals_cache_must_not_reuse_hour_old_price(self):
        import data_engine as de
        old = {"price": 100.0, "pe": 17.5, "name": "SBIN"}
        def cache(key, ttl):
            return old if key.startswith("info_") else 125.0
        with patch.object(de, "cached_get", side_effect=cache), \
             patch.object(de, "_yahoo_v10_fundamentals") as fund, \
             patch.object(de, "_yahoo_v8_quote") as quote:
            card = de.get_info("SBIN")
            self.assertEqual(card["price"], 125.0)
            self.assertEqual(card["pe"], 17.5)
            self.assertEqual(old["price"], 100.0)  # Cached object is untouched.
            fund.assert_not_called()
            quote.assert_not_called()

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
             patch.object(de, "_nse_quote", return_value={"price": "1,010.50"}), \
             patch.object(de, "cached_set"):
            card = de.get_info("SBIN")
            self.assertEqual(card["price"], 1010.5)
            self.assertEqual(card["pe"], 12)

    def test_index_info_cache_uses_five_minute_ttl(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value={"price": 25000}) as cache, \
             patch.object(de, "_yahoo_v8_quote") as quote:
            self.assertEqual(de.get_info("^NSEI")["price"], 25000)
            cache.assert_called_once_with("info_v2_^NSEI", de.TTL_PRICE)
            quote.assert_not_called()

    def test_nan_or_zero_quote_uses_valid_nse_fallback(self):
        import data_engine as de
        with patch.object(de, "cached_get", return_value=None), \
             patch.object(de, "_yahoo_v8_quote", return_value={"price": float("nan")}), \
             patch.object(de, "_nse_quote", return_value={"price": "1,234.50"}), \
             patch.object(de, "cached_set") as save:
            self.assertEqual(de.get_live_price("SBIN"), 1234.5)
            self.assertTrue(save.called)

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
             patch.object(de, "_yahoo_v8_quote", return_value={"price": 25000}) as quote, \
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
