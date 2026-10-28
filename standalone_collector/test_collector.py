"""Offline tests: python -m unittest discover -s standalone_collector -p 'test_*.py'"""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from standalone_collector.collector import normalize, fetch, seed

class CollectorTests(unittest.TestCase):
    def sample(self):
        return {"symbol":"RELIANCE","provider":"LicensedFeed","source_url":"https://example.org/report",
                "statement_period":datetime.now(timezone.utc).strftime("%Y-%m"),
                "roe_pct":12.5,"pe":22.0}
    def test_valid_snapshot(self):
        self.assertEqual(normalize("RELIANCE", self.sample())["metrics"]["pe"],22.0)
    def test_rejects_wrong_company(self):
        with self.assertRaises(ValueError):
            normalize("TCS", self.sample())
    def test_rejects_nan_and_bool(self):
        for bad in (float("nan"),True,"22"):
            row=self.sample()
            row["pe"]=bad
            with self.assertRaises(ValueError):
                normalize("RELIANCE",row)
    def test_rejects_missing_source_and_future_period(self):
        row=self.sample()
        row["source_url"]="http://example.org/report"
        with self.assertRaises(ValueError):
            normalize("RELIANCE",row)
        row=self.sample()
        row["statement_period"]="2099-12"
        with self.assertRaises(ValueError):
            normalize("RELIANCE",row)
    def test_no_api_configuration_fails_closed(self):
        with patch.dict("os.environ", {"COLLECTOR_API_BASE_URL":""}):
            with self.assertRaises(ValueError):
                fetch("RELIANCE")
    def test_no_redirects_and_bounded_request(self):
        response=Mock()
        response.content=b"{}"
        response.json.return_value=self.sample()
        session=Mock()
        session.get.return_value=response
        with patch.dict("os.environ", {"COLLECTOR_API_BASE_URL":"https://api.example.org"}):
            self.assertEqual(fetch("RELIANCE",session)["symbol"],"RELIANCE")
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(session.get.call_args.kwargs["timeout"],(5,20))
    def test_rejects_invalid_seed_symbols(self):
        with self.assertRaises(ValueError):
            seed(Mock(), ["RELIANCE","BAD;DROP TABLE"])
if __name__=="__main__":
    unittest.main()
