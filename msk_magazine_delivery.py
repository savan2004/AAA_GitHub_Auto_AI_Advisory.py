"""MSK-only, opt-in delivery of approved AiAutoUpdate magazine PDFs via Telegram.

Does not contact AiAutoUpdate, fetch its DB, start jobs, or create content.
Only copies a previously verified Telegram PDF identified in an approved
MSK-side release manifest. Off by default, private-chat command only.
"""
from datetime import date, datetime
import json
import os
from pathlib import Path
import re

DEFAULT_ROOT = Path(__file__).resolve().parent / "data" / "msk_magazines"
MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
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
    now = date.today() if now is None else now
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
    # Source content itself must be independently checked; a valid manifest
    # alone cannot prove the copied Telegram attachment is the expected PDF.
    return data

def _read(root, month, source_chat, *, now=None):
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise MagazineManifestError("Issue must use YYYY-MM")
    path = Path(root) / (month + ".json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise MagazineManifestError("Unreadable magazine release manifest") from exc
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

def copy_approved_magazine(bot, chat_id, *, month=None, env=None, root=None, now=None):
    """Copy approved source channel message to a private-chat user on demand.

    Caller verifies Telegram chat is private and applies existing bot rate limit.
    Bot's channel membership/permissions and actual PDF must be checked at release.
    """
    env = os.environ if env is None else env
    if not enabled(env):
        return "disabled"
    info = find_magazine(month, env=env, root=root, now=now)
    if info is None:
        return "unavailable"
    caption = ("📘 MSK Monthly Market Outlook — " + info["issue"] +
               "\nVerified editorial approval recorded. Educational research only.")
    bot.copy_message(chat_id, info["source_chat"], info["source_message_id"],
                     caption=caption)
    return "sent"
