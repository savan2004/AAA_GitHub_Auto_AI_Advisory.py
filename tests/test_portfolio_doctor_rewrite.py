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
        with self.assertRaises(doctor.PortfolioDoctorError):
            doctor.parse_holdings("input.csv", b"SAMPLEA.BO 7 120")

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
        self.assertIn("Mixed / insufficient trend confirmation", " ".join(text.split()))
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
