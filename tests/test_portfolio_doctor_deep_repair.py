"""Synthetic regression cases; no customer data or Telegram sends."""
import io
import unittest
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace
import sys

import openpyxl
import portfolio_doctor_live as doctor


def xlsx(rows):
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


class DeepRepairTests(unittest.TestCase):
    def test_wilder_rsi14_published_arithmetic_example(self):
        closes = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,
                  45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        self.assertAlmostEqual(doctor._rsi(closes[:15]), 70.4641350211, places=8)
        self.assertAlmostEqual(doctor._rsi(closes), 66.2496185536, places=8)
        self.assertIsNone(doctor._rsi(closes[:14]))
        self.assertEqual(doctor._rsi([100] * 30), 50)
        self.assertEqual(doctor._rsi(list(range(1,31))), 100)
        self.assertEqual(doctor._rsi(list(range(30,0,-1))), 0)

    def test_xlsx_extra_holding_below_old_row_limit_is_rejected(self):
        rows = [["Symbol","Qty","Buy Price"]]
        rows += [["SAMPLE" + str(i), 2, 100] for i in range(5)]
        rows += [[None,None,None]] * 5
        rows += [["EXTRA",1,100]]
        with self.assertRaises(doctor.PortfolioDoctorError) as error:
            doctor.parse_holdings("private.xlsx", xlsx(rows))
        self.assertEqual(error.exception.code, "BAD_INPUT")

    def test_xlsx_five_holdings_with_blank_lines_are_all_retained(self):
        rows = [["Symbol","Qty","Buy Price"]]
        for i in range(5):
            rows += [[None,None,None], ["SAMPLE" + str(i), i+1, 100]]
        holdings = doctor.parse_holdings("private.xlsx", xlsx(rows))
        self.assertEqual(len(holdings), 5)
        self.assertEqual(sum(h["qty"] for h in holdings), 15)

    def test_xlsx_undeclared_dimensions_do_not_hide_rows(self):
        from zipfile import ZipFile, ZIP_DEFLATED
        import re
        original = xlsx([["SAMPLE" + str(i),1,100] for i in range(6)])
        output = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(output,"w",ZIP_DEFLATED) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:C1"', data)
                target.writestr(name,data)
        with self.assertRaises(doctor.PortfolioDoctorError):
            doctor.parse_holdings("private.xlsx",output.getvalue())

    def test_csv_and_typed_five_holdings_preserve_quantity_and_cost(self):
        for delimiter in (" ", ",", ";", "\t"):
            payload = "\n".join(delimiter.join([f"SAMPLE{i}",str(i+1),"100"])
                                for i in range(5)).encode()
            holdings = doctor.parse_holdings("private.csv",payload)
            self.assertEqual(len(holdings),5)
            self.assertEqual(sum(h["qty"] * h["buy"] for h in holdings),1500)

    def test_shared_stooq_fallback_is_disabled_for_doctor(self):
        calls = {}
        def bulk(*args, **kwargs):
            calls.update(kwargs)
            return {}
        fake = SimpleNamespace(bulk_history=bulk)
        with patch.dict(sys.modules,{"market_data":fake}), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy",return_value={}), \
             patch.object(doctor,"recover_fresh_daily_for_doctor",return_value=None):
            with self.assertRaises(doctor.PortfolioDoctorError):
                doctor.price_holdings([{"symbol":"SAMPLEA","qty":1,"buy":100}])
        self.assertEqual(calls["max_fallbacks"],0)


    def test_advanced_report_preserves_verified_basic_indicators(self):
        import pandas as pd
        import portfolio_doctor_advanced as advanced
        row = {"symbol": "SAMPLEA", "qty": 2, "buy": 100, "close": 110,
               "date": "2026-11-17", "invested": 200, "value": 220,
               "pnl": 20, "pnl_pct": 10, "weight": 100,
               "ema20": 106.25, "ema50": 101.75, "rsi14": 66.2496185536,
               "high20": 118, "low20": 95}
        frame = pd.DataFrame({"Close": [110.0] * 65},
                             index=pd.bdate_range(end="2026-11-17", periods=65))
        # The optional research calculator may use a different RSI convention.
        research = SimpleNamespace(compute_technicals=lambda *_: {
            "trend": "UPTREND", "rsi": 12, "ema20": 1, "ema50": 2,
            "vol_ratio": 1.2})
        with patch.dict(sys.modules, {"research_engine": research}):
            review = advanced.assemble_review(
                [row], {"SAMPLEA": frame}, sector_lookup=lambda _: "IT",
                advanced_fundamentals=False)
        tech = review["technicals"][0]
        for metric in ("rsi14", "ema20", "ema50"):
            self.assertEqual(tech[metric], row[metric])
        self.assertEqual(tech["trend"], "UPTREND")
        self.assertEqual(tech["volume_ratio"], 1.2)



    def test_actual_yfinance_caches_cannot_retain_requested_symbols(self):
        try:
            from yfinance import cache
        except ImportError:
            self.skipTest("dependency exercised in advanced CI job")
        with patch.object(cache._TzCacheManager, "_tz_cache", None), \
             patch.object(cache._CookieCacheManager, "_Cookie_cache", None), \
             patch.object(cache._TzDBManager, "get_database",
                          side_effect=AssertionError("disk timezone cache used")), \
             patch.object(cache._CookieDBManager, "get_database",
                          side_effect=AssertionError("disk cookie cache used")):
            doctor.disable_worker_provider_disk_caches()
            cache.get_tz_cache().store("SAMPLE_PRIVATE", "Asia/Kolkata")
            cache.get_cookie_cache().store("basic", {"private": "value"})
            self.assertIsNone(cache.get_tz_cache().lookup("SAMPLE_PRIVATE"))
            self.assertIsNone(cache.get_cookie_cache().lookup("basic"))

    def test_changed_provider_cache_interface_fails_closed(self):
        fake = SimpleNamespace(cache=SimpleNamespace())
        with patch.dict(sys.modules, {"yfinance": fake}):
            with self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.disable_worker_provider_disk_caches()
        self.assertEqual(error.exception.code, "WORKER_FAILED")



    def test_exchange_holidays_special_session_and_year_expiry(self):
        from datetime import date
        cases = [
            ((2026, 11, 24, 18, 0), date(2026, 11, 23)),
            ((2026, 11, 25, 10, 0), date(2026, 11, 23)),
            ((2026, 4, 6, 10, 0), date(2026, 4, 2)),
            ((2026, 11, 8, 16, 0), date(2026, 11, 6)),
            ((2026, 11, 8, 16, 30), date(2026, 11, 8)),
            ((2026, 11, 9, 10, 0), date(2026, 11, 8)),
            ((2026, 1, 15, 18, 0), date(2026, 1, 14)),
        ]
        for stamp, expected in cases:
            with self.subTest(stamp=stamp):
                self.assertEqual(doctor.expected_doctor_session(
                    datetime(*stamp, tzinfo=doctor.IST)), expected)
        with self.assertRaises(doctor.PortfolioDoctorError):
            doctor.expected_doctor_session(datetime(2027, 1, 4, 18, tzinfo=doctor.IST))
        self.assertEqual(doctor.previous_doctor_session(date(2026,11,11)),
                         date(2026,11,9))



    def test_actual_child_blocks_file_and_sqlite_persistence(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        code = """import os, sqlite3, sys
import portfolio_doctor_live as doctor
doctor.enforce_worker_read_only()
for operation in (
    lambda: open(sys.argv[1], "w"),
    lambda: os.open(sys.argv[1], os.O_CREAT | os.O_WRONLY),
    lambda: sqlite3.connect(sys.argv[1]),
):
    try:
        operation()
    except PermissionError as error:
        assert str(error) == "DOCTOR_READ_ONLY"
    else:
        raise AssertionError("write unexpectedly allowed")
assert not os.path.exists(sys.argv[1])
print("READ_ONLY_PASS")
"""
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "synthetic.csv")
            result = subprocess.run([sys.executable, "-B", "-c", code, target],
                                    capture_output=True, text=True, timeout=15,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("READ_ONLY_PASS", result.stdout)
            self.assertEqual(list(Path(folder).iterdir()), [])



    def test_optional_research_deadline_cannot_be_swallowed(self):
        import signal
        import time
        if not hasattr(signal, "setitimer"):
            self.skipTest("Render and Linux CI enforce POSIX optional deadlines")
        old_handler = signal.getsignal(signal.SIGALRM)
        start = time.monotonic()
        with self.assertRaises(doctor.OptionalReviewTimeout):
            with doctor.optional_review_deadline(0.05):
                try:
                    time.sleep(2)
                except Exception:
                    self.fail("ordinary provider catch swallowed optional deadline")
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), old_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        # The timer is gone; normal basic report calculations remain usable.
        self.assertEqual(doctor._rsi(list(range(30))), 100)



    def test_real_worker_optional_timeout_delivers_basic_pdf(self):
        import os
        import subprocess
        code = """import io, os, sys, time
from types import SimpleNamespace
import portfolio_doctor_live as doctor
row = {"symbol": "SAMPLEA", "qty": 2, "buy": 100, "close": 110,
       "date": "2026-11-17", "invested": 200, "value": 220,
       "pnl": 20, "pnl_pct": 10, "weight": 100, "ema20": 106,
       "ema50": 101, "rsi14": 58, "high20": 118, "low20": 95}
doctor.price_holdings = lambda *a, **kw: ([row], {})
doctor.disable_worker_provider_disk_caches = lambda: None
def slow_optional(*a, **kw):
    with doctor.optional_review_deadline(0.05):
        time.sleep(5)
doctor.build_optional_pdf = slow_optional
os.environ["MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED"] = "1"
result = io.BytesIO()
sys.stdin = SimpleNamespace(buffer=io.BytesIO(b"SAMPLEA 2 100"))
sys.stdout = SimpleNamespace(buffer=result)
doctor.worker_main("synthetic.csv")
from pypdf import PdfReader
pdf = PdfReader(io.BytesIO(result.getvalue()))
text = "".join(page.extract_text() or "" for page in pdf.pages)
assert "SAMPLEA" in text and "Rs 200.00" in text and "Rs 220.00" in text
sys.stdout = sys.__stdout__
print("OPTIONAL_TIMEOUT_BASIC_PDF_PASS")
"""
        result = subprocess.run([sys.executable, "-B", "-c", code],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OPTIONAL_TIMEOUT_BASIC_PDF_PASS", result.stdout)

    def test_actual_parent_deadline_kills_worker_and_next_request_recovers(self):
        import importlib.util
        import time
        if importlib.util.find_spec("yfinance") is None:
            self.skipTest("real dependency worker runs in advanced CI")
        start = time.monotonic()
        with self.assertRaises(doctor.PortfolioDoctorError) as first:
            doctor.run_isolated_memory("synthetic.csv", b"SAMPLEA 2 100", timeout=0.01)
        self.assertEqual(first.exception.code, "TIMEOUT")
        self.assertLess(time.monotonic() - start, 5)
        with self.assertRaises(doctor.PortfolioDoctorError) as second:
            doctor.run_isolated_memory("synthetic.csv", b"INVALID_INPUT", timeout=15)
        self.assertEqual(second.exception.code, "BAD_INPUT")


if __name__ == "__main__":
    unittest.main()
