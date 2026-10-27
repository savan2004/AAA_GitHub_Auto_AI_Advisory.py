"""Offline, zero-network validation of filing-backed business model forecasting."""
from pathlib import Path
import os
import tempfile
import unittest

# Existing chart library imports config which needs a token even offline.
os.environ.setdefault("TELEGRAM_TOKEN", "123:fixture-only-not-real")

from business_model_forecast import (
    PROFILES, business_model_for, explain_gap, forecast_segments, validate_profile,
)


class BusinessForecastTests(unittest.TestCase):
    def test_only_filing_verified_company_has_profile(self):
        self.assertIsNone(business_model_for("TCS"))
        self.assertIn("not yet verified", explain_gap("TCS"))
        self.assertEqual(business_model_for("RELIANCE.NS")["fiscal_year"], "FY2025-26")

    def test_official_fy26_ril_segment_baselines(self):
        p = business_model_for("RELIANCE")
        actual = {s["name"]: (s["revenue"], s["ebitda"]) for s in p["segments"]}
        self.assertEqual(actual["Oil to Chemicals"], (662401, 60546))
        self.assertEqual(actual["Retail"], (371085, 27034))
        self.assertEqual(actual["Digital Services"], (176164, 76560))
        self.assertEqual(actual["Oil and Gas"], (23861, 19050))
        self.assertEqual(actual["Media and Entertainment"], (40682, 5842))
        self.assertIn("NOT additive", p["special_note"])
        self.assertIn("New Energy", p["special_note"])

    def test_three_year_math_and_scenario_bounds(self):
        f = forecast_segments(business_model_for("RELIANCE"))
        retail = next(s for s in f["segments"] if s["name"] == "Retail")
        self.assertEqual([x["fiscal_year"] for x in retail["scenarios"]["Base"]],
                         ["FY2027", "FY2028", "FY2029"])
        revenue = retail["actual_revenue_cr"]
        for x in retail["scenarios"]["Base"]:
            revenue *= 1.12
            self.assertAlmostEqual(x["revenue_cr"], revenue, delta=1)
            self.assertAlmostEqual(x["ebitda_cr"], revenue *
                                   retail["reported_ebitda_margin_pct"] / 100, delta=100)
        for i in range(3):
            self.assertLess(retail["scenarios"]["Bear"][i]["revenue_cr"],
                            retail["scenarios"]["Base"][i]["revenue_cr"])
            self.assertLess(retail["scenarios"]["Base"][i]["revenue_cr"],
                            retail["scenarios"]["Bull"][i]["revenue_cr"])
        self.assertNotIn("price_target", f)
        self.assertNotIn("consolidated_eps", f)
        self.assertNotIn("consolidated_pat", f)

    def test_invalid_assumptions_fail_closed(self):
        import copy
        p = copy.deepcopy(business_model_for("RELIANCE"))
        p["segments"][0]["assumed_growth"]["Bull"] = float("nan")
        with self.assertRaises(ValueError):
            validate_profile(p)
        p = copy.deepcopy(business_model_for("RELIANCE"))
        p["segments"][0]["revenue"] = 0
        with self.assertRaises(ValueError):
            validate_profile(p)
        p = copy.deepcopy(business_model_for("RELIANCE"))
        p["segments"][0]["assumed_growth"]["Bear"] = 99
        with self.assertRaises(ValueError):
            validate_profile(p)
        with self.assertRaises(ValueError):
            forecast_segments(business_model_for("RELIANCE"), 4)

    def test_pdf_has_business_first_and_explicit_unsupported_forecasts(self):
        from fixtures import strong
        import report_generator as rg
        from pypdf import PdfReader
        synthetic = strong(sym="RELIANCE", name="Reliance Industries Limited")
        report = rg.assemble(synthetic, use_ai=False)
        with tempfile.TemporaryDirectory() as directory:
            fn = str(Path(directory) / "synthetic_test.pdf")
            rg.build_pdf(report, fn)
            pages = PdfReader(fn).pages
            text = "\n".join((p.extract_text() or "") for p in pages)
            self.assertIn("Business Model & 3-Year Forecast", text)
            self.assertIn("Oil to Chemicals", text)
            self.assertIn("Retail", text)
            self.assertIn("FY2027", text)
            self.assertIn("FY2029", text)
            self.assertIn("hypothetical scenarios", text)
            self.assertIn("NOT additive", text)
            self.assertIn("new energy", " ".join(text.lower().split()))
            self.assertIn("No consolidated projected PAT", " ".join(text.split()))
            self.assertLess(text.index("Business Model & 3-Year Forecast"),
                            text.index("Technical Analysis"))

    def test_other_stocks_do_not_get_ril_segment_numbers(self):
        from fixtures import strong
        import report_generator as rg
        from pypdf import PdfReader
        report = rg.assemble(strong(sym="INFY", name="Infosys Limited"), use_ai=False)
        with tempfile.TemporaryDirectory() as directory:
            fn = str(Path(directory) / "unknown_model.pdf")
            rg.build_pdf(report, fn)
            text = "\n".join((p.extract_text() or "") for p in PdfReader(fn).pages)
            self.assertIn("not yet verified", text)
            self.assertNotIn("662,401", text)
            self.assertNotIn("FY2029", text)


if __name__ == "__main__":
    unittest.main()
