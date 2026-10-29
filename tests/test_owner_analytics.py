"""Offline checks for MSK owner analytics; no live Telegram or database calls."""
import os
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import msk_usage as ua


def private_message(text="/start", update_id=100, user_id=11, username="analyst"):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "first_name": "Test", "username": username},
            "text": text,
        },
    }


class AnalyticsTests(unittest.TestCase):
    def test_private_message_identifies_feature_without_saving_text(self):
        raw = private_message("INFY")
        result = ua.extract_event(raw)
        self.assertEqual(result["feature"], "stock_search")
        self.assertEqual(result["user_id"], 11)
        self.assertNotIn("text", result)
        self.assertNotIn("INFY", str(result))

    def test_buttons_and_commands_are_classified(self):
        self.assertEqual(ua.extract_event(private_message("🏦 Conservative"))["feature"],
                         "conservative")
        self.assertEqual(ua.extract_event(private_message("/report SBIN"))["feature"],
                         "research_pdf")
        cb = {
            "update_id": 105,
            "callback_query": {
                "data": "fund|TCS", "from": {"id": 27, "first_name": "Investor"},
                "message": {"chat": {"type": "private", "id": 27}},
            },
        }
        self.assertEqual(ua.extract_event(cb)["feature"], "fundamental")
        self.assertNotIn("TCS", str(ua.extract_event(cb)))

    def test_ignore_other_group_chatter_bots_and_forward(self):
        raw = private_message("hello")
        raw["message"]["chat"]["type"] = "group"
        self.assertIsNone(ua.extract_event(raw))
        raw["message"]["text"] = "MSK review NSE:TCS"
        self.assertEqual(ua.extract_event(raw)["feature"], "stock_search")
        raw["message"]["is_automatic_forward"] = True
        self.assertIsNone(ua.extract_event(raw))
        raw = private_message("/start")
        raw["message"]["from"]["is_bot"] = True
        self.assertIsNone(ua.extract_event(raw))
        self.assertIsNone(ua.extract_event({"update_id": 11}))

    def test_username_rejected_if_not_safe(self):
        raw = private_message("/start", username='<script>alert(1)</script>')
        self.assertEqual(ua.extract_event(raw)["username"], "")

    def test_html_escapes_user_controlled_fields(self):
        stats = {"users": 1, "active_today": 1, "requests_today": 5,
                 "requests_7d": 5, "features": [("<script>", 5)],
                 "top_users": [(9, "", "<img src=x onerror=evil()>", 5, None)],
                 "as_of": "29 Oct 2026, 08:00 PM IST"}
        result = ua.dashboard_html(stats)
        self.assertNotIn("<script>", result)
        self.assertNotIn("<img src=x", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("Private dashboard", result)
        self.assertNotIn("quota exceeded", result)

    def test_unconfigured_tracking_is_nonblocking(self):
        with patch.dict(os.environ, {ua.DB_ENV: ""}):
            self.assertFalse(ua.record_event(private_message()))
            self.assertFalse(ua.claim_owner(11, "bad"))

    def test_duplicate_update_is_not_double_counted(self):
        cur = Mock()
        cur.fetchone.return_value = None
        context = Mock()
        context.__enter__ = Mock(return_value=cur)
        context.__exit__ = Mock(return_value=False)
        conn = Mock()
        conn.cursor.return_value = context
        wrapper = Mock()
        wrapper.__enter__ = Mock(return_value=conn)
        wrapper.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {ua.DB_ENV: "postgresql://unused"}):
            with patch.object(ua, "_connect", return_value=wrapper):
                self.assertFalse(ua.record_event(private_message()))
        sql_statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("ON CONFLICT (update_id) DO NOTHING" in q for q in sql_statements))
        self.assertFalse(any("request_count=request_count+1" in q for q in sql_statements))

    def test_owner_setup_requires_high_entropy_key_and_one_time_binding(self):
        with patch.dict(os.environ, {ua.DB_ENV: "postgresql://unused",
                                     "MSK_OWNER_SETUP_KEY": "a" * 32}):
            self.assertFalse(ua.claim_owner(99, "wrong"))
            cur = Mock()
            cur.fetchone.return_value = (99,)
            context = Mock()
            context.__enter__ = Mock(return_value=cur)
            context.__exit__ = Mock(return_value=False)
            conn = Mock()
            conn.cursor.return_value = context
            wrapper = Mock()
            wrapper.__enter__ = Mock(return_value=conn)
            wrapper.__exit__ = Mock(return_value=False)
            with patch.object(ua, "_connect", return_value=wrapper):
                self.assertTrue(ua.claim_owner(99, "a" * 32))
                self.assertFalse(ua.claim_owner(100, "wrong"))

    def test_owner_daily_claim_fail_closed_without_database(self):
        with patch.dict(os.environ, {ua.DB_ENV: ""}):
            self.assertFalse(ua.claim_daily_digest(datetime(2026, 10, 29).date()))

    def test_production_route_and_handler_wiring(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('app.route("/owner/usage"', source)
        self.assertIn('commands=["ownerstats", "ownerusers"]', source)
        self.assertIn('commands=["ownerclaim"]', source)
        self.assertIn("hmac.compare_digest(auth.password", source)
        self.assertIn('usage_analytics.record_event', source)
        self.assertIn('usage_analytics.daily_digest_loop', source)
        self.assertIn("no-store, private", source)
        self.assertNotIn("usage_analytics.is_owner(m.chat.id)", source)

    def test_unlimited_ai_flag_skips_daily_quota_but_tracks(self):
        import llm_wrapper
        with patch.dict(os.environ, {"MSK_UNLIMITED_USAGE": "1"}):
            with patch("llm_wrapper.safe_llm_call", return_value=(True, "Research OK")):
                with patch("limits.can_use_llm", side_effect=AssertionError("quota queried")):
                    with patch("limits.register_llm_usage") as register:
                        with patch("history.add_history_item"):
                            self.assertEqual(llm_wrapper.call_llm_with_limits(11, "TEST"),
                                             "Research OK")
                            register.assert_called_once_with(11)


if __name__ == "__main__":
    unittest.main()
