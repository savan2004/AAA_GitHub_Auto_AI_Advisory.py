"""Nifty 500 source validity and bounded partial-source fallback tests."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd
from smart_chart_universe import load_nifty500_universe, load_nifty100_universe, NSE_NIFTY100_URLS


class UniverseTests(unittest.TestCase):
    def test_500_real_index_members_are_loaded(self):
        symbols = ["NIFTY" + str(i) for i in range(500)]
        text = pd.DataFrame({"Company Name": symbols, "Symbol": symbols}).to_csv(index=False)
        response = SimpleNamespace(content=text.encode(), text=text)
        response.raise_for_status = lambda: None
        with patch("smart_chart_universe.requests.get", return_value=response):
            universe, source = load_nifty500_universe()
        self.assertEqual(source, "NSE_NIFTY500_CONSTITUENTS_CSV")
        self.assertEqual(len(universe), 500)
        self.assertIn(("NIFTY249.NS", "NIFTY249"), universe)

    def test_outage_labels_curated_partial_not_500(self):
        import requests
        with patch("smart_chart_universe.requests.get",
                   side_effect=requests.Timeout("index unavailable")):
            universe, source = load_nifty500_universe()
        self.assertEqual(source, "CURATED_PARTIAL_UNIVERSE_FALLBACK")
        self.assertTrue(10 < len(universe) < 500)
        self.assertTrue(all(sym.endswith(".NS") for sym, _ in universe))

    def test_malformed_constituent_file_rejected(self):
        response = SimpleNamespace(content=b"noise", text="noise")
        response.raise_for_status = lambda: None
        with patch("smart_chart_universe.requests.get", return_value=response):
            _, source = load_nifty500_universe()
        self.assertEqual(source, "CURATED_PARTIAL_UNIVERSE_FALLBACK")


class Nifty100UniverseTests(unittest.TestCase):
    @staticmethod
    def response(symbols):
        text = pd.DataFrame({"Symbol": symbols}).to_csv(index=False)
        return SimpleNamespace(content=text.encode(), text=text,
                               raise_for_status=lambda: None)

    def test_exact_membership_and_official_url(self):
        symbols = [f"STOCK{i}" for i in range(100)]
        with patch("smart_chart_universe.requests.get",
                   return_value=self.response(symbols)) as fetch:
            universe, source = load_nifty100_universe()
        self.assertEqual(source, "NSE_NIFTY100_CONSTITUENTS_CSV")
        self.assertEqual(universe, [(s + ".NS", s) for s in symbols])
        self.assertEqual(fetch.call_args.args[0], NSE_NIFTY100_URLS[0])

    def test_secondary_official_source(self):
        import requests
        with patch("smart_chart_universe.requests.get", side_effect=[
                requests.Timeout(), self.response([f"STOCK{i}" for i in range(100)])]):
            self.assertEqual(len(load_nifty100_universe()[0]), 100)

    def test_reject_wrong_count_and_duplicate_members(self):
        for symbols in ([f"STOCK{i}" for i in range(500)],
                        [f"STOCK{i}" for i in range(99)] + ["STOCK0"]):
            with self.subTest(count=len(symbols)):
                with patch("smart_chart_universe.requests.get",
                           return_value=self.response(symbols)):
                    with self.assertRaises(RuntimeError):
                        load_nifty100_universe()

    def test_official_component_union_only_when_disjoint(self):
        import requests
        from smart_chart_universe import NSE_COMPONENT_URLS
        first = [f"STOCK{i}" for i in range(50)]
        second = [f"STOCK{i}" for i in range(50, 100)]
        failures = [requests.Timeout()] * len(NSE_NIFTY100_URLS)
        with patch("smart_chart_universe.requests.get", side_effect=failures +
                   [self.response(first), self.response(second)]) as fetch:
            members, source = load_nifty100_universe()
        self.assertEqual(len(members), 100)
        self.assertEqual(source, "NSE_NIFTY50_PLUS_NEXT50")
        self.assertEqual(fetch.call_args.args[0], NSE_COMPONENT_URLS[1])
        with patch("smart_chart_universe.requests.get", side_effect=failures +
                   [self.response(first), self.response(first)]):
            with self.assertRaises(RuntimeError):
                load_nifty100_universe()

    def test_snapshot_accepts_only_same_day_and_official_source(self):
        import tempfile
        from pathlib import Path
        from datetime import datetime, timedelta
        from smart_chart_universe import IST, _save_members, _read_members
        symbols = [f"STOCK{i}" for i in range(100)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "public.json"
            now = datetime(2026, 11, 27, 12, tzinfo=IST)
            _save_members(path, symbols, [NSE_NIFTY100_URLS[0]], now)
            self.assertEqual(_read_members(path, now + timedelta(hours=1))[0], symbols)
            self.assertIsNone(_read_members(path, now - timedelta(seconds=1)))
            self.assertIsNone(_read_members(path, now + timedelta(hours=13)))
            _save_members(path, symbols, ["https://example.com"], now)
            self.assertIsNone(_read_members(path, now))

    def test_manual_path_does_not_load_universe(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parent /
                  "gen_smart_stock_chart.py").read_text(encoding="utf-8")
        self.assertNotIn("load_nifty500_universe", source)
        self.assertGreater(source.index("load_nifty100_universe()"),
                           source.index('SCAN: skipped (manual)'))
        self.assertIn("recovery=shortlist_recovery", source)


if __name__ == "__main__":
    unittest.main()
