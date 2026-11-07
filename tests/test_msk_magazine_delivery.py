"""Offline, no-Telegram-network tests for approved MSK magazine copying."""
import json
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from msk_magazine_delivery import (
    MagazineManifestError, copy_approved_magazine, find_magazine, verify_source_pdf_bytes,
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
        return type("TelegramReceipt", (), {"message_id": 321})()

def approved(issue="2026-11"):
    return {
        "issue": issue, "source_project": "AiAutoUpdate",
        "approved": True, "approved_by": "MSK Research Editor",
        "approval_timestamp": "2026-11-02T10:00:00+05:30",
        "title": "MSK Monthly Market Outlook",
        "published_at": "2026-11-02", "as_of": "2026-10-30",
        "source_chat": "@AiAutoUpdater", "source_message_id": 777,
        "mime_type": "application/pdf", "verified_pdf_sha256": "a" * 64,
    }

def save(root, issue, data):
    (Path(root) / (issue + ".json")).write_text(json.dumps(data), encoding="utf-8")

class MagazineCopyTests(unittest.TestCase):
    def test_real_pdf_bytes_checksum_accepts_exact_content_only(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "edition.pdf"
            content = b"%PDF-1.4\\n1 0 obj\\n<<>>\\nendobj\\n%%EOF"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            self.assertTrue(verify_source_pdf_bytes(path, digest))
            path.write_bytes(content + b"tampered")
            with self.assertRaisesRegex(MagazineManifestError, "SHA256 mismatch"):
                verify_source_pdf_bytes(path, digest)

    def test_source_pdf_bytes_rejects_non_pdf_missing_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "edition.pdf"
            path.write_bytes(b"not a PDF")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(MagazineManifestError, "not a PDF"):
                verify_source_pdf_bytes(path, digest)
            with self.assertRaises(MagazineManifestError):
                verify_source_pdf_bytes(Path(root) / "missing.pdf", digest)
            with self.assertRaises(MagazineManifestError):
                verify_source_pdf_bytes(path, "not-a-digest")
            alias = Path(root) / "alias.pdf"
            alias.symlink_to(path)
            with self.assertRaisesRegex(MagazineManifestError, "non-symlink"):
                verify_source_pdf_bytes(alias, digest)

    def test_approval_receipt_requires_dated_timezone_aware_timestamp(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            for invalid in (None, "", "2026-11-02T10:00:00",
                            "not-a-date", "2026-11-04T10:00:00+05:30", 1):
                record = approved()
                if invalid is None:
                    record.pop("approval_timestamp")
                else:
                    record["approval_timestamp"] = invalid
                save(root, "2026-11", record)
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(MagazineManifestError, "approval timestamp"):
                        copy_approved_magazine(bot, 123, month="2026-11",
                                               env=ENV, root=root, now=date(2026, 11, 3))
            self.assertEqual(bot.copies, [])

    def test_approval_offset_cannot_bypass_ist_publication_dates(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            record = approved()
            # Nov 2 in New York is Nov 3 in India: future as of Nov 2 IST.
            record["approval_timestamp"] = "2026-11-02T23:30:00-05:00"
            save(root, "2026-11", record)
            with self.assertRaisesRegex(MagazineManifestError, "in the future"):
                copy_approved_magazine(bot, 123, month="2026-11",
                                       env=ENV, root=root, now=date(2026, 11, 2))
            self.assertEqual(bot.copies, [])
            # The same timestamp becomes valid once the IST calendar reaches Nov 3.
            self.assertEqual(copy_approved_magazine(
                bot, 123, month="2026-11", env=ENV, root=root,
                now=date(2026, 11, 3)), "sent")
            self.assertEqual(len(bot.copies), 1)

    def test_approval_cannot_predate_publication(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as root:
            record = approved()
            record["approval_timestamp"] = "2026-11-01T10:00:00+05:30"
            save(root, "2026-11", record)
            with self.assertRaisesRegex(MagazineManifestError, "predates publication"):
                copy_approved_magazine(bot, 123, month="2026-11", env=ENV,
                                       root=root, now=date(2026, 11, 3))
            self.assertEqual(bot.copies, [])

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
            self.assertNotIn("Verified editorial approval", bot.copies[0][1]["caption"])

    def test_telegram_copy_without_message_id_is_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            save(root, "2026-11", approved())
            for invalid in (None, 0, -1, "123", True):
                class MissingReceiptBot(FakeBot):
                    def copy_message(self, *args, **kwargs):
                        super().copy_message(*args, **kwargs)
                        if invalid is None:
                            return None
                        return type("Receipt", (), {"message_id": invalid})()
                bot = MissingReceiptBot()
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(MagazineManifestError, "delivery message id"):
                        copy_approved_magazine(
                            bot, 123, month="2026-11", env=ENV,
                            root=root, now=date(2026, 11, 3))
                    self.assertEqual(len(bot.copies), 1)

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
                 "published_at": "2026-10-05", "as_of": "2026-10-02",
                 "approval_timestamp": "2026-10-05T10:00:00+05:30"})
            save(root, "2026-11", {**approved(), "approved": False})
            self.assertEqual(copy_approved_magazine(
                bot, 123, env=ENV, root=root, now=date(2026,11,3)), "sent")
            self.assertIn("2026-10", bot.copies[0][1]["caption"])

if __name__ == "__main__":
    unittest.main()
