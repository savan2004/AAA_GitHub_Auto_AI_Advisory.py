"""Offline safety tests for genuine daily chart PNG delivery from Neon."""
from datetime import date
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
from chart_integration import ChartGenerator
import smart_chart_snapshot as store


SESSION = "2026-11-13"
META = (
    "CONCOR.NS|Concor|BUY|+13/20|442.15|425.00|470.00|490.00|"
    "Sample: 52/60 qualifying NSE setups from 250; "
    "partial-universe scan; Setup: Breakout confirmed: 20-session high + volume; "
    "EOD 2026-11-13; Chart V8"
)


class ChartSnapshotSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate genuine image bytes, not a pretend PNG header.
        image = Image.frombytes("RGB", (1000, 600), os.urandom(1000 * 600 * 3))
        with io.BytesIO() as stream:
            image.save(stream, format="PNG")
            cls.image = stream.getvalue()

    def test_strict_two_mb_gate(self):
        self.assertEqual(store.MAX_PNG_BYTES, 1_999_999)
        with self.assertRaisesRegex(ValueError, "size range"):
            store._validate(self.image.ljust(2_000_000, b"x"), META, SESSION)

    def test_renderer_keeps_highest_dpi_that_fits(self):
        fig = MagicMock()
        attempts = []
        def render(path, **kwargs):
            attempts.append(kwargs["dpi"])
            self.assertEqual(kwargs["pil_kwargs"]["compress_level"], 9)
            payload = self.image if kwargs["dpi"] == 144 else b"x" * 2_000_000
            Path(path).write_bytes(payload)
        fig.savefig.side_effect = render
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "chart.png"
            size, dpi = store.save_bounded_chart_png(fig, destination, "#ffffff")
            self.assertEqual(attempts, [160, 144])
            self.assertEqual(dpi, 144)
            self.assertLess(size, 2_000_000)
            self.assertEqual(destination.read_bytes(), self.image)
            self.assertEqual(list(Path(folder).iterdir()), [destination])

    def test_bounded_renderer_caps_canvas_before_save(self):
        fig = MagicMock()
        fig.get_size_inches.return_value = (16, 10)
        def render(path, **kw):
            self.assertLessEqual(kw["dpi"], 120)
            self.assertIsNone(kw["bbox_inches"])
            Path(path).write_bytes(self.image)
        fig.savefig.side_effect = render
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "chart.png"
            size, dpi = store.save_bounded_chart_png(fig, dest, "#ffffff",
                                                       max_dimensions=(1920, 1200))
            self.assertEqual(dpi, 120)
            self.assertEqual(dest.read_bytes(), self.image)

    def test_chart_worker_defers_plot_imports_and_releases_provider_caches(self):
        source = (Path(__file__).resolve().parent /
                  "gen_smart_stock_chart.py").read_text(encoding="utf-8")
        self.assertLess(source.index('os.environ[_thread_env] = "1"'),
                        source.index("import numpy as np"))
        self.assertLess(source.index("winner, sampled, valid, universe_size = choose_auto_chart("),
                        source.index("import matplotlib\n"))
        self.assertLess(source.index("_provider_cache.clear()"),
                        source.index("import mplfinance as mpf"))
        self.assertIn('_chart_memory_stage("png_written")', source)

    def test_renderer_rejects_oversize_without_exposing_file(self):
        fig = MagicMock()
        fig.savefig.side_effect = lambda path, **kwargs: Path(path).write_bytes(
            b"x" * 2_000_000)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "chart.png"
            with self.assertRaisesRegex(ValueError, "2 MB"):
                store.save_bounded_chart_png(fig, destination, "#ffffff")
            self.assertEqual(fig.savefig.call_count, 4)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_real_png_metadata_and_no_fake_trades(self):
        match = store._validate(self.image, META, SESSION)
        self.assertEqual(match.group(9), "52")
        caption = store.format_chart_meta(META, SESSION)
        self.assertIn("BUY", caption)
        self.assertIn("Confirmed setup: Breakout confirmed:", caption)
        self.assertNotIn("No-Trade Zone", caption)
        self.assertIn("52/60", caption)
        self.assertIn("SL: ₹", caption)

    def test_nifty100_real_scan_metadata_contract(self):
        source = (Path(__file__).resolve().parent /
                  "gen_smart_stock_chart.py").read_text(encoding="utf-8")
        self.assertIn('f"Sample: {valid}/{sampled} qualifying NSE setups from {universe_size}; "', source)
        meta = ("AXISBANK.NS|AXISBANK|BUY|+14/20|1418.10|0.00|0.00|0.00|"
                "Sample: 6/100 qualifying NSE setups from 100; partial-universe scan; "
                "Setup: Trend confirmation: higher highs/lows + weekly trend; "
                "EOD 2026-11-25; Chart V8")
        match = store._validate(self.image, meta, "2026-11-25")
        self.assertEqual(match.group(11), "100")
        self.assertIn("OBSERVATION ONLY", store.format_chart_meta(meta, "2026-11-25"))
        with self.assertRaises(ValueError):
            store._validate(self.image, meta, "2026-11-26")

    def test_reject_stale_metadata_and_corrupt_png(self):
        with self.assertRaisesRegex(ValueError, "session"):
            store._validate(self.image, META, "2026-11-12")
        with self.assertRaises(ValueError):
            store._validate(self.image[:80] + b"x" * 30000, META, SESSION)
        with self.assertRaises(ValueError):
            store._validate(self.image, META.replace("BUY|+13/20", "BUY NOW|+13/20"), SESSION)

    def test_wait_and_weak_old_snapshots_are_blocked(self):
        for weak in ("WAIT|-2/20", "WAIT|+11/20", "BUY|+11/20",
                     "SELL|-12/20"):
            with self.assertRaises(ValueError):
                store._validate(
                    self.image,
                    META.replace("BUY|+13/20", weak), SESSION)

    def test_chart_v8_rejects_legacy_or_invalid_levels_and_displays_missing_targets(self):
        with self.assertRaises(ValueError):
            store._validate(self.image, META.replace("; Chart V8", ""), SESSION)
        with self.assertRaises(ValueError):
            store._validate(self.image, META.replace("425.00|470.00", "450.00|470.00"), SESSION)
        one = META.replace("490.00", "0.00")
        store._validate(self.image, one, SESSION)
        self.assertIn("T2: unavailable", store.format_chart_meta(one, SESSION))
        none = META.replace("425.00|470.00|490.00", "0.00|0.00|0.00")
        store._validate(self.image, none, SESSION)
        caption = store.format_chart_meta(none, SESSION)
        self.assertIn("OBSERVATION ONLY", caption)
        self.assertNotIn("₹0.00", caption)

    def test_cannot_claim_full_universe_when_only_partially_scanned(self):
        with self.assertRaisesRegex(ValueError, "full-universe"):
            store._validate(
                self.image,
                META.replace("partial-universe scan", "full-universe scan"), SESSION)

    def test_db_load_requires_matching_sha_and_writes_verified_file(self):
        context = MagicMock()
        conn = MagicMock()
        context.__enter__.return_value = conn
        expected_sha = hashlib.sha256(self.image).hexdigest()
        cursor = MagicMock()
        cursor.fetchone.return_value = (self.image, expected_sha, META)
        conn.execute.return_value = cursor
        fake = SimpleNamespace(connect=MagicMock(return_value=context))
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(store, "_dsn", return_value="postgresql://example.invalid/db?sslmode=require"), \
             patch.dict(sys.modules, {"psycopg": fake}):
            verified = store.load_verified_daily_chart(SESSION, folder)
            self.assertIsNotNone(verified)
            caption, png_path = verified
            self.assertIn("partial-universe scan", caption)
            self.assertEqual(hashlib.sha256(Path(png_path).read_bytes()).hexdigest(),
                             expected_sha)
            self.assertNotIn("options", fake.connect.call_args.kwargs)
            cursor.fetchone.return_value = (self.image, "0" * 64, META)
            self.assertIsNone(store.load_verified_daily_chart(SESSION, folder))

    def test_auto_button_reads_verified_png_without_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / f"chart_verified_{SESSION}.png"
            target.write_bytes(self.image)
            verified = ("Real EOD chart", str(target))
            fake_breadth = SimpleNamespace(
                expected_completed_session=lambda: date(2026, 11, 13))
            with patch.dict(sys.modules, {"market_breadth": fake_breadth}), \
                 patch("smart_chart_snapshot.load_verified_daily_chart",
                       return_value=verified) as load, \
                 patch("chart_integration.subprocess.run") as subprocess_mock:
                generator = ChartGenerator(output_dir=folder)
                self.assertEqual(generator.generate(), (True, *verified))
                self.assertEqual(generator.generate(), (True, *verified))
                load.assert_called_once_with(SESSION, folder)
                subprocess_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
