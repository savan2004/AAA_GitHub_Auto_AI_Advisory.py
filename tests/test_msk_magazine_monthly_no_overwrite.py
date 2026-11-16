"""Regression: monthly magazine generation must never overwrite existing issues."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from scripts.msk_magazine_monthly import MagazineError, generate


class MagazineNoOverwriteTests(unittest.TestCase):
    def test_existing_issue_preserves_all_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue_dir = root / "2026-11"
            issue_dir.mkdir()
            prior = issue_dir / "release_gate.json"
            prior.write_bytes(b'{"approved": false, "evidence": "original"}')
            with self.assertRaisesRegex(MagazineError, "refusing to overwrite"):
                generate("2026-11", root, now=datetime(2026, 11, 16, tzinfo=ZoneInfo("Asia/Kolkata")))
            self.assertEqual(prior.read_bytes(), b'{"approved": false, "evidence": "original"}')
            self.assertEqual(sorted(p.name for p in issue_dir.iterdir()), ["release_gate.json"])

    def test_existing_empty_issue_directory_is_not_reused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026-11").mkdir()
            with self.assertRaisesRegex(MagazineError, "already exists"):
                generate("2026-11", root, now=datetime(2026, 11, 16, tzinfo=ZoneInfo("Asia/Kolkata")))
            self.assertEqual(list((root / "2026-11").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
