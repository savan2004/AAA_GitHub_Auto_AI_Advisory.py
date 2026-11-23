"""Offline MSK Chart V8 structure regression: no market API or customer data."""
import unittest
from pathlib import Path

from msk_chart_context import summarize_chart, completed_weekly_reference


def sample(length=240, trend=2.0, volume=1000.0):
    closes = [200 + trend * i for i in range(length)]
    highs = [v + 1.0 for v in closes]
    lows = [v - 1.0 for v in closes]
    volumes = [volume] * length
    return highs, lows, closes, volumes


class MSKChartContextTests(unittest.TestCase):
    def test_positive_breakout_excludes_latest_bar_and_has_four_periods(self):
        h, l, c, v = sample()
        old_resistance = max(h[-21:-1])
        v[-1] = 3000.0
        info = summarize_chart(h, l, c, v, c[-1] - 3, c[-1] - 7, 2.0)
        self.assertEqual(info["resistance"], old_resistance)
        self.assertEqual(info["level"], "20D UPSIDE CLOSE")
        self.assertEqual(info["outlook"], "POSITIVE MOMENTUM")
        self.assertEqual(info["confirmation"], "PRICE + TREND + VOLUME ALIGNED")
        self.assertEqual(info["volume_ratio"], 3.0)
        self.assertEqual(set(info["windows"]), {20, 50, 100, 200})
        self.assertTrue(all(x == "ABOVE" for x in info["windows"].values()))

    def test_intrabar_wick_is_not_a_closing_break(self):
        h, l, c, v = sample(80, trend=0)
        h[-1] = 1000.0
        info = summarize_chart(h, l, c, v, c[-1] + 1, c[-1] - 1, 0)
        self.assertEqual(info["level"], "INSIDE PREVIOUS 20D RANGE")
        self.assertEqual(info["resistance"], 201)
        self.assertEqual(info["confirmation"], "NO NEW LEVEL BREAK")
        self.assertEqual(set(info["windows"]), {20, 50})

    def test_negative_breakout_with_volume_confirmation(self):
        h, l, c, v = sample(80, trend=-2.0)
        v[-1] = 2500.0
        info = summarize_chart(h, l, c, v, c[-1] + 1, c[-1] + 5, -1.0)
        self.assertEqual(info["level"], "20D DOWNSIDE CLOSE")
        self.assertEqual(info["outlook"], "NEGATIVE MOMENTUM")
        self.assertEqual(info["confirmation"], "PRICE + TREND + VOLUME ALIGNED")
        self.assertEqual(set(info["windows"]), {20, 50})

    def test_missing_volume_does_not_claim_confirmation(self):
        h, l, c, v = sample(80)
        v[-3] = 0
        info = summarize_chart(h, l, c, v, c[-1] - 3, c[-1] - 7, 2)
        self.assertIsNone(info["volume_ratio"])
        self.assertEqual(info["confirmation"], "PRICE BREAK; CONFIRMATION MIXED")

    def test_invalid_data_rejected(self):
        h, l, c, v = sample(80)
        h[5] = c[5] - 20
        with self.assertRaisesRegex(ValueError, "Invalid OHLCV"):
            summarize_chart(h, l, c, v, 200, 199, 1)
        with self.assertRaisesRegex(ValueError, "55"):
            summarize_chart(h[:20], l[:20], c[:20], v[:20], 200, 199, 1)

    def test_completed_weekly_reference_excludes_in_progress_week(self):
        import pandas as pd
        dates = pd.bdate_range("2026-08-03", "2026-11-23")
        daily = pd.DataFrame({"Close": [200.0 + i for i in range(len(dates))]},
                             index=dates)
        from_monday = completed_weekly_reference(daily)
        self.assertIsNotNone(from_monday)
        self.assertEqual(str(from_monday.index[-1].date()), "2026-11-20")
        completed_friday = completed_weekly_reference(daily.loc[:"2026-11-20"])
        self.assertEqual(str(completed_friday.index[-1].date()), "2026-11-20")
        self.assertIsNone(completed_weekly_reference(daily.head(15)))

    def test_manual_chart_cache_cannot_cross_session_or_reuse_deleted_png(self):
        import subprocess
        import tempfile
        import time
        from unittest.mock import patch
        from chart_integration import ChartGenerator

        with tempfile.TemporaryDirectory() as root:
            generator = ChartGenerator(output_dir=root)
            saved = Path(root) / "cached.png"
            saved.write_bytes(b"test-only")
            generator.cache["SBIN.NS|6mo|2026-11-20"] = {
                "ts": time.time(), "path": str(saved), "meta": "prior-session"}
            failed = subprocess.CompletedProcess(
                args=["offline"], returncode=1, stdout="",
                stderr="[ERROR] Expected offline stop")
            with patch("market_breadth.expected_completed_session",
                       return_value="2026-11-20"), patch("subprocess.run",
                                                       return_value=failed) as runner:
                ok, meta, path = generator.generate("SBIN.NS", "SBI", "6mo")
                self.assertTrue(ok)
                self.assertEqual(meta, "prior-session")
                runner.assert_not_called()
            with patch("market_breadth.expected_completed_session",
                       return_value="2026-11-23"), patch("subprocess.run",
                                                       return_value=failed) as runner:
                ok, _, _ = generator.generate("SBIN.NS", "SBI", "6mo")
                self.assertFalse(ok)
                runner.assert_called_once()
            saved.unlink()
            with patch("market_breadth.expected_completed_session",
                       return_value="2026-11-20"), patch("subprocess.run",
                                                       return_value=failed) as runner:
                ok, _, _ = generator.generate("SBIN.NS", "SBI", "6mo")
                self.assertFalse(ok)
                runner.assert_called_once()

    def test_production_generator_uses_safe_helper_and_completed_session(self):
        # Ensures a standalone helper isn't "tested but never called".
        source = (Path(__file__).with_name("gen_smart_stock_chart.py")
                  .read_text(encoding="utf-8"))
        self.assertIn("chart_context = summarize_chart(", source)
        self.assertIn("from msk_chart_context import summarize_chart", source)
        self.assertIn("expected_eod = str(expected_completed_session())", source)
        self.assertIn("vol_s.shift(1).rolling(20", source)
        self.assertNotIn('interval="1wk"', source)
        self.assertIn("weekly_data = completed_weekly_reference(data)", source)
        self.assertIn('support = chart_context["support"]', source)
        self.assertIn('resistance = chart_context["resistance"]', source)


if __name__ == "__main__":
    unittest.main()
