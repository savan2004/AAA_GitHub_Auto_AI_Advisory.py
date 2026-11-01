"""Offline regressions for monthly-data quality and deterministic draft generation."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monthly_outlook import load_outlook
from monthly_outlook_pipeline import build_draft, atomic_write_json

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def example():
    sectors = {
        "Nifty IT": {"score": 80, "r1m": 4.0, "trend": "Uptrend"},
        "Nifty Pharma": {"score": 70, "r1m": 2.1, "trend": "Uptrend"},
        "Nifty FMCG": {"score": 60, "r1m": 0, "trend": "Sideways"},
        "Nifty Auto": {"score": 45, "r1m": -2.0, "trend": "Sideways"},
        "Nifty Metal": {"score": 25, "r1m": -5, "trend": "Downtrend"},
    }
    return {
        "idx": {
            "Nifty 50": {"ltp": 24100.0, "r1m": 1.5, "r3m": 3.1,
                         "trend": "Uptrend", "ema50": 23890.0},
            "Nifty Bank": {"ltp": 53600.0, "r1m": -1.2, "r3m": 0.3,
                           "trend": "Sideways", "ema50": 54000.0},
        }, "asof": "30-Oct-2026", "sec": sectors,
        "n_unique": 80, "n_configured": 100,
        "p50_all": 64.0, "p200_all": 58.0,
        "swing_high": 24300.0, "swing_low": 23500.0,
        "freshness_warning": None,
    }


class MonthlyPipelineTests(unittest.TestCase):
    def test_draft_is_deterministic_never_approved(self):
        a = build_draft(example(), "2026-11", NOW)
        b = build_draft(example(), "2026-11", NOW)
        self.assertEqual(a, b)
        self.assertIs(a["approved"], False)
        self.assertIn("80/100", a["summary"])
        self.assertIn("Nifty IT", a["summary"])
        self.assertIn("23,500.00", a["summary"])
        self.assertNotIn("70% accuracy", a["summary"])

    def test_missing_benchmark_or_bank_fails_closed(self):
        for name in ("Nifty 50", "Nifty Bank"):
            sample = example()
            del sample["idx"][name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_draft(sample, now=NOW)

    def test_invalid_prices_fails_closed(self):
        sample = example()
        sample["idx"]["Nifty 50"]["ltp"] = float("nan")
        with self.assertRaises(ValueError):
            build_draft(sample, now=NOW)

    def test_insufficient_stock_coverage_fails_closed(self):
        sample = example()
        sample["n_unique"] = 79
        with self.assertRaises(ValueError):
            build_draft(sample, now=NOW)

    def test_insufficient_sector_coverage_fails_closed(self):
        sample = example()
        sample["sec"] = dict(list(sample["sec"].items())[:4])
        with self.assertRaises(ValueError):
            build_draft(sample, now=NOW)

    def test_stale_and_future_dates_fail_closed(self):
        for date in ("19-Oct-2026", "02-Nov-2026"):
            sample = example()
            sample["asof"] = date
            with self.subTest(date=date), self.assertRaises(ValueError):
                build_draft(sample, now=NOW)

    def test_month_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            build_draft(example(), month="2026-10", now=NOW)

    def test_generated_draft_not_visible_to_archive(self):
        data = build_draft(example(), now=NOW)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "2026-11.json"
            atomic_write_json(data, target)
            self.assertEqual(json.loads(target.read_text())["approved"], False)
            self.assertIsNone(load_outlook("2026-11", folder))

    def test_pdf_smoke_if_reportlab_available(self):
        try:
            import reportlab
        except ImportError:
            self.skipTest("reportlab not installed in lightweight CI")
        from monthly_outlook_pipeline import build_pdf
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "draft.pdf"
            build_pdf(build_draft(example(), now=NOW), path)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes()[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()
