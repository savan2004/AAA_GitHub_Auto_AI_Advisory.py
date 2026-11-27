"""Offline regression for the symbol-specific, fail-closed PDF publisher error."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class PdfWithholdDiagnosticsTest(unittest.TestCase):
    def test_withheld_report_names_actual_issuer_and_never_saves(self):
        publisher = Path(__file__).resolve().parents[1] / "scripts" / "publish_research_pdf.py"
        for symbol in ("SBIN", "DLF"):
            with self.subTest(symbol=symbol):
                report = types.ModuleType("report_generator")
                report.generate_stock_report_pdf = lambda *args, **kwargs: (False, None, None)
                snapshot = types.ModuleType("research_pdf_snapshot")
                snapshot._current_session = lambda: "2026-11-18"
                snapshot._verified = lambda *args: self.fail("must not validate withheld PDF")
                snapshot.save_verified_report = lambda *args, **kwargs: self.fail("must not persist withheld PDF")
                snapshot.load_verified_report = lambda *args, **kwargs: self.fail("must not load withheld PDF")
                with patch.dict(sys.modules, {"report_generator": report, "research_pdf_snapshot": snapshot}):
                    spec = importlib.util.spec_from_file_location("publisher_under_test", publisher)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    with self.assertRaisesRegex(RuntimeError, f"Real {symbol} research PDF generation withheld; no snapshot written"):
                        module.main(symbol)


if __name__ == "__main__":
    unittest.main()
