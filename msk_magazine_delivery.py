"""MSK-only, opt-in delivery of approved AiAutoUpdate magazine PDFs via Telegram.

Does not contact AiAutoUpdate, fetch its DB, start jobs, or create content.
Only copies a previously verified Telegram PDF identified in an approved
MSK-side release manifest. Off by default, private-chat command only.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re

DEFAULT_ROOT = Path(__file__).resolve().parent / "data" / "msk_magazines"
MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
IST = timezone(timedelta(hours=5, minutes=30))
CHAT = re.compile(r"^(?:-100\d{6,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")

class MagazineConfigurationError(RuntimeError):
    pass

class MagazineManifestError(ValueError):
    pass

def enabled(env=None):
    env = os.environ if env is None else env
    return env.get("MSK_MAGAZINE_ENABLED", "").strip().lower() == "true"

def _source_chat(env):
    source = env.get("MSK_MAGAZINE_SOURCE_CHAT", "").strip()
    if not CHAT.fullmatch(source):
        raise MagazineConfigurationError("MSK magazine source channel is not configured")
    return source

def _check_manifest(data, month, source_chat, *, now=None):
    now = datetime.now(IST).date() if now is None else now
    if not isinstance(data, dict) or data.get("approved") is not True:
        raise MagazineManifestError("Magazine is not approved")
    if data.get("issue") != month or data.get("source_project") != "AiAutoUpdate":
        raise MagazineManifestError("Magazine issue or source mismatch")
    if data.get("mime_type") != "application/pdf":
        raise MagazineManifestError("Magazine source is not marked as PDF")
    if data.get("source_chat") != source_chat:
        raise MagazineManifestError("Magazine source channel mismatch")
    mid = data.get("source_message_id")
    if type(mid) is not int or not 1 <= mid <= 2**31 - 1:
        raise MagazineManifestError("Invalid source Telegram message id")
    digest = data.get("verified_pdf_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise MagazineManifestError("PDF has no recorded independent checksum")
    approver = data.get("approved_by")
    if not isinstance(approver, str) or not 3 <= len(approver.strip()) <= 100:
        raise MagazineManifestError("Approved issue lacks named reviewer")
    # Approval must be dated, not inferred from an undated boolean/name.
    approval_at = data.get("approval_timestamp")
    if not isinstance(approval_at, str):
        raise MagazineManifestError("Magazine approval timestamp missing")
    try:
        approved_at = datetime.fromisoformat(approval_at)
    except ValueError as exc:
        raise MagazineManifestError("Magazine approval timestamp invalid") from exc
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise MagazineManifestError("Magazine approval timestamp must include timezone")
    # Compare civil dates in the edition's IST timezone, not the sender's offset.
    approved_day_ist = approved_at.astimezone(IST).date()
    if approved_day_ist > now:
        raise MagazineManifestError("Magazine approval timestamp is in the future")
    name = data.get("title")
    if not isinstance(name, str) or not 3 <= len(name.strip()) <= 120:
        raise MagazineManifestError("Magazine title invalid")
    try:
        published = date.fromisoformat(data.get("published_at", ""))
        as_of = date.fromisoformat(data.get("as_of", ""))
    except (TypeError, ValueError) as exc:
        raise MagazineManifestError("Magazine dates invalid") from exc
    if published > now or as_of > published:
        raise MagazineManifestError("Magazine has future or conflicting dates")
    if approved_day_ist < published:
        raise MagazineManifestError("Magazine approval predates publication")
    if published.strftime("%Y-%m") != month:
        raise MagazineManifestError("Magazine publication month does not match issue")
    # Source content itself must be independently checked; a valid manifest
    # alone cannot prove the copied Telegram attachment is the expected PDF.
    return data

def verify_source_pdf_bytes(pdf_path, expected_sha256):
    """Independently hash an actual local PDF before editorial approval.

    A manifest checksum alone is not evidence of Telegram attachment bytes.
    This helper does NOT authorize copying or publishing a Telegram message.
    """
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256):
        raise MagazineManifestError("Invalid expected PDF SHA256")
    path = Path(pdf_path)
    if path.is_symlink() or not path.is_file():
        raise MagazineManifestError("Source PDF must be a regular non-symlink file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            header = source.read(5)
            if header != b"%PDF-":
                raise MagazineManifestError("Source file is not a PDF")
            digest.update(header)
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise MagazineManifestError("Cannot read source PDF") from exc
    if digest.hexdigest() != expected_sha256:
        raise MagazineManifestError("Source PDF SHA256 mismatch")
    return True

def verify_downloaded_source_attachment(document, pdf_bytes, manifest):
    """Validate independently downloaded Telegram document bytes, without publishing.

    Caller must obtain document metadata and bytes from the actual source message.
    A manifest alone or Telegram copy_message receipt cannot prove this match.
    """
    if not isinstance(document, dict) or not isinstance(manifest, dict):
        raise MagazineManifestError("Source attachment metadata missing")
    if document.get("source_chat") != manifest.get("source_chat"):
        raise MagazineManifestError("Source attachment channel mismatch")
    mid = document.get("source_message_id")
    if type(mid) is not int or mid <= 0 or mid != manifest.get("source_message_id"):
        raise MagazineManifestError("Source attachment message id mismatch")
    if document.get("mime_type") != "application/pdf":
        raise MagazineManifestError("Source attachment is not a PDF")
    # Telegram document metadata must report the actual downloaded byte count.
    # A checksum match against manifest bytes alone cannot validate a claimed
    # Telegram attachment if metadata is incomplete or inconsistent.
    size = document.get("file_size")
    if type(size) is not int or size <= 0:
        raise MagazineManifestError("Source attachment file size missing or invalid")
    if not isinstance(pdf_bytes, bytes) or size != len(pdf_bytes):
        raise MagazineManifestError("Source attachment file size mismatch")
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
        raise MagazineManifestError("Downloaded source attachment PDF bytes missing")
    expected = manifest.get("verified_pdf_sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise MagazineManifestError("Source attachment checksum missing")
    if hashlib.sha256(pdf_bytes).hexdigest() != expected:
        raise MagazineManifestError("Downloaded source attachment SHA256 mismatch")
    return True

def _read(root, month, source_chat, *, now=None):
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise MagazineManifestError("Issue must use YYYY-MM")
    root_path = Path(root)
    path = root_path / (month + ".json")
    # Approved release manifests must be regular files inside the configured
    # archive. Never follow a symlink into another directory or secret file.
    if root_path.is_symlink() or path.is_symlink():
        raise MagazineManifestError("Symlinked magazine archive or manifest rejected")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise MagazineManifestError("Unreadable magazine release manifest") from exc
    if not isinstance(data, dict):
        raise MagazineManifestError("Invalid magazine release manifest")
    if data.get("approved") is False:
        return None  # a draft never blocks access to an older approved edition
    return _check_manifest(data, month, source_chat, now=now)

def find_magazine(month=None, *, env=None, root=None, now=None):
    """Return approved release metadata or None. Never silently accept invalid manifests."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    source_chat = _source_chat(env)
    root = DEFAULT_ROOT if root is None else Path(root)
    if month is not None:
        return _read(root, month, source_chat, now=now)
    if not root.is_dir():
        return None
    for path in sorted(root.glob("????-??.json"), reverse=True):
        if not MONTH.fullmatch(path.stem):
            continue
        # A draft is skipped, a corrupt or falsely approved manifest fails closed.
        try:
            value = _read(root, path.stem, source_chat, now=now)
        except MagazineManifestError:
            raise
        if value:
            return value
    return None

def copy_approved_magazine(bot, chat_id, *, month=None, env=None, root=None, now=None,
                           source_attachment_loader=None):
    """Copy approved source channel message to a private-chat user on demand.

    Caller verifies Telegram chat is private and applies existing bot rate limit.
    source_attachment_loader(chat, message_id) must retrieve independently
    authenticated source metadata and bytes, returning (document, pdf_bytes).
    Never build its result from the manifest or a caller-supplied local PDF.
    No provider is configured by default: delivery stays blocked until wired.
    Bot's channel membership/permissions and actual PDF must be checked at release.
    """
    env = os.environ if env is None else env
    if not enabled(env):
        return "disabled"
    info = find_magazine(month, env=env, root=root, now=now)
    if info is None:
        return "unavailable"
    if not callable(source_attachment_loader):
        raise MagazineManifestError("Source attachment verification unavailable")
    try:
        document, pdf_bytes = source_attachment_loader(
            info["source_chat"], info["source_message_id"])
    except Exception:
        # Provider failures can contain authenticated URLs; expose no raw detail.
        raise MagazineManifestError("Source attachment retrieval failed") from None
    verify_downloaded_source_attachment(document, pdf_bytes, info)
    caption = ("📘 MSK Monthly Market Outlook — " + info["issue"] +
               "\nEditorial approval recorded; verify source evidence independently. Educational research only.")
    receipt = bot.copy_message(chat_id, info["source_chat"], info["source_message_id"],
                               caption=caption)
    # A completed API call is not a delivery receipt without a Telegram message id.
    message_id = getattr(receipt, "message_id", None)
    if type(message_id) is not int or message_id <= 0:
        raise MagazineManifestError("Telegram copy returned no valid delivery message id")
    return "sent"
