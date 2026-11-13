"""Price/date audit must reject weekends, stale or fictitious OHLCV."""
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from research_pdf_price_guard import verified_completed_price_session


class ResearchReportMarketSessionGuardTests(unittest.TestCase):
    @staticmethod
    def sample(last="2026-11-13"):
        return SimpleNamespace(empty=False,index=[datetime.fromisoformat(last)])

    def test_genuine_completed_session_and_numeric_close(self):
        provider=Mock(return_value=self.sample())
        quality=Mock(return_value={"historical_close":758.2})
        session, close=verified_completed_price_session("DLF","2026-11-13",provider,quality)
        self.assertEqual((session,close),("2026-11-13",758.2))
        provider.assert_called_once_with("DLF","1y")
        quality.assert_called_once()

    def test_reject_stale_quote_and_invalid_close(self):
        for previous in ("2026-11-12","2026-11-11"):
            with self.subTest(previous=previous):
                with self.assertRaisesRegex(RuntimeError,"not the latest"):
                    verified_completed_price_session(
                        "DLF","2026-11-13",lambda *a:self.sample(previous),
                        lambda x:{"historical_close":758.2})
        for invalid in (None,0,-100,float("nan"),float("inf")):
            with self.subTest(invalid=str(invalid)):
                with self.assertRaises(RuntimeError):
                    verified_completed_price_session(
                        "DLF","2026-11-13",lambda *a:self.sample(),
                        lambda x:{"historical_close":invalid})

    def test_reject_invalid_history_or_symbol(self):
        with self.assertRaises(ValueError):
            verified_completed_price_session(
                "NOT_APPROVED","2026-11-13",lambda *a:self.sample(),
                lambda x:{"historical_close":758.2})
        with self.assertRaisesRegex(RuntimeError,"not available"):
            verified_completed_price_session(
                "SBIN","2026-11-13",lambda *a:None,
                lambda x:{"historical_close":758.2})
        with self.assertRaisesRegex(RuntimeError,"safety"):
            verified_completed_price_session(
                "SBIN","2026-11-13",lambda *a:self.sample(),lambda x:None)


    def test_exact_rendered_report_evidence_uses_one_market_fetch(self):
        from research_pdf_price_guard import verified_generated_report_session
        self.assertEqual(
            verified_generated_report_session(
                "DLF", "2026-11-13",
                {"symbol": "DLF", "price_session": "13-Nov-2026",
                 "historical_close": 758.75}),
            ("2026-11-13", 758.75))

    def test_reject_wrong_or_stale_generated_evidence(self):
        from research_pdf_price_guard import verified_generated_report_session
        examples = [
            {"symbol":"DLF","price_session":"12-Nov-2026","historical_close":758.75},
            {"symbol":"SBIN","price_session":"13-Nov-2026","historical_close":758.75},
            {"symbol":"DLF","price_session":"undated","historical_close":758.75},
            {"symbol":"DLF","price_session":"13-Nov-2026","historical_close":None},
        ]
        for evidence in examples:
            with self.subTest(evidence=evidence):
                with self.assertRaises(RuntimeError):
                    verified_generated_report_session("DLF","2026-11-13",evidence)


if __name__ == "__main__":
    unittest.main()
