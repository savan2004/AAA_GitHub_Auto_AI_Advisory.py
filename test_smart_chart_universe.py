"""Nifty 500 source validity and bounded partial-source fallback tests."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd
from smart_chart_universe import load_nifty500_universe


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


if __name__ == "__main__":
    unittest.main()
