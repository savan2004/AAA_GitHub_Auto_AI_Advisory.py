"""Offline private magazine archive gates (no real Drive or Telegram)."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import msk_magazine_drive as archive


class MagazineDriveTests(unittest.TestCase):
    def test_default_off_does_not_read_file_or_request_token(self):
        with patch.dict(os.environ, {"MSK_MAGAZINE_DRIVE_ENABLED":"0"}), \
             patch.object(archive.requests,"post") as post:
            self.assertFalse(archive.archive(Path("missing"),"2026-11","123"))
            post.assert_not_called()

    def test_only_matching_private_draft_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = b"%PDF-" + b"x"*30000
            path = root/"MSK_2026-11_EDITORIAL_DRAFT.pdf"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            gate = {"issue":"2026-11","pdf_sha256":digest,"editorial_approved":False,
                    "publication_allowed":False,"page_count":16,
                    "mechanical_pdf_checks":{"passed":True}}
            manifest = {"issue":"2026-11","run_id":"123","pdf_sha256":digest,
                        "editorial_approved":False,"publication_allowed":False}
            (root/"release_gate.json").write_text(json.dumps(gate))
            (root/"run_manifest.json").write_text(json.dumps(manifest))
            with patch.object(archive,"audit_pdf",return_value={"passed":True}):
                data, props = archive.verified_payload(root,"2026-11","123")
                self.assertEqual(data,raw)
                self.assertEqual(props["status"],"UNAPPROVED_DRAFT")
                with self.assertRaises(ValueError):
                    archive.verified_payload(root,"2026-11","124")
                gate["publication_allowed"]=True
                (root/"release_gate.json").write_text(json.dumps(gate))
                with self.assertRaises(ValueError):
                    archive.verified_payload(root,"2026-11","123")

if __name__ == "__main__":
    unittest.main()
