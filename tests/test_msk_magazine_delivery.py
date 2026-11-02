"""Offline, no-Telegram-network tests for approved MSK magazine copying."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from msk_magazine_delivery import (
    MagazineManifestError, copy_approved_magazine, find_magazine,
)

ENV = {
    "MSK_MAGAZINE_ENABLED": "true",
    "MSK_MAGAZINE_SOURCE_CHAT": "@AiAutoUpdater",
}

class FakeBot:
    def __init__(self):
        self.copies = []
    def copy_message(self, *args, **kwargs):
        self.copies.append((args, kwargs))

def approved(issue="2026-11"):
    return {
        "issue": issue, "source_project": "AiAutoUpdate",
        "approved": True, "approved_by": "MSK Research Editor",
        "title": "MSK Monthly Market Outlook",
        "published_at": "2026-11-02", "as_of": "2026-10-30",
        "source_chat": "@AiAutoUpdater", "source_message_id": 777,
        "mime_type": "application/pdf", "verified_pdf_sha256": "a" * 64,
    }

def save(root, issue, data):
    (Path(root) / (issue + ".json")).write_text(json.dumps(data), encoding="utf-8")

class MagazineCopyTests(unittest.TestCase):
    def test_disabled_no_access_or_send(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            save(root, "2026-11", approved())
            self.assertEqual(copy_approved_magazine(
                bot, 123, env={}, root=root, now=date(2026,11,3)), "disabled")
            self.assertEqual(bot.copies, [])

    def test_approved_copy_and_explicit_archive(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            save(root, "2026-11", approved())
            status = copy_approved_magazine(
                bot, 123, month="2026-11", env=ENV, root=root, now=date(2026,11,3))
            self.assertEqual(status, "sent")
            self.assertEqual(len(bot.copies), 1)
            self.assertEqual(bot.copies[0][0], (123, "@AiAutoUpdater", 777))
            self.assertIn("2026-11", bot.copies[0][1]["caption"])

    def test_unapproved_never_sent(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            record = approved()
            record["approved"] = False
            save(root, "2026-11", record)
            self.assertEqual(copy_approved_magazine(
                bot, 123, month="2026-11", env=ENV, root=root,
                now=date(2026,11,3)), "unavailable")
            self.assertEqual(bot.copies, [])

    def test_missing_and_bad_manifest_fail_closed(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(copy_approved_magazine(
                bot, 123, env=ENV, root=root, now=date(2026,11,3)), "unavailable")
            bad = approved()
            bad["verified_pdf_sha256"] = ""
            save(root, "2026-11", bad)
            with self.assertRaises(MagazineManifestError):
                copy_approved_magazine(bot, 123, env=ENV,
                                       root=root, now=date(2026,11,3))
            bad = approved()
            bad["source_chat"] = "@OtherBot"
            save(root, "2026-11", bad)
            with self.assertRaises(MagazineManifestError):
                copy_approved_magazine(bot, 123, env=ENV,
                                       root=root, now=date(2026,11,3))
            self.assertEqual(bot.copies, [])

    def test_path_traversal_and_future_dates_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            save(root, "2026-11", approved())
            with self.assertRaises(MagazineManifestError):
                find_magazine("../secrets", env=ENV, root=root,
                              now=date(2026,11,3))
            with self.assertRaises(MagazineManifestError):
                find_magazine("2026-11", env=ENV, root=root,
                              now=date(2026,11,1))

    def test_approved_manifest_publication_month_must_match_issue(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            record = approved("2026-11")
            record["published_at"] = "2026-10-31"
            record["as_of"] = "2026-10-30"
            save(root, "2026-11", record)
            with self.assertRaisesRegex(MagazineManifestError, "publication month"):
                copy_approved_magazine(
                    bot, 123, month="2026-11", env=ENV,
                    root=root, now=date(2026, 11, 3))
            self.assertEqual(bot.copies, [])

    def test_symlinked_manifest_cannot_escape_approved_archive(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            outside_manifest = Path(outside) / "external.json"
            outside_manifest.write_text(json.dumps(approved()), encoding="utf-8")
            (Path(root) / "2026-11.json").symlink_to(outside_manifest)
            with self.assertRaisesRegex(MagazineManifestError, "Symlinked"):
                copy_approved_magazine(
                    bot, 123, month="2026-11", env=ENV,
                    root=root, now=date(2026, 11, 3))
            self.assertEqual(bot.copies, [])

    def test_symlinked_archive_root_is_rejected(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as parent:
            actual = Path(parent) / "real"
            actual.mkdir()
            save(actual, "2026-11", approved())
            alias = Path(parent) / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(MagazineManifestError, "Symlinked"):
                copy_approved_magazine(
                    bot, 123, month="2026-11", env=ENV,
                    root=alias, now=date(2026, 11, 3))
            self.assertEqual(bot.copies, [])

    def test_latest_skips_unapproved_draft(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            save(root, "2026-10", {**approved("2026-10"),
                 "published_at": "2026-10-05", "as_of": "2026-10-02"})
            save(root, "2026-11", {**approved(), "approved": False})
            self.assertEqual(copy_approved_magazine(
                bot, 123, env=ENV, root=root, now=date(2026,11,3)), "sent")
            self.assertIn("2026-10", bot.copies[0][1]["caption"])

if __name__ == "__main__":
    unittest.main()
