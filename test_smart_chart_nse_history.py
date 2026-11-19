"""Offline acceptance for an NSE-only fallback with exact source identity."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import pandas as pd

from smart_chart_nse_history import official_nse_chart_history


class NSEHistoryFallbackTests(unittest.TestCase):
    @staticmethod
    def csv_text(symbol="SBIN", identity=True):
        index = pd.bdate_range(end="2026-11-13", periods=80)
        data = {
            "DATE1": [x.strftime("%d-%m-%Y") for x in index],
            "OPEN PRICE": [100.0] * len(index),
            "HIGH PRICE": [105.0] * len(index),
            "LOW PRICE": [95.0] * len(index),
            "CLOSE PRICE": [102.0] * len(index),
            "TTL TRD QNTY": [15000] * len(index),
            "SERIES": ["EQ"] * len(index),
        }
        if identity:
            data["SYMBOL"] = [symbol] * len(index)
        return pd.DataFrame(data).to_csv(index=False)

    def call_with_csv(self, csv):
        response = SimpleNamespace(
            status_code=200, content=csv.encode("utf-8"), text=csv)
        home = SimpleNamespace(status_code=200, content=b"ok", text="ok")
        with patch("smart_chart_nse_history.requests.Session") as mock_session:
            mock_session.return_value.get.side_effect = [home, response]
            answer = official_nse_chart_history("SBIN.NS", "2026-11-13")
            self.assertEqual(mock_session.return_value.get.call_count, 2)
            requested = mock_session.return_value.get.call_args_list[1].args[0]
            self.assertIn("symbol=SBIN&", requested)
            self.assertIn("to=13-11-2026", requested)
            return answer

    def test_exact_nse_eq_data_is_accepted(self):
        frame = self.call_with_csv(self.csv_text())
        self.assertIsNotNone(frame)
        self.assertEqual(len(frame), 80)
        self.assertEqual(str(frame.index[-1].date()), "2026-11-13")
        self.assertEqual(float(frame["Close"].iloc[-1]), 102.0)

    def test_mismatched_or_missing_provider_identity_is_rejected(self):
        self.assertIsNone(self.call_with_csv(self.csv_text("TCS")))
        self.assertIsNone(self.call_with_csv(self.csv_text(identity=False)))

    def test_no_untrusted_symbols_or_fabricated_data(self):
        with patch("smart_chart_nse_history.requests.Session") as session:
            self.assertIsNone(official_nse_chart_history(
                "NIFTY%20X.NS", "2026-11-13"))
            self.assertIsNone(official_nse_chart_history(
                "SBIN.BO", "2026-11-13"))
            session.assert_not_called()

    def test_provider_outage_fails_closed(self):
        response = SimpleNamespace(
            status_code=429, content=b"rate limited", text="rate limited")
        with patch("smart_chart_nse_history.requests.Session") as session:
            session.return_value.get.side_effect = [response, response]
            self.assertIsNone(official_nse_chart_history(
                "SBIN.NS", "2026-11-13"))


if __name__ == "__main__":
    unittest.main()
