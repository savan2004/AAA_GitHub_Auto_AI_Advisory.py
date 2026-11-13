"""MSK Bot deep P0 release-safety regressions; synthetic/no live Telegram."""
import ast
import logging
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from technical_price_safety import technical_price_context, guarded_target_line

IST = timezone(timedelta(hours=5, minutes=30))
MAIN = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def sample_history(*, final="2026-11-12"):
    dates = pd.date_range(end=final, periods=31)
    values = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Open": values, "High": [v + 3 for v in values],
        "Low": [v - 3 for v in values], "Close": values,
        "Volume": [1000] * len(dates),
    }, index=dates)


def quote_at(now, *, status="recent_observation", price=137):
    return {
        "price": price, "quote_source": "Yahoo Finance v8",
        "quote_asof_ist": (now - timedelta(minutes=3)).isoformat(),
        "quote_status": status,
    }


class TechnicalAsOfTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, 10, 0, tzinfo=IST)

    def test_fresh_candle_and_timestamped_source_permits_illustrative_target(self):
        market = technical_price_context(
            sample_history(), quote_at(self.now), now=self.now
        )
        self.assertIsNotNone(market)
        self.assertEqual(market["historical_close"], 130)
        self.assertEqual(market["target_price"], 137)
        self.assertIn("2026-11-12", market["historical_line"])
        self.assertIn("not live LTP", market["historical_line"])
        self.assertIn("Yahoo Finance v8", market["verified_quote_line"])
        self.assertEqual(guarded_target_line(
            lambda trend, p, atr: f"target:{p}", "BULLISH", 2, market
        ), "target:137.0")

    def test_undated_future_stale_or_unsorted_candles_are_refused(self):
        bad = sample_history().reset_index(drop=True)
        self.assertIsNone(technical_price_context(bad, quote_at(self.now), now=self.now))
        self.assertIsNone(technical_price_context(
            sample_history(final="2026-10-29"),
            quote_at(self.now), now=self.now,
        ))
        self.assertIsNone(technical_price_context(
            sample_history(final="2026-11-13"),
            quote_at(self.now), now=self.now,
        ))
        reversed_df = sample_history().iloc[::-1]
        self.assertIsNone(technical_price_context(
            reversed_df, quote_at(self.now), now=self.now
        ))

    def test_unknown_source_naive_time_or_expired_quote_withholds_targets(self):
        for quote in (
            None,
            {"price": 137, "quote_asof_ist": self.now.isoformat(),
             "quote_status": "recent_observation", "quote_source": "Unknown"},
            {"price": 137, "quote_asof_ist": "2026-11-12T09:57:00",
             "quote_status": "recent_observation", "quote_source": "NSE India"},
            quote_at(self.now - timedelta(hours=2)),
        ):
            with self.subTest(quote=quote):
                market = technical_price_context(
                    sample_history(), quote, now=self.now
                )
                self.assertIsNone(market["target_price"])
                self.assertIn("withheld", guarded_target_line(
                    lambda *args: "UNSAFE TARGET", "BULLISH", 2, market
                ))

    def test_after_hours_cannot_forge_live_trade_targets(self):
        now = datetime(2026, 11, 12, 17, 0, tzinfo=IST)
        market = technical_price_context(
            sample_history(), quote_at(now, status="last_observed_session"),
            now=now,
        )
        self.assertIn("Last observed session", market["verified_quote_line"])
        self.assertIsNone(market["target_price"])

    def test_main_analysis_and_technical_do_not_label_old_close_as_ltp(self):
        tree = ast.parse(MAIN)
        for name in ("build_adv", "build_technical_text"):
            node = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == name)
            fragment = ast.get_source_segment(MAIN, node)
            with self.subTest(card=name):
                self.assertIn("technical_price_context(df, observed)", fragment)
                self.assertIn("guarded_target_line(", fragment)
                self.assertIn('market["historical_line"]', fragment)
                self.assertNotIn(" LTP: ₹", fragment)


class PortfolioAccessTests(unittest.TestCase):
    def test_private_chat_sender_matches_portfolio_owner(self):
        node = next(n for n in ast.parse(MAIN).body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_require_private_portfolio")
        messages = []
        ns = {"safe_send": lambda chat, text: messages.append((chat, text))}
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "main.py", "exec"), ns)
        guard = ns["_require_private_portfolio"]
        private = SimpleNamespace(
            chat=SimpleNamespace(id=1001, type="private"),
            from_user=SimpleNamespace(id=1001)
        )
        group = SimpleNamespace(
            chat=SimpleNamespace(id=-200, type="supergroup"),
            from_user=SimpleNamespace(id=1001)
        )
        spoofed = SimpleNamespace(
            chat=SimpleNamespace(id=1001, type="private"),
            from_user=SimpleNamespace(id=3003)
        )
        self.assertTrue(guard(private))
        self.assertFalse(guard(group))
        self.assertFalse(guard(spoofed))
        self.assertEqual(len(messages), 2)
        self.assertTrue(all("private" in text for _, text in messages))

    def test_portfolio_handlers_gate_group_access_before_any_storage_call(self):
        nodes = {node.name: ast.get_source_segment(MAIN, node)
                 for node in ast.parse(MAIN).body
                 if isinstance(node, ast.FunctionDef)}
        for handler in ("cmd_buy", "cmd_sell", "cmd_portfolio"):
            with self.subTest(handler=handler):
                fragment = nodes[handler]
                guard = fragment.index("_require_private_portfolio(m)")
                first_sensitive = (fragment.index("portfolio.")
                                   if "portfolio." in fragment
                                   else fragment.index("build_portfolio_card("))
                self.assertLess(guard, first_sensitive)
                self.assertIn("_allow_heavy(m)", fragment)

    def test_legacy_json_rejects_11th_symbol_without_damaging_existing(self):
        from test_msk_equity_portfolio_p0 import load_legacy_manager
        manager = load_legacy_manager()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            store = manager(str(path), allow_empty_init=True)
            for i in range(10):
                store.add(1001, f"TEST{i}", 1, 100)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "10 distinct"):
                store.add(1001, "OVER", 1, 100)
            self.assertEqual(path.read_bytes(), before)
            store.add(1001, "TEST0", 1, 200)
            self.assertEqual(store.get(1001)["TEST0"]["qty"], 2)

    def test_positive_timestamp_without_provider_identity_is_not_verified(self):
        import data_engine
        self.assertIsNone(data_engine._verified_quote_payload(
            {"price": 100, "quote_timestamp": self.now_epoch},
            "Yahoo Finance v8", "SBIN.NS", now_epoch=self.now_epoch
        ))

    @property
    def now_epoch(self):
        return datetime(2026, 11, 12, 10, 0, tzinfo=IST).timestamp()


if __name__ == "__main__":
    unittest.main()
