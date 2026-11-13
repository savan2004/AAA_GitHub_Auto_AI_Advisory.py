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
    "CONCOR.NS|Concor|WAIT|+0/20|442.15|0.00|0.00|0.00|"
    "Sample: 52/60 verified NSE symbols from 250; "
    "not a full-universe ranking; EOD 2026-11-13"
)


class ChartSnapshotSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate genuine image bytes, not a pretend PNG header.
        image = Image.frombytes("RGB", (1000, 600), os.urandom(1000 * 600 * 3))
        with io.BytesIO() as stream:
            image.save(stream, format="PNG")
            cls.image = stream.getvalue()

    def test_real_png_metadata_and_no_fake_trades(self):
        match = store._validate(self.image, META, SESSION)
        self.assertEqual(match.group(9), "52")
        caption = store.format_chart_meta(META, SESSION)
        self.assertIn("WAIT", caption)
        self.assertIn("No-Trade Zone", caption)
        self.assertIn("52/60", caption)
        self.assertNotIn("SL: ₹", caption)

    def test_reject_stale_metadata_and_corrupt_png(self):
        with self.assertRaisesRegex(ValueError, "session"):
            store._validate(self.image, META, "2026-11-12")
        with self.assertRaises(ValueError):
            store._validate(self.image[:80] + b"x" * 30000, META, SESSION)
        with self.assertRaises(ValueError):
            store._validate(self.image, META.replace("WAIT", "BUY NOW"), SESSION)

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
            self.assertIn("not a full-universe ranking", caption)
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
