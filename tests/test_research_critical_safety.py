"""Critical research-report safety regressions; offline, no Telegram or live quotes."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "123:offline-test")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import fixtures as fx
import research_engine as eng
import report_generator as rg
import fundamental_pipeline as fp


class CriticalResearchReportSafety(unittest.TestCase):
    def test_bear_target_cannot_be_above_breakdown(self):
        t = {"supports": [{"price": 203.17}],
             "pivots": {"S2": 201.10, "S3": 199.61},
             "ema200": 245.84, "w52l": 203.00}
        target = rg._scenario_objective(t, 203.17, "bear")
        self.assertIn("201.10", target)
        self.assertNotIn("245.84", target)

    def test_no_lower_support_is_explicitly_unconfirmed(self):
        t = {"supports": [{"price": 203.17}],
             "pivots": {}, "ema200": 245.84, "w52l": 203.0}
        self.assertEqual(rg._scenario_objective(t, 203.17, "bear"),
                         "Lower support not confirmed")

    def test_bull_objective_above_trigger_only(self):
        t = {"resistances": [{"price": 206.73}, {"price": 208.5}],
             "pivots": {"R2": 208.22}}
        x = rg._scenario_objective(t, 206.73, "bull")
        self.assertIn("208.22", x)

    def test_jiofin_is_not_misread_as_manufacturing(self):
        inp = fx.strong(sym="JIOFIN", name="Jio Financial Services Limited", sector="Others")
        inp["extras"]["growth"]["Compounded Profit Growth"]["3 Years"] = 293.0
        report = rg.assemble(inp, use_ai=False)
        self.assertTrue(report["fv"]["fin"])
        self.assertIsNone(report["fv"]["de"])
        self.assertIsNone(report["fv"]["ocf_pat"])
        self.assertIsNone(report["fv"]["peg"])
        self.assertEqual(report["sector"], "")
        self.assertIn("inferred", report["sector_note"])
        self.assertFalse(any("Conservative balance sheet" in x or "PEG of" in x
                             or "operating leverage" in x for x in report["flags"]["strengths"]))
        self.assertFalse(any(x["title"] == "Weak cash conversion" for x in report["flags"]["risks"]))

    def test_pdf_does_not_call_pillar_availability_full_data_coverage(self):
        from pypdf import PdfReader
        report = rg.assemble(fx.strong(), use_ai=False)
        with tempfile.TemporaryDirectory() as d:
            pdf = os.path.join(d, "sample.pdf")
            rg.build_pdf(report, pdf)
            text = " ".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
        self.assertIn("Pillar coverage 6/6", text)
        self.assertIn("NOT full field or source verification", text)
        self.assertNotIn("Data coverage 100%", text)
        self.assertIn("INTERNAL RESEARCH DRAFT", text)

    def test_automated_publication_requires_explicit_approval(self):
        inp = fx.strong()
        report = rg.assemble(inp, use_ai=False)
        gate = fp.validate_report({"symbol": inp["sym"]}, inp, report, pdf_path=None,
                                  now=datetime(2026, 10, 16, 12, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        self.assertFalse(gate.ok)
        self.assertTrue(any("automated publication blocked" in e for e in gate.errors), gate.errors)


if __name__ == "__main__":
    unittest.main()
