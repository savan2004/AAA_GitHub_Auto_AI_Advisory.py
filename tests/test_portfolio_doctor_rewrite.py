"""Rewrite acceptance: private input, credential boundary, money and PDF layout."""
import io
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
import pandas as pd
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

import portfolio_doctor_live as doctor
from portfolio_doctor_calculations import position_values


def workbook(rows):
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


class RewriteInputTests(unittest.TestCase):
    def test_boolean_spreadsheet_values_are_not_numeric_holdings(self):
        for row in (["SAMPLEA", True, 100], ["SAMPLEA", 1, False]):
            with self.subTest(row=row), self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.parse_holdings("input.xlsx", workbook([row]))
            self.assertEqual(error.exception.code, "BAD_INPUT")

    def test_malformed_csv_has_safe_input_error(self):
        for payload in (b'"SAMPLEA",7,"120', b"SAMPLEA,7,120,extra",
                        b"SAMPLEA 7 inf", b"SAMPLEA 0 100"):
            with self.subTest(payload=payload), self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.parse_holdings("input.csv", payload)
            self.assertEqual(error.exception.code, "BAD_INPUT")
            self.assertNotIn("SAMPLEA", str(error.exception))

    def test_nse_prefix_is_canonical_and_does_not_duplicate_position(self):
        parsed = doctor.parse_holdings("input.csv", b"NSE:SAMPLEA 7 120")
        self.assertEqual(parsed[0]["symbol"], "SAMPLEA")
        with self.assertRaises(doctor.PortfolioDoctorError):
            doctor.parse_holdings("input.csv", b"SAMPLEA 7 120\nNSE:SAMPLEA.NS 2 130")
        self.assertEqual(doctor.parse_holdings("input.csv", b"SAMPLEA.BO 7 120")[0],
                         {"symbol": "SAMPLEA", "qty": 7., "buy": 120., "exchange": "BSE"})

    def test_malformed_private_rows_do_not_become_company_search(self):
        for text in ("SAMPLEA seven 120", "SAMPLEA 7 bad",
                     "NSE:SAMPLEA 7 120", "SAMPLEA.BO 7 120"):
            with self.subTest(text=text):
                self.assertTrue(doctor.looks_like_holdings_text(text))
        for text in ("3M India Limited", "Lloyds Metals and Energy", "NSE:INFY"):
            self.assertFalse(doctor.looks_like_holdings_text(text))

    def test_duplicate_or_macro_archive_is_rejected_without_extraction(self):
        import zipfile
        for macro in (True, False):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                if macro:
                    archive.writestr("xl/vbaProject.bin", b"not allowed")
                else:
                    archive.writestr("../escape.xml", b"not allowed")
            with self.assertRaises(doctor.PortfolioDoctorError) as error:
                doctor.parse_holdings("input.xlsx", output.getvalue())
            self.assertEqual(error.exception.code, "BAD_INPUT")


class RewriteBoundaryTests(unittest.TestCase):
    def test_worker_gets_no_application_credentials(self):
        source = {"PATH": "/runtime/bin", "HOME": "/runtime/home",
                  "SSL_CERT_FILE": "/runtime/cert.pem",
                  "MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED": "1",
                  "TELEGRAM_TOKEN": "synthetic-secret",
                  "BOT_TOKEN": "synthetic-secret",
                  "MSK_PORTFOLIO_DOCTOR_LEDGER_DATABASE_URL": "synthetic-secret",
                  "MSK_ANALYTICS_DATABASE_URL": "synthetic-secret",
                  "MSK_INDEX_DATABASE_URL": "synthetic-secret",
                  "PORTFOLIO_DATABASE_URL": "synthetic-secret",
                  "OPENAI_KEY": "synthetic-secret",
                  "GROQ_API_KEY": "synthetic-secret"}
        result = doctor.worker_environment(source)
        self.assertEqual(result["PATH"], source["PATH"])
        self.assertEqual(result["SSL_CERT_FILE"], source["SSL_CERT_FILE"])
        self.assertEqual(result["MSK_RESEARCH_ONLY"], "1")
        self.assertEqual(result["MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED"], "1")
        self.assertNotIn("synthetic-secret", result.values())
        self.assertEqual(source["TELEGRAM_TOKEN"], "synthetic-secret")

    def test_default_history_recovery_does_not_use_raw_nse_adjustment_basis(self):
        index = pd.bdate_range(end="2026-11-17", periods=60)
        frame = pd.DataFrame({"Open": 100., "High": 101., "Low": 99.,
                              "Close": 100., "Volume": 1000.}, index=index)
        calls = []
        fake = SimpleNamespace(
            _yahoo_v8_hist=lambda *a, **kw: (calls.append("Yahoo") or frame),
            _nse_hist=lambda *a, **kw: self.fail("unchecked raw NSE history used"))
        with patch.dict(sys.modules, {"data_engine": fake}):
            result = doctor.recover_fresh_daily_for_doctor(
                "SAMPLEA", now=datetime(2026, 11, 18, 9, tzinfo=doctor.IST))
        self.assertIsNotNone(result)
        self.assertEqual(calls, ["Yahoo"])

    def test_decimal_money_does_not_accumulate_binary_subtraction_error(self):
        result = position_values({"qty": 0.1, "buy": 0.2}, 0.3)
        self.assertEqual(result["invested"], 0.02)
        self.assertEqual(result["value"], 0.03)
        self.assertEqual(result["pnl"], 0.01)
        self.assertEqual(result["pnl_pct"], 50.0)


class RewritePDFTests(unittest.TestCase):
    def test_large_numbers_and_complete_technical_text_fit_page(self):
        row = {"symbol": "SAMPLEABCDEFGHIJKLMN", "qty": 1e9,
               "buy": 999999999.13, "close": 999999999.97,
               "date": "2026-11-17", "invested": 0, "value": 0,
               "pnl": 0, "pnl_pct": 0, "weight": 0,
               "ema20": 1e20, "ema50": 1e19, "rsi14": 55,
               "high20": 1e20, "low20": 100}
        raw = doctor.build_pdf_bytes([row])
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        text = "\n".join(page.extract_text() or "" for page in pages)
        self.assertIn("999,999,999,130,000,000.00", text)
        self.assertIn("Mixed; no clear agreement between the averages", " ".join(text.split()))
        overflow = []
        for page in pages:
            def visit(text, cm, tm, font, size):
                label = text.rstrip("\n")
                if label and font:
                    face = str(font.get("/BaseFont", "/Helvetica")).lstrip("/")
                    if float(tm[4]) + stringWidth(label, face, size) > float(page.mediabox.width) - 41:
                        overflow.append(label)
            page.extract_text(visitor_text=visit)
        self.assertEqual(overflow, [])
        self.assertLessEqual(len(pages), 5)


class NumberedIndianSymbolTests(unittest.TestCase):
    def test_both_requested_formats_have_identical_positions(self):
        expected = [{"symbol": "SBIN", "qty": 100., "buy": 840.},
                    {"symbol": "BHEL", "qty": 150., "buy": 150.}]
        for text in ("SBIN 100 840\n\nBhel 150 150",
                     "1. SBIN 100 840\n2. BHEL 150 150",
                     "1) sbin 100 840\n2) bhel 150 150",
                     "1. SBIN,100,840\n2. Bhel,150,150"):
            with self.subTest(text=text):
                self.assertEqual(doctor.parse_holdings("input.csv", text.encode()), expected)
                self.assertTrue(doctor.looks_like_holdings_text(text))

    def test_suffixes_numeric_bse_codes_and_invalid_rows(self):
        self.assertEqual(doctor.parse_holdings("input.csv", b"500325 2 100")[0]["symbol"], "500325")
        for raw in (b"BSE:SBIN 2 100", b"sbin.bo 2 100"):
            self.assertEqual(doctor.parse_holdings("input.csv", raw)[0]["exchange"], "BSE")
        self.assertEqual(doctor.parse_holdings("input.csv", b"sbin.ns 2 100")[0]["symbol"], "SBIN")
        for raw in (b"1. SBIN 2 100\n2. sbin.NS 3 100",
                    b"NSE:SBIN.BO 2 100", b"BSE:SBIN.NS 2 100",
                    b"1. SBIN 100 nope", b"1. SBIN 100 840 extra"):
            with self.subTest(raw=raw), self.assertRaises(doctor.PortfolioDoctorError):
                doctor.parse_holdings("input.csv", raw)
        for raw in ("1. SBIN 100 nope", "2. BHEL 150", '1. "SBIN",2,"100'):
            self.assertTrue(doctor.looks_like_holdings_text(raw))
        self.assertFalse(doctor.looks_like_holdings_text("3M India Limited"))

    def _response(self, data):
        return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)

    def _chart(self, *, symbol="SAMPLEA.BO", currency="INR", exchange="BSE"):
        stamps = pd.bdate_range(end="2026-11-17", periods=30, tz="Asia/Kolkata")
        return {"chart": {"result": [{
            "meta": {"symbol": symbol, "currency": currency,
                     "exchangeName": exchange, "instrumentType": "EQUITY"},
            "timestamp": [int(s.timestamp()) for s in stamps],
            "indicators": {"quote": [{"open": [100.]*30, "high": [101.]*30,
                "low": [99.]*30, "close": [100.]*30, "volume": [1000]*30}]}}]}}

    def test_exact_bse_only_fallback_and_chart_identity(self):
        quote = {"symbol": "SAMPLEA.BO", "exchange": "BSE", "quoteType": "EQUITY"}
        now = datetime(2026, 11, 18, 9, tzinfo=doctor.IST)
        calls = []
        def get(url, **kw):
            calls.append((url, kw["timeout"]))
            return self._response({"quotes": [quote]} if "search" in url else self._chart())
        result = doctor.bse_daily_for_doctor("SAMPLEA", now=now, get=get)
        self.assertEqual(len(result), 30)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(timeout <= 8 for _, timeout in calls))
        for overrides in ({"currency": "USD"}, {"symbol": "OTHER.BO"}, {"exchange": "NSI"}):
            def bad_get(url, **kw):
                return self._response(self._chart(**overrides))
            self.assertIsNone(doctor.bse_daily_for_doctor(
                "SAMPLEA", now=now, explicit=True, get=bad_get))

    def test_bse_never_guesses_similar_stock_or_overrides_nse(self):
        base = {"symbol": "SAMPLEA.BO", "exchange": "BSE", "quoteType": "EQUITY"}
        for quotes in ([base, {"symbol": "SAMPLEA.NS"}], [base, base],
                       [{**base, "symbol": "SIMILAR.BO"}], [], [{**base, "quoteType": "ETF"}]):
            def get(url, **kw):
                self.assertIn("search", url)
                return self._response({"quotes": quotes})
            self.assertIsNone(doctor.bse_daily_for_doctor("SAMPLEA", get=get))

    def test_verified_bse_prices_reach_pdf_without_nse_recovery(self):
        import portfolio_doctor_report
        index = pd.bdate_range(end="2026-11-17", periods=30)
        frame = pd.DataFrame({"Open":100., "High":101., "Low":99.,
                              "Close":100., "Volume":1000}, index=index)
        fake_market = SimpleNamespace(bulk_history=lambda *a, **kw: {})
        with patch.dict(sys.modules, {"market_data": fake_market}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", side_effect=AssertionError("NSE recovery")), \
             patch.object(doctor, "bse_daily_for_doctor", return_value=frame) as bse:
            rows = doctor.price_holdings([{"symbol":"SAMPLEA", "qty":2., "buy":90., "exchange":"BSE"}])
        bse.assert_called_once()
        self.assertIn("BSE:", rows[0]["price_source"])
        self.assertEqual(rows[0]["value"], 200.)
        pdf = portfolio_doctor_report.build_pdf_bytes(rows)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("Source: BSE exact ticker", text)

    def test_bse_short_history_stale_prices_and_duplicates_are_rejected(self):
        now = datetime(2026, 11, 18, 9, tzinfo=doctor.IST)
        from copy import deepcopy
        for kind in ("short", "stale", "duplicate", "invalid"):
            chart = deepcopy(self._chart())
            data = chart["chart"]["result"][0]
            if kind == "short":
                data["timestamp"] = data["timestamp"][-4:]
                for key, values in data["indicators"]["quote"][0].items():
                    data["indicators"]["quote"][0][key] = values[-4:]
            elif kind == "stale":
                data["timestamp"] = [stamp - 86400 for stamp in data["timestamp"]]
            elif kind == "duplicate":
                data["timestamp"][-1] = data["timestamp"][-2]
            else:
                data["indicators"]["quote"][0]["high"][-1] = 1.
            with self.subTest(kind=kind):
                self.assertIsNone(doctor.bse_daily_for_doctor(
                    "SAMPLEA", now=now, explicit=True,
                    get=lambda *a, **kw: self._response(chart)))

    def test_explicit_nse_never_falls_back_to_bse(self):
        fake = SimpleNamespace(bulk_history=lambda *a, **kw: {})
        with patch.dict(sys.modules, {"market_data": fake}), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy", return_value={}), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None), \
             patch.object(doctor, "bse_daily_for_doctor", side_effect=AssertionError("exchange changed")):
            for payload in (b"NSE:SAMPLEA 2 100", b"SAMPLEA.NS 2 100"):
                with self.assertRaises(doctor.PortfolioDoctorError) as error:
                    doctor.price_holdings(doctor.parse_holdings("input.csv", payload))
                self.assertEqual(error.exception.code, "NO_PRICES")
