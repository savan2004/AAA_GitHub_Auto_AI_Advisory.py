"""Offline checks for scheduled one-stock fundamental PDF safety."""
import copy
from datetime import date, timedelta
import importlib.util
import os
# The production config requires a Telegram token at import time; tests never send.
os.environ.setdefault('TELEGRAM_TOKEN', '123:offline-test-only')
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("msk_daily_fundamental_pdf", ROOT / "scripts" / "msk_daily_fundamental_pdf.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)


class DailyPdfRules(unittest.TestCase):
    def test_weekday_rotation_and_weekend_skip(self):
        mon = date(2026, 10, 26)
        self.assertEqual(daily.candidates(mon)[0], "RELIANCE")
        self.assertEqual(daily.candidates(mon + timedelta(days=1))[0], "TCS")
        self.assertEqual(daily.candidates(mon + timedelta(days=4))[0], "HINDUNILVR")
        self.assertEqual(daily.candidates(mon + timedelta(days=5)), [])
        self.assertEqual(daily.candidates(mon + timedelta(days=7))[0], "BHARTIARTL")

    def test_durable_claim_blocks_repeat_and_false_ack(self):
        today = date(2026, 10, 26)
        state = {"days": {}}
        meta = {"day": today.isoformat(), "symbol": "RELIANCE", "sha256": "abc"}
        daily.claim(today, state, meta)
        self.assertEqual(state["days"][today.isoformat()]["status"], "CLAIMED")
        with self.assertRaises(ValueError):
            daily.claim(today, state, meta)
        for value in (None, 0, -1, True):
            with self.assertRaises(ValueError):
                daily.confirm(today, state, value)
        daily.confirm(today, state, 413)
        self.assertEqual(state["days"][today.isoformat()]["message_id"], 413)
        with self.assertRaises(ValueError):
            daily.confirm(today, state, 414)

    def test_claim_rejects_wrong_company_date_and_weekend(self):
        today = date(2026, 10, 26)
        for m in (
            {"day": today.isoformat(), "symbol": "INVALID", "sha256": "abc"},
            {"day": "2026-10-27", "symbol": "RELIANCE", "sha256": "abc"},
        ):
            with self.assertRaises(ValueError):
                daily.claim(today, {"days": {}}, m)
        with self.assertRaises(ValueError):
            daily.claim(date(2026, 10, 31), {"days": {}},
                        {"day": "2026-10-31", "symbol": "RELIANCE", "sha256": "abc"})

    def test_missing_or_malformed_durable_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            p = str(Path(temp) / "state.json")
            with self.assertRaises(ValueError):
                daily.load_state(p)
            Path(p).write_text('{"days": []}')
            with self.assertRaises(ValueError):
                daily.load_state(p)
            daily.save_state(p, {"days": {}})
            self.assertEqual(daily.load_state(p), {"days": {}})

    def test_missing_annual_fundamentals_never_publish(self):
        from fixtures import strong
        x = strong()
        self.assertEqual(daily.validate_inputs(x, date(2026, 10, 16)), [])
        x2 = copy.deepcopy(x)
        x2["stm"]["years"] = []
        x2["fund"]["roe"] = None
        x2["extras"]["shareholding"] = []
        errors = daily.validate_inputs(x2, date(2026, 10, 16))
        self.assertTrue(any("annual" in e for e in errors))
        self.assertTrue(any("roe" in e for e in errors))
        self.assertTrue(any("Ownership" in e for e in errors))

    def test_stale_close_blocks_daily_research(self):
        from fixtures import strong
        x = strong()
        issues = daily.validate_inputs(x, date(2026, 10, 26))
        self.assertTrue(any("not dated today" in e for e in issues))

    def test_report_engine_supplies_real_asof_date(self):
        from fixtures import strong
        import report_generator as rg
        R = rg.assemble(strong(), use_ai=False)
        self.assertEqual(R["asof"], "16-Oct-2026")
        self.assertEqual(R["sym"], "STRONGCO")

    def test_actual_fixture_pdf_contains_required_sections(self):
        from fixtures import strong
        import report_generator as rg
        R = rg.assemble(strong(), use_ai=False)
        with tempfile.TemporaryDirectory() as temp:
            pdf = str(Path(temp) / "example.pdf")
            rg.build_pdf(R, pdf)
            self.assertEqual(daily.validate_pdf(pdf, R), [])

if __name__ == "__main__":
    unittest.main()
