"""Offline smoke tests for overnight research date and ready-report handoff."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import overnight_index_research as overnight
import index_top5

IST = ZoneInfo("Asia/Kolkata")


class SessionTests(unittest.TestCase):
    def test_after_close_is_current_session(self):
        self.assertEqual(str(overnight.session_day(datetime(2026, 10, 30, 16, 0, tzinfo=IST))), "2026-10-30")

    def test_before_open_is_previous_close(self):
        self.assertEqual(str(overnight.session_day(datetime(2026, 10, 30, 8, 0, tzinfo=IST))), "2026-10-29")

    def test_monday_morning_falls_back_to_friday(self):
        self.assertEqual(str(overnight.session_day(datetime(2026, 11, 2, 8, 0, tzinfo=IST))), "2026-10-30")

    def test_bot_uses_ready_report_without_scanning(self):
        with patch.object(overnight, "ready_report", return_value="CACHED REPORT"), patch.object(
                index_top5, "_POOL") as pool:
            result = index_top5.request_index_top5("moderate", 123, lambda *_: None)
            self.assertEqual(result, "CACHED REPORT")
            pool.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
