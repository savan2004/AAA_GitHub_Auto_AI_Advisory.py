"""Offline production PDF publication acceptance using synthetic financials.

Never treats PDF structure as proof that issuer accounting values were audited.
"""
import os
os.environ.setdefault("TELEGRAM_TOKEN", "123:synthetic-test")
os.environ.setdefault("MPLBACKEND", "Agg")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fixtures import strong
import report_generator as rg
from pdf_release_gate import validate_research_pdf, ResearchPDFRefused


class PDFReleaseGateTests(unittest.TestCase):
    def test_actual_nine_section_pdf_is_readable_and_financially_disclaimed(self):
        fixture = strong()
        assembled = rg.assemble(fixture, use_ai=False)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "STRONGCO-report.pdf"
            try:
                rg.build_pdf(assembled, str(path))
                review = validate_research_pdf(path, expected_symbol="STRONGCO")
                self.assertGreaterEqual(review["pages"], 8)
                self.assertGreater(review["bytes"], 25000)
                self.assertEqual(review["status"], "structure_and_disclosure_only")
                self.assertFalse(review["issuer_filing_verified"])
                self.assertFalse(review["real_data_reconciled"])
                self.assertFalse(review["telegram_delivered"])
                with self.assertRaisesRegex(ResearchPDFRefused, "identity"):
                    validate_research_pdf(path, expected_symbol="WRONGCO")
                with self.assertRaisesRegex(ResearchPDFRefused, "identity"):
                    validate_research_pdf(path, expected_symbol="TRONGCO")
                with self.assertRaisesRegex(ResearchPDFRefused, "identity"):
                    validate_research_pdf(path, expected_symbol="STRONGCOX")
            finally:
                for key in ("tech_price", "tech_momentum", "tech",
                            "quarterly"):
                    f = (assembled.get("ch") or {}).get(key)
                    if isinstance(f, str):
                        Path(f).unlink(missing_ok=True)

    def test_invalid_empty_corrupt_or_too_short_pdf_never_released(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.pdf"
            for payload in (b"", b"%PDF-1.5", b"not a document"):
                path.write_bytes(payload)
                with self.subTest(payload=payload):
                    with self.assertRaises(ResearchPDFRefused):
                        validate_research_pdf(path)
            with self.assertRaisesRegex(ResearchPDFRefused, "missing"):
                validate_research_pdf(Path(td) / "absent.pdf")

    def test_stale_price_series_cannot_start_live_research(self):
        from datetime import date
        import pandas as pd
        frame = pd.DataFrame({
            "Open": [100.] * 80, "High": [101.] * 80,
            "Low": [99.] * 80, "Close": [100.] * 80,
            "Volume": [1000] * 80,
        }, index=pd.bdate_range(end=date(2025, 7, 15), periods=80))
        with patch("data_engine.get_hist", return_value=frame):
            with self.assertRaisesRegex(ValueError, "stale or unverified"):
                rg.collect_inputs("SBIN")

    def test_invalid_generated_report_is_deleted_before_sender_receives_success(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(rg, "collect_inputs", return_value={}), \
             patch.object(rg, "assemble", return_value={
                 "name": "STRONGCO"
             }), \
             patch.object(rg, "OUTPUT_DIR", td), \
             patch.object(rg, "build_pdf",
                          side_effect=lambda R, path: Path(path).write_bytes(
                              b"%PDF-1.5\ninvalid placeholder")):
            ok, message, name = rg.generate_stock_report_pdf("STRONGCO")
            self.assertFalse(ok)
            self.assertIn("withheld", message)
            self.assertIn("size outside delivery bounds", message)
            self.assertFalse(list(Path(td).glob("*.pdf")))


if __name__ == "__main__":
    unittest.main()
