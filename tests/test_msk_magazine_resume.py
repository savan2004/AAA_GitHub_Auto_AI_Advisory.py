from datetime import date
import hashlib
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from scripts.msk_magazine_monthly import UNAPPROVED
from scripts.msk_magazine_resume import advance, next_page


class DraftRelease:
    def __init__(self):
        self.exists = False
        self.private_draft = True
        self.files = {}

    def run(self, command, action, tag, *args):
        output = ""
        if action == "view":
            if not self.exists:
                return CompletedProcess([], 1, "", "release not found")
            assets = [{"name": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                      for name, data in self.files.items()]
            output = json.dumps({"isDraft": self.private_draft, "assets": assets})
        elif action == "create":
            assert "--draft" in args
            self.exists = True
        elif action == "download":
            (Path(args[3]) / args[1]).write_bytes(self.files[args[1]])
        elif action == "upload":
            path = Path(args[0])
            assert path.name not in self.files
            self.files[path.name] = path.read_bytes()
        else:
            raise AssertionError(action)
        return CompletedProcess([], 0, output, "")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.release = DraftRelease()

    def tick(self, day):
        with tempfile.TemporaryDirectory() as runner:
            return advance(date(2026, 11, day), Path(runner), self.release.run)

    def supply(self, page, title):
        buffer = io.BytesIO()
        c = Canvas(buffer, pagesize=A4)
        c.drawString(30, 600, f"2026-11 {page:02d}/16 {title}")
        c.drawString(30, 30, UNAPPROVED)
        c.save()
        self.release.files[f"page_{page:02d}.pdf"] = buffer.getvalue()

    def test_first_monday_start_and_daily_resume_in_fresh_runners(self):
        self.tick(1)
        self.assertFalse(self.release.exists)
        self.assertIn("Awaiting", self.tick(2))
        self.supply(1, "Monthly Market Outlook")
        self.supply(2, "Editor's Note & Index")
        self.assertIn("Staged unapproved page 01", self.tick(3))
        first = dict(self.release.files)
        self.tick(3)
        self.assertEqual(first, self.release.files)
        self.assertIn("Staged unapproved page 02", self.tick(4))
        self.assertTrue(first.items() <= self.release.files.items())

    def test_missing_month_after_first_monday_is_not_created(self):
        self.tick(3)
        self.assertFalse(self.release.exists)

    def test_public_release_is_rejected(self):
        self.release.exists = True
        self.release.private_draft = False
        with self.assertRaisesRegex(ValueError, "must be a draft"):
            self.tick(3)

    def test_wrong_page_title_cannot_advance(self):
        self.tick(2)
        self.supply(1, "Different title")
        with self.assertRaisesRegex(ValueError, "Missing expected"):
            self.tick(3)
        self.assertEqual(list(self.release.files), ["page_01.pdf"])

    def test_invalid_checkpoint_hash_blocks_resume(self):
        asset = {"name": "page_01_2026-11-02_" + "0"*64 + ".pdf", "digest": None}
        with self.assertRaisesRegex(ValueError, "checksum"):
            next_page([asset], date(2026, 11, 3))
