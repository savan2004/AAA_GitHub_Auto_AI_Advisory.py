"""Offline, privacy-safe Doctor incomplete-report diagnostics and fail-closed tests."""
import io
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import portfolio_doctor_live as doctor
from portfolio_doctor_incomplete import (
    decode_incomplete_diagnostic, encode_incomplete_diagnostic,
    private_incomplete_message,
)


class IncompletePrivateDoctorTests(unittest.TestCase):
    def test_private_diagnostic_lists_only_unverified_symbols_without_values(self):
        failures = {"SAMPLEB": "STALE_PRICES", "SAMPLEC": "NO_PRICES"}
        private = private_incomplete_message(failures)
        self.assertIn("INCOMPLETE PRICE DIAGNOSTIC", private)
        self.assertIn("SAMPLEB", private)
        self.assertIn("SAMPLEC", private)
        self.assertNotIn("SAMPLEA", private)
        self.assertIn("NOT A PORTFOLIO REPORT", private)
        self.assertIn("No partial totals", private)
        self.assertNotIn("₹", private)
        self.assertNotIn("total value", private.lower())

    def test_no_invalid_symbol_or_provider_text_crosses_worker_boundary(self):
        self.assertEqual(encode_incomplete_diagnostic({"<script>": "NO_PRICES"}), b"")
        self.assertEqual(encode_incomplete_diagnostic({"SAMPLEB": "API_KEY=secret"}), b"")
        self.assertIsNone(private_incomplete_message({"SAMPLEB": "arbitrary server error"}))
        valid = encode_incomplete_diagnostic({"SAMPLEB": "NO_PRICES"})
        decoded = decode_incomplete_diagnostic(
            valid, [{"symbol": "SAMPLEA"}, {"symbol": "SAMPLEB"}])
        self.assertEqual(decoded, {"SAMPLEB": "NO_PRICES"})
        self.assertEqual(decode_incomplete_diagnostic(
            valid, [{"symbol": "SAMPLEA"}]), {})
        self.assertEqual(decode_incomplete_diagnostic(b"x"*1025,
            [{"symbol": "SAMPLEB"}]), {})
        self.assertEqual(decode_incomplete_diagnostic(
            b'{"kind":"MSK_DOCTOR_INCOMPLETE_V1","unverified":{"SAMPLEB":"NO_PRICES"},"extra":"token"}',
            [{"symbol": "SAMPLEB"}]), {})

    def test_html_special_symbol_escaped_for_private_telegram(self):
        message = private_incomplete_message({"M&M": "NO_PRICES"})
        self.assertIn("<code>M&amp;M</code>", message)
        self.assertNotIn("<code>M&M</code>", message)

    def test_exception_message_and_logging_code_never_contain_holding(self):
        exc = doctor.PortfolioDoctorError("NO_PRICES",
                                          incomplete={"SAMPLEB": "NO_PRICES"})
        self.assertEqual(str(exc), "NO_PRICES")
        self.assertEqual(exc.code, "NO_PRICES")
        self.assertEqual(exc.incomplete, {"SAMPLEB": "NO_PRICES"})

    def test_one_failed_holding_blocks_all_complete_report_results(self):
        sample_a, sample_b = object(), object()
        fake_market_data = SimpleNamespace(
            bulk_history=lambda *_a, **_kw: {
                "SAMPLEA": sample_a, "SAMPLEB": sample_b,
            })
        def checked(frame, *, now=None):
            if frame is sample_b:
                raise doctor.PortfolioDoctorError("STALE_PRICES")
            return frame
        holdings = [
            {"symbol": "SAMPLEA", "qty": 2, "buy": 100, "exchange": "NSE"},
            {"symbol": "SAMPLEB", "qty": 3, "buy": 200, "exchange": "NSE"},
        ]
        with patch.dict(sys.modules, {"market_data": fake_market_data}), \
             patch.object(doctor, "verified_completed_daily_frame", side_effect=checked), \
             patch.object(doctor, "recover_fresh_daily_for_doctor", return_value=None), \
             patch("portfolio_doctor_bhavcopy.recover_from_public_bhavcopy", return_value={}):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.price_holdings(holdings)
        self.assertEqual(err.exception.code, "STALE_PRICES")
        self.assertEqual(err.exception.incomplete, {"SAMPLEB": "STALE_PRICES"})
        self.assertNotIn("SAMPLEB", str(err.exception))

    def test_parent_extracts_private_child_diagnostic_without_logging_it(self):
        data = encode_incomplete_diagnostic({"SAMPLEB": "NO_PRICES"})
        child = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=data,
            stderr=b"MSK_DOCTOR_CODE=NO_PRICES\\n")
        upload = b"SAMPLEA,1,100\\nSAMPLEB,2,200"
        with patch.object(doctor.subprocess, "run", return_value=child):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory("private.csv", upload)
        self.assertEqual(err.exception.incomplete, {"SAMPLEB": "NO_PRICES"})
        self.assertEqual(str(err.exception), "NO_PRICES")

    def test_parent_rejects_child_diagnostic_with_nonuploaded_symbol(self):
        data = encode_incomplete_diagnostic({"SAMPLEC": "NO_PRICES"})
        child = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=data,
            stderr=b"MSK_DOCTOR_CODE=NO_PRICES\\n")
        upload = b"SAMPLEA,1,100\\nSAMPLEB,2,200"
        with patch.object(doctor.subprocess, "run", return_value=child):
            with self.assertRaises(doctor.PortfolioDoctorError) as err:
                doctor.run_isolated_memory("private.csv", upload)
        self.assertFalse(err.exception.incomplete)

    def test_handler_private_route_does_not_send_pdf_on_missing_prices(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        doctor_handler = src.split("def portfolio_doctor_document(", 1)[1].split(
            "# Portfolio Doctor's optional new-capital builder", 1)[0]
        self.assertIn("private_incomplete_message(exc.incomplete)", doctor_handler)
        self.assertIn("elif exc.code in (\"STALE_PRICES\", \"NO_PRICES\")", doctor_handler)
        self.assertNotIn("send_document(uid, detailed", doctor_handler)
        self.assertIn("safe_send(uid, detailed or", doctor_handler)


if __name__ == "__main__":
    unittest.main()
