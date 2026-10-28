"""Model portfolio snapshot progress regression: offline, no market requests."""
import unittest
from unittest.mock import patch

import research_upgrade as research

NOW = 1_800_000_000


class SnapshotProgressTests(unittest.TestCase):
    def setUp(self):
        self.original_snapshot = research._SNAPSHOT
        self.original_cursor = research._REFRESH_CURSOR
        research._SNAPSHOT = None
        research._REFRESH_CURSOR = 0

    def tearDown(self):
        research._SNAPSHOT = self.original_snapshot
        research._REFRESH_CURSOR = self.original_cursor

    def test_failed_source_does_not_starve_later_nse_symbols(self):
        symbols = [f"S{i:02d}" for i in range(20)]
        queried = []
        def provider(symbol):
            queried.append(symbol)
            return None

        with patch("research_upgrade.time.time", return_value=NOW) as clock:
            for round_number in range(3):
                clock.return_value = NOW + 121 * round_number
                snap = research.snapshot(
                    symbols, lambda _key, _ttl: None,
                    provider, lambda *_a, **_k: {},
                    priority=symbols,
                )
                self.assertLessEqual(snap["refreshed"], 8)
                self.assertEqual(snap["ttl"], 120)
        self.assertEqual(len(queried), 24)
        self.assertEqual(set(queried), set(symbols))
        self.assertEqual(len(set(queried[:8]) & set(queried[8:16])), 0)

    def test_history_download_budget_keeps_refreshing_until_coverage_complete(self):
        symbols = [f"S{i:02d}" for i in range(10)]
        infos = {s: {"roe_pct": 20.0, "market_cap": 1e12, "fetched_at": NOW}
                 for s in symbols}
        downloaded = {}
        def cached(key, _ttl):
            symbol = key.removeprefix("info_v2_").removesuffix(".NS")
            return infos.get(symbol)
        def history(series, period, chunk_size=60):
            self.assertEqual(period, "6mo")
            for symbol in series:
                if symbol not in downloaded:
                    downloaded[symbol] = object()
                    if len(downloaded) % 2 == 0:
                        break
            return {s: downloaded[s] for s in series if s in downloaded}
        def evaluate(symbol, info, frame, now=None):
            return ({"symbol": symbol}, None) if frame is not None else (None, "missing history")

        with patch("research_upgrade.evaluate", side_effect=evaluate):
            with patch("research_upgrade.time.time", return_value=NOW) as clock:
                for round_number in range(5):
                    clock.return_value = NOW + 121 * round_number
                    snap = research.snapshot(
                        symbols, cached,
                        lambda _symbol: self.fail("cached fundamentals must be reused"),
                        history, priority=symbols,
                    )
                    expected_pending = 8 - 2 * round_number
                    self.assertEqual(snap["pending_history"], expected_pending)
                    self.assertEqual(snap["ttl"], 120 if expected_pending else 900)
                    self.assertEqual(len(snap["rows"]), 2 * (round_number + 1))
                clock.return_value = NOW + 121 * 5
                self.assertIs(research.snapshot(symbols, cached, lambda _: None,
                                                history, priority=symbols), snap)
        self.assertEqual(set(downloaded), set(symbols))


if __name__ == "__main__":
    unittest.main()
