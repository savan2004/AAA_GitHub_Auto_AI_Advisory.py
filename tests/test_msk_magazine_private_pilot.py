"""Private pilot safety tests. Every Telegram request is mocked; zero real sends."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import msk_magazine_private_pilot as pilot


ENV = {"MSK_MAGAZINE_PRIVATE_PILOT": "I_CONFIRM_OWNER_PRIVATE_CHAT",
       "MSK_STAGING_TELEGRAM_BOT_TOKEN": "12345678:" + "Z" * 30,
       "MSK_OWNER_PRIVATE_CHAT_ID": "123456789"}


class FakeResponse:
    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": self.result}


class FakeSession:
    def __init__(self, *, chat_type="private", missing_id=False):
        self.calls = []
        self.chat_type = chat_type
        self.missing_id = missing_id

    def post(self, url, *, data, files=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data),
                           "files": files is not None, "timeout": timeout})
        if url.endswith("/getChat"):
            return FakeResponse({"id": 123456789, "type": self.chat_type})
        if url.endswith("/sendDocument"):
            return FakeResponse({
                "message_id": None if self.missing_id else 9123,
                "chat": {"id": 123456789, "type": "private"},
                "document": {"file_id": "telegram_file_abcdefgh"},
            })
        raise AssertionError("Unexpected Telegram endpoint")


class PrivatePilotTests(unittest.TestCase):
    def _sample(self, root):
        proof = Path(root) / "candidate"
        proof.mkdir()
        (proof / "MSK_2026-11_EDITORIAL_DRAFT.pdf").write_bytes(
            b"%PDF-1.4\n" + b"content for safe staged mocked send\n"
        )
        return proof

    def test_missing_explicit_owner_consent_never_calls_telegram(self):
        with TemporaryDirectory() as temp:
            session = FakeSession()
            with self.assertRaisesRegex(pilot.PrivatePilotError, "confirmation"):
                pilot.private_test(Path(temp), Path(temp) / "receipt.json",
                                   env={}, session=session)
            self.assertEqual(session.calls, [])

    def test_no_chat_id_never_calls_telegram(self):
        with TemporaryDirectory() as temp:
            session = FakeSession()
            env = dict(ENV)
            del env["MSK_OWNER_PRIVATE_CHAT_ID"]
            with self.assertRaisesRegex(pilot.PrivatePilotError, "chat ID"):
                pilot.private_test(Path(temp), Path(temp) / "receipt.json",
                                   env=env, session=session)
            self.assertEqual(session.calls, [])

    def test_group_chat_blocked_before_send(self):
        with TemporaryDirectory() as temp, patch.object(
            pilot, "preflight", return_value=(self._sample(temp) /
                "MSK_2026-11_EDITORIAL_DRAFT.pdf", pilot.APPROVED_ARTIFACT_SHA256)
        ):
            session = FakeSession(chat_type="supergroup")
            with self.assertRaisesRegex(pilot.PrivatePilotError, "private"):
                pilot.private_test(Path(temp), Path(temp) / "receipt.json",
                                   env=ENV, session=session)
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(session.calls[0]["url"].endswith("/getChat"))

    def test_one_verified_private_send_records_real_message_id(self):
        with TemporaryDirectory() as temp:
            pdf = self._sample(temp) / "MSK_2026-11_EDITORIAL_DRAFT.pdf"
            with patch.object(pilot, "preflight",
                              return_value=(pdf, pilot.APPROVED_ARTIFACT_SHA256)):
                session = FakeSession()
                receipt = Path(temp) / "test-receipt.json"
                result = pilot.private_test(Path(temp), receipt,
                                            env=ENV, session=session)
                self.assertEqual(result["message_id"], 9123)
                self.assertEqual(result["status"], "PRIVATE_DELIVERY_CONFIRMED")
                self.assertEqual(len(session.calls), 2)
                self.assertTrue(session.calls[1]["url"].endswith("/sendDocument"))
                record = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(record["attempts"], 1)
                self.assertEqual(record["message_id"], 9123)
                with self.assertRaisesRegex(pilot.PrivatePilotError, "never retry"):
                    pilot.private_test(Path(temp), receipt, env=ENV, session=session)
                self.assertEqual(len(session.calls), 3)  # only a getChat, never another sendDocument
                self.assertEqual(sum(x["files"] for x in session.calls), 1)

    def test_ambiguous_telegram_send_no_retry(self):
        with TemporaryDirectory() as temp:
            pdf = self._sample(temp) / "MSK_2026-11_EDITORIAL_DRAFT.pdf"
            with patch.object(pilot, "preflight",
                              return_value=(pdf, pilot.APPROVED_ARTIFACT_SHA256)):
                session = FakeSession(missing_id=True)
                receipt = Path(temp) / "attempt.json"
                with self.assertRaisesRegex(pilot.PrivatePilotError, "message/chat/document"):
                    pilot.private_test(Path(temp), receipt, env=ENV, session=session)
                recorded = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(recorded["status"], "DELIVERY_UNVERIFIED")
                self.assertEqual(recorded["attempts"], 1)
                self.assertEqual(sum(x["files"] for x in session.calls), 1)

    def test_private_probe_is_pinned_to_approved_issue_and_digest(self):
        self.assertEqual(pilot.APPROVED_ISSUE, "2026-11")
        self.assertEqual(len(pilot.APPROVED_ARTIFACT_SHA256), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in pilot.APPROVED_ARTIFACT_SHA256))


if __name__ == "__main__":
    unittest.main()
