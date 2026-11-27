import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch, Mock
import msk_ai_data_recovery as recovery

NOW = date(2026, 11, 26)
PERIOD = date(2026, 9, 30)
URL = ("https://nsearchives.nseindia.com/corporate/ixbrl/"
       "INTEGRATED_FILING_INDAS_203729_23102026202117_iXBRL_WEB.html")
ENV = {"MSK_AI_RECOVERY_FREE_PROVIDER": "groq", "GROQ_API_KEY": "test-only",
       "MSK_AI_ALLOW_PAID": "1", "OPENAI_API_KEY": "never-pass",
       "MSK_AI_ALLOW_ASKFUZZ": "1", "ASKFUZZ_API_KEY": "never-pass"}

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        recovery._attempts.clear()
        recovery._week = None

    def test_opt_in_required(self):
        ask = Mock()
        self.assertEqual(recovery.discover_filing("INFY", PERIOD, ["eps"], today=NOW,
                         env={}, ask=ask)["status"], "disabled")
        ask.assert_not_called()

    def test_lead_only_and_weekly_dedup(self):
        ask = Mock(return_value=SimpleNamespace(ok=True,
                   text='{"source_url":"'+URL+'"}'))
        r = recovery.discover_filing("INFY", PERIOD, ["eps"], today=NOW, env=ENV, ask=ask)
        self.assertEqual(r["status"], "unverified_lead")
        self.assertEqual(r["source_url"], URL)
        self.assertNotIn("value", r)
        self.assertEqual(ask.call_args.kwargs["env"], {"GROQ_API_KEY": "test-only"})
        self.assertEqual(recovery.discover_filing("INFY", PERIOD, ["revenue"],
                         today=NOW, env=ENV, ask=ask), r)
        self.assertEqual(ask.call_count, 1)
        recovery.discover_filing("INFY", PERIOD, ["eps"], today=date(2026,11,30), env=ENV, ask=ask)
        self.assertEqual(ask.call_count, 2)

    def test_reject_values_unapproved_links_and_failure(self):
        for symbol, text in (("INFY", '{"source_url":"'+URL+'","eps":19.32}'),
                             ("TCS", '{"source_url":"https://evil.example/a"}'),
                             ("SBIN", '{"source_url":"'+URL+'?redirect=1"}'),
                             ("DMART", '19.32')):
            ask = Mock(return_value=SimpleNamespace(ok=True, text=text))
            self.assertEqual(recovery.discover_filing(symbol, PERIOD, ["eps"], today=NOW,
                             env=ENV, ask=ask)["status"], "withheld_no_lead")
        ask = Mock(side_effect=TimeoutError)
        self.assertEqual(recovery.discover_filing("HDFCBANK", PERIOD, ["eps"], today=NOW,
                         env=ENV, ask=ask)["status"], "withheld_no_lead")

    def test_weekly_cap(self):
        ask = Mock(return_value=SimpleNamespace(ok=False))
        with patch.object(recovery, "_MAX_WEEKLY", 1):
            recovery.discover_filing("INFY", PERIOD, ["eps"], today=NOW, env=ENV, ask=ask)
            self.assertEqual(recovery.discover_filing("TCS", PERIOD, ["eps"], today=NOW,
                             env=ENV, ask=ask)["status"], "weekly_limit")
        self.assertEqual(ask.call_count, 1)

    def test_calendar_rules(self):
        self.assertFalse(recovery.refresh_due(date(2026,11,23), today=NOW, kind="missing"))
        self.assertTrue(recovery.refresh_due(date(2026,11,20), today=NOW, kind="technical"))
        self.assertFalse(recovery.refresh_due(date(2026,11,1), today=NOW, kind="fundamental"))
        self.assertTrue(recovery.refresh_due(date(2026,10,31), today=NOW, kind="fundamental"))
        self.assertTrue(recovery.refresh_due(NOW, today=NOW, kind="fundamental", new_filing=True))

    def test_statement_recovery_does_not_fill_missing_values(self):
        import research_data as rd
        row = {"label":"Sep 2026","revenue":100,"net_profit":10,"eps":None}
        with patch.object(rd, "_cget", return_value=None), patch.object(rd, "_cset"), \
             patch.object(rd, "get_screener_extras", return_value={"quarters":[row]}), \
             patch.object(rd, "_yahoo_statements", return_value={"years":[],"quarters":[]}), \
             patch.object(recovery, "discover_filing", return_value={"status":"unverified_lead",
                          "source_url":URL}) as ai:
            result = rd.get_statements("INFY", today=NOW)
        self.assertIsNone(result["quarters"][-1]["eps"])
        self.assertEqual(result["ai_recovery"]["status"], "unverified_lead")
        ai.assert_called_once()
