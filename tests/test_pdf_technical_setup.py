"""Offline image/PDF regression checks for old-chart-inspired V2.2 setup pages."""
from __future__ import annotations
import os
os.environ.setdefault("TELEGRAM_TOKEN", "123:offline-test")
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from fixtures import strong
import pdf_technical_setup as ch


class TechnicalChartRegression(unittest.TestCase):
    def test_valid_eod_history_generates_two_png_pages(self):
        data = strong()
        df = data["df"]
        result = ch.build_setup_charts(df, "STRONGCO", {"ltp": round(float(df["Close"].iloc[-1]), 2)})
        self.assertIsNotNone(result)
        self.assertIn(result["summary"]["status"],
                      ("WAIT / NO CONFIRMED SETUP", "BREAKOUT OBSERVED", "BREAKDOWN OBSERVED"))
        self.assertIn(str(df.index[-1].date()), result["summary"]["date"])
        for key in ("price", "momentum"):
            f = Path(result[key])
            self.assertTrue(f.is_file())
            self.assertGreater(f.stat().st_size, 15000)
            self.assertEqual(f.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            f.unlink()

    def test_short_series_mismatched_last_price_and_corrupt_candle_fail_closed(self):
        df = strong()["df"]
        right = {"ltp": round(float(df["Close"].iloc[-1]), 2)}
        self.assertIsNone(ch.build_setup_charts(df.iloc[-20:], "STRONGCO", right))
        self.assertIsNone(ch.build_setup_charts(df, "STRONGCO", {"ltp": 99999.0}))
        wrong = df.copy()
        wrong.iloc[-1, wrong.columns.get_loc("High")] = 0.1
        self.assertIsNone(ch.build_setup_charts(wrong, "STRONGCO", right))

    def test_adx_does_not_infer_downtrend_from_rising_lows(self):
        dates = pd.bdate_range("2025-01-01", periods=240)
        close = pd.Series(np.arange(240) * .5 + 100, index=dates)
        df = pd.DataFrame({"High": close+1, "Low": close-1, "Close": close})
        adx, plus, minus = ch.adx_series(df)
        self.assertGreater(float(plus.iloc[-1]), 1.0)
        self.assertAlmostEqual(float(minus.iloc[-1]), 0.0, delta=1e-9)
        self.assertGreater(float(adx.iloc[-1]), 75)

    def test_published_adx_matches_chart_and_uses_correct_minus_dm(self):
        from technical_indicators import calc_adx
        df = strong()["df"]
        canonical = calc_adx(df)
        derived = ch.adx_series(df)
        for scalar, full in zip(canonical, derived):
            self.assertAlmostEqual(scalar, round(float(full.iloc[-1]), 1), delta=0.15)

    def test_breakout_signal_needs_prior_high_volume_and_adx_not_just_price(self):
        df = strong()["df"].copy()
        z = ch._series(df)
        # The parser is deterministic: under-2x volume prevents a confirmed marker.
        z["ratio"].iloc[-1] = 1.25
        self.assertFalse(ch.breakout_observation(df, z)["verified"])
        z["ratio"].iloc[-1] = 2.5
        z["adx"].iloc[-1] = 10
        self.assertFalse(ch.breakout_observation(df, z)["verified"])

    def test_pdf_uses_new_charts_not_standalone_network_script(self):
        import report_generator as rg
        from pypdf import PdfReader
        data = strong()
        r = rg.assemble(data, use_ai=False)
        self.assertIsNotNone(r["ch"]["tech_price"])
        self.assertIsNotNone(r["ch"]["tech_momentum"])
        self.assertIsNone(r["ch"]["tech"])
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "full_research.pdf"
            rg.build_pdf(r, str(pdf))
            text = " ".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)
            self.assertIn("Technical Analysis", text)
            self.assertIn("ONE-PAGE DECISION SNAPSHOT", text)
            self.assertIn("Verified quarterly visual trends", text)
            self.assertIn("Technical setup observation", text)
            self.assertIn("No confirmed setup means WAIT", text)
            self.assertGreater(pdf.stat().st_size, 30000)
        for key in ("tech_price", "tech_momentum"):
            Path(r["ch"][key]).unlink(missing_ok=True)

    def test_quarterly_actual_chart_and_missing_period_guard(self):
        from report_quarterly_charts import render_quarterly_chart
        data = strong()
        quarters = data["stm"]["quarters"]
        plot = render_quarterly_chart(quarters, "STRONGCO")
        self.assertIsNotNone(plot)
        file = Path(plot)
        self.assertGreater(file.stat().st_size, 10_000)
        self.assertEqual(file.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        file.unlink(missing_ok=True)
        incomplete = [{**q, "revenue": None} for q in quarters]
        self.assertIsNone(render_quarterly_chart(incomplete, "STRONGCO"))

    def test_pivots_require_right_side_confirmation(self):
        df = strong()["df"]
        # Last candle alone cannot become a confirmed swing pivot.
        p = ch.confirmed_double_pattern(df)
        if p:
            self.assertLess(p["pivots"][-1], 70-4)


if __name__ == "__main__":
    unittest.main()
