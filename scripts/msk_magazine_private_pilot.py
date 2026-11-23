"""Single-attempt, private-chat-only Telegram acceptance probe for MSK magazine.

This is NOT the public /magazine feature, a scheduled task, or a production
publication. It can send exactly one dated editorially-approved PDF proof to
the explicitly allowlisted owner private chat. There is no uncertain retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from pypdf import PdfReader
import requests

APPROVED_ISSUE = "2026-11"
# SHA256 of the exact editorial draft approved by the owner in chat on
# 24 Nov 2026, not a later rebuild: PR #265, run 45238995335.
APPROVED_ARTIFACT_SHA256 = "025254c56ae585a588c43dbb74a3c7c14c6592b54e3a624f62c92c35a39cbbc9"
API_ROOT = "https://api.telegram.org"
MAX_BYTES = 15_000_000


class PrivatePilotError(ValueError):
    pass


def preflight(issue_dir: Path) -> tuple[Path, str]:
    root = Path(issue_dir)
    name = f"MSK_{APPROVED_ISSUE}_EDITORIAL_DRAFT.pdf"
    pdf = root / name
    ledger = root / "release_gate.json"
    if any(p.is_symlink() for p in (root, pdf, ledger)):
        raise PrivatePilotError("Symlinked source or ledger rejected")
    if not pdf.is_file() or not ledger.is_file():
        raise PrivatePilotError("Missing original release PDF and release gate")
    if not 10_000 <= pdf.stat().st_size <= MAX_BYTES:
        raise PrivatePilotError("Unsafe PDF size")
    gate = json.loads(ledger.read_text(encoding="utf-8"))
    if (gate.get("issue") != APPROVED_ISSUE or gate.get("page_count") != 16
            or gate.get("editorial_pages_authored") != 14
            or gate.get("chart_pages_with_dated_evidence") != 14
            or gate.get("nse_reconciled_named_rows") != 14
            or gate.get("nse_original_pdf_reconciled") is not True
            or gate.get("macro_issuer_source_files_rechecked") != 7
            or gate.get("macro_issuer_original_sources_verified") is not True
            or gate.get("mechanical_pdf_checks", {}).get("passed") is not True):
        raise PrivatePilotError("Final technical source or visual acceptance gates missing")
    if gate.get("publication_allowed") is not False:
        raise PrivatePilotError("Private pilot only accepts non-public PDF proof")
    checksum = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if checksum != APPROVED_ARTIFACT_SHA256 or gate.get("pdf_sha256") != checksum:
        raise PrivatePilotError("PDF differs from the owner-approved proof")
    reader = PdfReader(str(pdf), strict=True)
    if len(reader.pages) != 16 or not reader.pages[0].images:
        raise PrivatePilotError("PDF A4 issue structure/logo missing")
    if any("UNAPPROVED EDITORIAL DRAFT" not in (p.extract_text() or "") for p in reader.pages):
        raise PrivatePilotError("Private test requires obvious DRAFT watermark")
    return pdf, checksum


def _api(session, token: str, method: str, *, data: dict, files=None):
    try:
        response = session.post(API_ROOT + "/bot" + token + "/" + method,
                                data=data, files=files, timeout=(8, 45))
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        # Never trigger another send when a timeout/failed response is uncertain.
        raise PrivatePilotError(method + ": Telegram result unverified") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PrivatePilotError(method + ": Telegram did not confirm success")
    if not isinstance(payload.get("result"), dict):
        raise PrivatePilotError(method + ": missing Telegram result")
    return payload["result"]


def private_test(issue_dir: Path, receipt_path: Path, *, env=None, session=None):
    env = os.environ if env is None else env
    if env.get("MSK_MAGAZINE_PRIVATE_PILOT") != "I_CONFIRM_OWNER_PRIVATE_CHAT":
        raise PrivatePilotError("Explicit private owner-only pilot confirmation missing")
    token = env.get("MSK_STAGING_TELEGRAM_BOT_TOKEN", "")
    raw_id = env.get("MSK_OWNER_PRIVATE_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]{5,18}", raw_id):
        raise PrivatePilotError("Owner's private Telegram chat ID is not configured")
    if not re.fullmatch(r"[0-9]{5,18}:[A-Za-z0-9_-]{20,}", token):
        raise PrivatePilotError("Dedicated staging bot token not configured")
    chat_id = int(raw_id)
    pdf, checksum = preflight(issue_dir)
    receipt_file = Path(receipt_path)
    if receipt_file.is_symlink():
        raise PrivatePilotError("Symlinked delivery receipt rejected")
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    sess = session or requests.Session()
    chat = _api(sess, token, "getChat", data={"chat_id": chat_id})
    if chat.get("type") != "private" or chat.get("id") != chat_id:
        raise PrivatePilotError("Destination must match the exact allowlisted private chat")

    # Exclusive reserve BEFORE making a non-idempotent send. Even a failed or
    # unverified send reserves this receipt path until reviewed manually.
    record = {"issue": APPROVED_ISSUE, "owner_pdf_sha256": checksum,
              "chat_id": chat_id, "status": "DELIVERY_UNVERIFIED",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "message_id": None, "attempts": 1}
    try:
        with receipt_file.open("x", encoding="utf-8") as out:
            json.dump(record, out, indent=2)
            out.write("\n")
    except FileExistsError:
        raise PrivatePilotError("A delivery attempt already exists: never retry blindly")
    caption = ("PRIVATE OWNER QA ONLY | MSK Monthly Market Outlook 2026-11 "
               "| NOT FOR CUSTOMER DISTRIBUTION")
    try:
        with pdf.open("rb") as f:
            message = _api(sess, token, "sendDocument",
                           data={"chat_id": chat_id, "caption": caption,
                                 "disable_notification": "true"},
                           files={"document": (pdf.name, f, "application/pdf")})
        returned_chat = message.get("chat", {})
        doc = message.get("document", {})
        mid = message.get("message_id")
        if (type(mid) is not int or mid <= 0
                or not isinstance(returned_chat, dict)
                or returned_chat.get("id") != chat_id
                or returned_chat.get("type") != "private"
                or not isinstance(doc, dict) or not doc.get("file_id")):
            raise PrivatePilotError("Telegram response lacks verified message/chat/document")
        record.update({"status": "PRIVATE_DELIVERY_CONFIRMED",
                       "message_id": mid, "telegram_file_id": doc["file_id"]})
    except Exception:
        # Fail closed. Retain reserved receipt; do not retry uncertain sends.
        receipt_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        raise
    receipt_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"status": record["status"], "message_id": record["message_id"],
            "pdf_sha256": checksum, "issue": APPROVED_ISSUE}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-dir", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--send", action="store_true",
                        help="Explicit private owner-only send: never retry on ambiguous failure")
    args = parser.parse_args()
    if not args.send:
        pdf, checksum = preflight(args.issue_dir)
        print(json.dumps({"state": "PRIVATE_PILOT_DRY_RUN_OK", "pdf": pdf.name,
                          "sha256": checksum, "actual_send_attempted": False}))
        return
    print(json.dumps(private_test(args.issue_dir, args.receipt)))


if __name__ == "__main__":
    main()
