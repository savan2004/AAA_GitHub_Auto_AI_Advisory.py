import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import date

from msk_ai_builder_bank import BankError
from msk_ai_builder_weekly import stage_week
from msk_ai_builder_weekly_job import run_weekly_job
from test_msk_ai_builder_evidence import NOW
from test_msk_ai_builder_weekly import CLOSE, inputs, provider


class WeeklyJobTests(unittest.TestCase):
    def test_default_disabled_without_database_or_provider(self):
        bank, refresh = Mock(), Mock()
        result = run_weekly_job(environ={}, bank_factory=bank, refresh=refresh)
        self.assertEqual(result["status"], "DISABLED")
        self.assertEqual(result["publication_status"], "WITHHELD")
        bank.assert_not_called()
        refresh.assert_not_called()

    def test_invalid_flag_and_disabled_bank_fail_closed(self):
        bank, refresh = Mock(return_value=None), Mock()
        with self.assertRaisesRegex(BankError, "INVALID_WEEKLY_JOB_FLAG"):
            run_weekly_job(environ={"MSK_BUILDER_WEEKLY_JOB_ENABLED": "true"},
                           bank_factory=bank, refresh=refresh)
        bank.assert_not_called()
        with self.assertRaisesRegex(BankError, "WEEKLY_BANK_DISABLED"):
            run_weekly_job(environ={"MSK_BUILDER_WEEKLY_JOB_ENABLED": "1"},
                           bank_factory=bank, refresh=refresh)
        refresh.assert_not_called()

    def test_enabled_invokes_once_per_week_and_withholds(self):
        bank = Mock()
        refresh = Mock(return_value=(SimpleNamespace(
            status="INTERNAL_SECONDARY_STAGING", attempted=("A", "B"),
            companies=("A",), failures={"B": "EOD_MISSING"}), "version"))
        result = run_weekly_job(environ={"MSK_BUILDER_WEEKLY_JOB_ENABLED": "1"},
                                bank_factory=Mock(return_value=bank), refresh=refresh)
        refresh.assert_called_once_with(bank, once_per_week=True)
        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["failure_counts"], {"EOD_MISSING": 1})
        self.assertEqual(result["publication_status"], "WITHHELD")

    def test_same_session_week_skips_before_provider_calls(self):
        bank, roster, closes = Mock(), Mock(), Mock()
        bank.read_for_review.return_value = ("old", {"close_date": CLOSE.isoformat()})
        with patch.dict("sys.modules", {"portfolio_doctor_live": SimpleNamespace(
                expected_doctor_session=lambda now: CLOSE)}):
            result, version = stage_week(bank, now=NOW, once_per_week=True,
                                        roster_provider=roster, close_provider=closes)
        self.assertEqual(result.status, "SKIPPED_ALREADY_REVIEWED")
        self.assertIsNone(version)
        roster.assert_not_called()
        closes.assert_not_called()
        bank.replace.assert_not_called()

    def test_prior_session_week_is_refreshed(self):
        bank = Mock()
        bank.read_for_review.return_value = ("old", {"close_date": "2026-11-13",
                                                    "companies": []})
        bank.replace.return_value = "new"
        roster, closes = inputs()
        with patch.dict("sys.modules", {"portfolio_doctor_live": SimpleNamespace(
                expected_doctor_session=lambda now: CLOSE)}):
            result, version = stage_week(bank, now=NOW, once_per_week=True,
                roster_provider=lambda: roster, close_provider=lambda day, rows: closes,
                evidence_provider=provider)
        self.assertEqual(version, "new")
        self.assertEqual(len(result.companies), 20)
        self.assertEqual(bank.replace.call_args.kwargs["expected_version"], "old")

    def test_corrupt_or_future_head_is_rejected(self):
        bank = Mock()
        with patch.dict("sys.modules", {"portfolio_doctor_live": SimpleNamespace(
                expected_doctor_session=lambda now: CLOSE)}):
            for value, code in (("nonsense", "BANK_CLOSE_DATE_INVALID"),
                                ("2026-11-23", "REVIEW_ROLLBACK")):
                bank.read_for_review.return_value = ("old", {"close_date": value})
                with self.assertRaisesRegex(BankError, code):
                    stage_week(bank, now=NOW, once_per_week=True, roster_provider=Mock())
        bank.replace.assert_not_called()
