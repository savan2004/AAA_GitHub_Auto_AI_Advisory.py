"""Offline coverage for the Yahoo v10 401 -> session-aware yfinance repair."""
import ast
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from yahoo_session_fundamentals import get_yahoo_session_fundamentals, normalize_yfinance_info

ROOT = Path(__file__).resolve().parents[1]


class YahooSessionNormalizerTests(unittest.TestCase):
    def test_inr_numeric_units_and_provider_are_explicit(self):
        data = {
            "currency": "INR", "longName": "Infosys Ltd",
            "trailingPE": 16.5, "returnOnEquity": 0.319,
            "revenueGrowth": 0.12, "debtToEquity": 9.3,
            "marketCap": 4_000_000_000_000, "regularMarketPrice": 1500,
        }
        normalized = normalize_yfinance_info(data, "INFY.NS", fetched_at=100)
        self.assertEqual(normalized["roe_pct"], 31.9)
        self.assertAlmostEqual(normalized["debt_equity_ratio"], 0.093, places=8)
        self.assertEqual(normalized["revenue_growth_pct"], 12)
        self.assertEqual(normalized["market_cap"], 4_000_000_000_000)
        self.assertEqual(normalized["provider"], "Yahoo Finance (yfinance cookie/session)")
        self.assertNotIn("fiscal_sales_period", normalized)
        self.assertIn("NOT independently verified", normalized["financial_period"])

    def test_never_treat_noninr_nan_boolean_as_real_market_data(self):
        self.assertIsNone(normalize_yfinance_info({"currency":"USD", "trailingPE":17}, "INFY.NS"))
        self.assertIsNone(normalize_yfinance_info({"currency":"INR", "trailingPE":float("nan")}, "INFY.NS"))
        self.assertIsNone(normalize_yfinance_info({"currency":"INR", "trailingPE":17}, "INFY.NS?crumb=bad"))
        result = normalize_yfinance_info(
            {"currency":"INR", "trailingPE":True, "marketCap":1e12, "returnOnEquity":None},
            "INFY.NS", fetched_at=100)
        self.assertIsNone(result["pe"])

    def test_does_not_log_sensitive_provider_exception_message(self):
        def broken(_):
            raise RuntimeError("SECRET_CREDENTIAL_987654")
        with patch("yahoo_session_fundamentals.logger") as logger:
            result = get_yahoo_session_fundamentals("INFY.NS", ticker_factory=broken)
            self.assertIsNone(result)
            self.assertNotIn("SECRET_CREDENTIAL_987654", repr(logger.warning.call_args))

    def test_mock_yfinance_info_adapter(self):
        fake = MagicMock()
        fake.get_info.return_value = {
            "currency": "INR", "trailingPE": 19,
            "marketCap": 3_000_000_000_000, "roe": None,
        }
        out = get_yahoo_session_fundamentals("INFY.NS", ticker_factory=lambda s: fake)
        self.assertEqual(out["pe"], 19)
        self.assertEqual(out["currency"], "INR")


class YahooEngineFallbackTests(unittest.TestCase):
    @staticmethod
    def load_live_function():
        source = (ROOT / "data_engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_yahoo_v10_fundamentals")
        ctx = {
            "_wait_for_rate_slot": lambda: None,
            "_HEADERS": {},
            "logger": MagicMock(),
            "time": MagicMock(time=lambda: 1000),
            "_jitter": lambda x: x,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<data_engine real function>", "exec"), ctx)
        return ctx

    def test_real_401_uses_one_unauthorized_host_then_session_client(self):
        ctx = self.load_live_function()
        request = MagicMock()
        request.get.return_value.status_code = 401
        ctx["requests"] = request
        with patch("yahoo_session_fundamentals.get_yahoo_session_fundamentals",
                   return_value={"pe": 17.0, "market_cap": 1e12}) as client:
            result = ctx["_yahoo_v10_fundamentals"]("INFY.NS")
        self.assertEqual(request.get.call_count, 1)
        client.assert_called_once_with("INFY.NS")
        self.assertEqual(result["pe"], 17.0)

    def test_successful_raw_request_does_not_duplicate_yfinance_calls(self):
        ctx = self.load_live_function()
        response = MagicMock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {
            "quoteSummary": {"result": [{
                "summaryDetail": {"trailingPE": {"raw": 15.0}},
                "price": {"marketCap": {"raw": 4e12}},
                "financialData": {},
            }]}
        }
        ctx["requests"] = MagicMock(get=MagicMock(return_value=response))
        with patch("yahoo_session_fundamentals.get_yahoo_session_fundamentals") as client:
            result = ctx["_yahoo_v10_fundamentals"]("INFY.NS")
        self.assertEqual(result["pe"], 15.0)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
