"""Offline tests for manual collector; no network/API key required."""
import unittest
from unittest.mock import Mock
from standalone_collector.manual_fetch import get_data

class ManualFetchTests(unittest.TestCase):
    def test_success(self):
        session = Mock()
        response = session.get.return_value
        response.content = b'{"status":"success","data":{"revenue":100}}'
        response.json.return_value = {"status":"success","data":{"revenue":100}}
        result = get_data("INE040A01034", "financials", "test-key", session=session)
        self.assertEqual(result["data"]["revenue"], 100)
        self.assertEqual(session.get.call_args.kwargs["headers"]["X-API-Key"], "test-key")
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    def test_missing_key(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            get_data("INE040A01034", "financials", "")

    def test_invalid_isin(self):
        with self.assertRaises(ValueError):
            get_data("SBIN", "financials", "test-key")

    def test_http_failure(self):
        import requests
        session = Mock()
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("401")
        with self.assertRaises(requests.HTTPError):
            get_data("INE040A01034", "financials", "test-key", session=session)

if __name__ == "__main__":
    unittest.main()
