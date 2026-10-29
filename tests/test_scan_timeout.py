"""Offline regression for production 2-of-10 futures timeout.
This test imports only the scanner function AST, never Telegram or yfinance.
"""
import ast
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timezone, timedelta
from conservative_screener import CONSERVATIVE_CANDIDATES
import html
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

SOURCE = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class FakeClose:
    @property
    def iloc(self):
        return self

    def __getitem__(self, index):
        return 101.0 if index == -1 else 100.0


class FakeFrame:
    empty = False

    def __len__(self):
        return 32

    def __getitem__(self, key):
        assert key == "Close"
        return FakeClose()


def scanner_namespace(delays=None, timeout=0.05):
    tree = ast.parse(SOURCE)
    fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
              and x.name == "build_scan")
    listed = next(x for x in tree.body if isinstance(x, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "SCREENER_STOCKS"
                          for t in x.targets))
    # Conservative candidates are now generated from an expanded sector map.
    parsed = {"CONSERVATIVE_CANDIDATES": CONSERVATIVE_CANDIDATES}
    exec(compile(ast.Module(body=[listed], type_ignores=[]), "<universe>", "exec"), parsed)
    symbols = parsed["SCREENER_STOCKS"]
    pool = ThreadPoolExecutor(max_workers=8)
    gate = threading.BoundedSemaphore(1)
    seen = []
    delays = delays or {}

    def fake_hist(sym, period):
        seen.append(sym)
        time.sleep(delays.get(sym, 0.001))
        return FakeFrame()

    ns = {
        "SCREENER_STOCKS": symbols,
        "_SCAN_TIMEOUT_SECONDS": timeout,
        "_SCAN_POOL": pool,
        "_SCAN_GATE": gate,
        "threading": threading,
        "wait": wait,
        "html": html,
        "date": date,
        "datetime": datetime,
        "timezone": timezone,
        "timedelta": timedelta,
        "get_hist": fake_hist,
        "calc_rsi": lambda close: 56,
        "trend_label": lambda close: "UP",
        "swing_signal": lambda rsi, trend, chg: "WATCH",
        "logger": Mock(),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<scanner>", "exec"), ns)
    return ns, pool, gate, seen


class BoundedScannerTests(unittest.TestCase):
    def test_partial_results_before_slow_quotes_finish(self):
        ns, pool, gate, seen = scanner_namespace(
            {"TMPV": 0.40, "ETERNAL": 0.40}, timeout=0.10)
        try:
            began = time.monotonic()
            result = ns["build_scan"]("aggressive")
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, 0.32)
            self.assertIn("Partial scan:", result)
            self.assertIn("stocks returned valid data", result)
            self.assertNotIn("futures unfinished", result)
            # A slow prior scan must not create unlimited new network jobs.
            self.assertIn("still running", ns["build_scan"]("aggressive"))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_completed_profiles_and_html_sensitive_symbols(self):
        ns, pool, gate, seen = scanner_namespace()
        try:
            response = ns["build_scan"]("moderate")
            self.assertIn("M&amp;M", response)
            self.assertIn("RELIANCE", response)
            self.assertNotIn("Partial scan", response)
            self.assertIn("Educational only", response)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_symbols_are_exchange_current(self):
        ns, pool, gate, seen = scanner_namespace()
        try:
            symbols = ns["SCREENER_STOCKS"]["aggressive"]
            self.assertIn("TMPV", symbols)
            self.assertIn("ETERNAL", symbols)
            self.assertNotIn("ZOMATO", symbols)
            self.assertNotIn("TATAMOTORS", symbols)
            self.assertEqual(len(symbols), 10)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_busy_gate_does_not_fetch(self):
        ns, pool, gate, seen = scanner_namespace()
        try:
            self.assertTrue(gate.acquire(blocking=False))
            self.assertIn("still running", ns["build_scan"]("conservative"))
            self.assertEqual(seen, [])
        finally:
            gate.release()
            pool.shutdown(wait=True, cancel_futures=True)

    def test_unknown_profile_no_network(self):
        ns, pool, gate, seen = scanner_namespace()
        try:
            self.assertIn("Unknown profile", ns["build_scan"]("bogus"))
            self.assertEqual(seen, [])
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
