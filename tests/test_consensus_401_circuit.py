"""No repeated Yahoo 401 calls; analyst targets are never fabricated."""
import sys
import types
import unittest
from unittest.mock import patch
import research_data

class ConsensusProviderTests(unittest.TestCase):
    def setUp(self):
        research_data._cache.clear()
        research_data._CONSENSUS_AUTH_BLOCKED = False

    def tearDown(self):
        research_data._cache.clear()
        research_data._CONSENSUS_AUTH_BLOCKED = False

    def test_auth_failure_stops_subsequent_requests(self):
        calls = []
        class Provider:
            @property
            def analyst_price_targets(self):
                calls.append("requested")
                raise RuntimeError("HTTP Error 401: Unauthorized")
        fake = types.SimpleNamespace(Ticker=lambda symbol: Provider())
        with patch.dict(sys.modules, {"yfinance": fake}):
            self.assertIsNone(research_data.get_consensus("SBIN", 750))
            self.assertIsNone(research_data.get_consensus("INFY", 1500))
        self.assertEqual(calls, ["requested"])

    def test_successful_consensus_is_restricted_to_verified_range(self):
        fake = types.SimpleNamespace(
            Ticker=lambda symbol: types.SimpleNamespace(
                analyst_price_targets={"mean": 900, "high": 950, "low": 800}))
        with patch.dict(sys.modules, {"yfinance": fake}):
            self.assertEqual(research_data.get_consensus("SBIN", 750)["mean"], 900)
            self.assertIsNone(research_data.get_consensus("EXTREME", 10))

if __name__ == "__main__":
    unittest.main()
