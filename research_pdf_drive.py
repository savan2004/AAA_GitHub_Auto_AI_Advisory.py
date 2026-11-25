"""Private MSK report archive; disabled unless explicitly configured.

One writer process only. No Telegram sends, Neon writes or automatic retries
of ambiguous uploads/deletes. Only tagged PDFs in the pinned folder are managed.
"""
import hashlib
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)
FOLDER = "1ue3u-6-CCpromy4hHX24g_m0uHWDkY6x"
OWNER = "aiautofintech@gmail.com"
TAG = "msk-research-v1"
LIMIT = 500
MAX_BYTES = 8_000_000
API = "https://www.googleapis.com/drive/v3"
FIELDS = "id,name,mimeType,parents,trashed,size,md5Checksum,createdTime,appProperties"
_LOCK = threading.Lock()


def enabled():
    return (os.getenv("MSK_RESEARCH_DRIVE_ENABLED") == "1"
            and os.getenv("MSK_RESEARCH_DRIVE_FOLDER_ID") == FOLDER)


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("Undated report")
    return dt


def _valid_symbol(value):
    if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", value):
        raise ValueError("Invalid symbol")
    return value


def _session():
    from market_breadth import expected_completed_session
    return str(expected_completed_session())


def _managed(item):
    p = item.get("appProperties") or {}
    return (bool(item.get("id")) and item.get("parents") == [FOLDER]
            and item.get("mimeType") == "application/pdf"
            and item.get("trashed") is False and p.get("archive") == TAG)


def _order(item):
    return (_stamp(item["appProperties"]["generatedAt"]), item["id"])


def _fresh(item, symbol, session, now):
    if not _managed(item):
        return False
    p = item["appProperties"]
    try:
        created = _stamp(p["generatedAt"])
        return (p.get("symbol") == symbol and p.get("session") == session
                and timedelta(0) <= now - created < timedelta(hours=24)
                and 25_000 <= int(item["size"]) <= MAX_BYTES)
    except (ValueError, KeyError, TypeError):
        return False


def _verify_bytes(item, raw):
    p = item["appProperties"]
    if (not 25_000 <= len(raw) <= MAX_BYTES or not raw.startswith(b"%PDF-")
            or len(raw) != int(item["size"])
            or hashlib.sha256(raw).hexdigest() != p["sha256"]
            or hashlib.md5(raw).hexdigest() != item["md5Checksum"]):
        raise ValueError("Cloud PDF integrity mismatch")


class Drive:
    def __init__(self):
        secret = {k: os.environ["MSK_DRIVE_" + k.upper()]
                  for k in ("client_id", "client_secret", "refresh_token")}
        response = requests.post("https://oauth2.googleapis.com/token",
                                 data={**secret, "grant_type": "refresh_token"},
                                 timeout=(4, 10))
        response.raise_for_status()
        self.http = requests.Session()
        self.http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        try:
            folder = self._json("GET", API + "/files/" + FOLDER,
                                params={"fields": "id,mimeType,trashed,shared,owners(emailAddress)"})
            if (folder.get("id") != FOLDER or folder.get("trashed") is not False
                    or folder.get("shared") is not False
                    or folder.get("mimeType") != "application/vnd.google-apps.folder"
                    or [o.get("emailAddress") for o in folder.get("owners", [])] != [OWNER]):
                raise ValueError("Archive folder identity not verified")
        except Exception:
            self.http.close()
            raise

    def close(self):
        self.http.close()

    def _json(self, method, url, **kw):
        r = self.http.request(method, url, timeout=(4, 15), **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    def list(self):
        result, token, seen = [], None, set()
        for _ in range(20):
            params = {"q": f"'{FOLDER}' in parents and trashed = false and "
                           f"mimeType = 'application/pdf' and appProperties has "
                           f"{{ key='archive' and value='{TAG}' }}",
                      "fields": f"nextPageToken,incompleteSearch,files({FIELDS})",
                      "pageSize": 1000}
            if token:
                params["pageToken"] = token
            data = self._json("GET", API + "/files", params=params)
            if data.get("incompleteSearch") or any(not _managed(x) for x in data.get("files", [])):
                raise ValueError("Incomplete or unsafe archive listing")
            result.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                if len({x["id"] for x in result}) != len(result):
                    raise ValueError("Duplicate archive listing")
                return result
            if token in seen:
                break
            seen.add(token)
        raise ValueError("Archive pagination incomplete")

    def upload(self, metadata, raw):
        # No retries: an uncertain POST must be reconciled by a subsequent list.
        import uuid
        boundary = uuid.uuid4().hex
        import json
        body = (("--" + boundary + "\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                 + json.dumps(metadata) + "\r\n--" + boundary
                 + "\r\nContent-Type: application/pdf\r\n\r\n").encode()
                + raw + ("\r\n--" + boundary + "--\r\n").encode())
        return self._json("POST", "https://www.googleapis.com/upload/drive/v3/files",
                          params={"uploadType": "multipart", "fields": FIELDS},
                          headers={"Content-Type": "multipart/related; boundary=" + boundary},
                          data=body)

    def get(self, file_id):
        return self._json("GET", API + "/files/" + file_id, params={"fields": FIELDS})

    def download(self, item):
        with self.http.get(API + "/files/" + item["id"], params={"alt": "media"},
                           timeout=(4, 15), stream=True) as r:
            r.raise_for_status()
            buf = bytearray()
            for block in r.iter_content(65536):
                buf.extend(block)
                if len(buf) > MAX_BYTES:
                    raise ValueError("Oversize PDF")
        raw = bytes(buf)
        _verify_bytes(item, raw)
        return raw

    def delete(self, item):
        # Recheck immediately; never remove unrelated, moved or untagged files.
        actual = self.get(item["id"])
        if not _managed(actual) or actual["appProperties"] != item["appProperties"]:
            raise ValueError("FIFO candidate changed")
        r = self.http.delete(API + "/files/" + item["id"], timeout=(4, 15))
        r.raise_for_status()


def _write_pdf(raw, symbol, output_dir):
    from pdf_release_gate import validate_research_pdf
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{symbol}_cloud_", suffix=".pdf", dir=target)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        validate_research_pdf(name, expected_symbol=symbol, min_pages=8)
        return name
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def load_cloud_report(symbol, *, output_dir="output/reports"):
    if not enabled():
        return None
    client = None
    try:
        symbol = _valid_symbol(symbol)
        session, now = _session(), _now()
        with _LOCK:
            client = Drive()
            matches = [x for x in client.list() if _fresh(x, symbol, session, now)]
            if not matches:
                return None
            item = max(matches, key=_order)
            raw = client.download(item)
            if not _fresh(item, symbol, _session(), _now()):
                return None
            return _write_pdf(raw, symbol, output_dir), session
    except Exception as exc:
        LOG.warning("MSK_DRIVE_READ_FAILED type=%s", type(exc).__name__)
        return None
    finally:
        if client:
            client.close()


def store_cloud_report(symbol, path, price_session):
    """Upload after release validation; evict oldest only after read-back proof."""
    if not enabled():
        return False
    client = None
    try:
        symbol = _valid_symbol(symbol)
        session = datetime.strptime(price_session, "%d-%b-%Y").date().isoformat()
        if session != _session():
            return False
        from pdf_release_gate import validate_research_pdf
        quality = validate_research_pdf(path, expected_symbol=symbol, min_pages=8)
        if not 25_000 <= quality["bytes"] <= MAX_BYTES:
            return False
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != quality["bytes"]:
            return False
        with _LOCK:
            client = Drive()
            before = client.list()
            # Validate FIFO timestamps before any mutation. If a previous cleanup
            # failed, stop further uploads instead of growing unboundedly.
            ordered = sorted(before, key=_order)
            if len(before) > LIMIT:
                raise ValueError("Archive over limit; reconciliation required")
            now = _now()
            for item in before:
                if _fresh(item, symbol, session, now):
                    client.download(item)
                    return True
            if session != _session():
                return False
            props = {"archive": TAG, "symbol": symbol, "session": session,
                     "generatedAt": now.isoformat(), "sha256": digest}
            uploaded = client.upload({"name": f"{symbol}_{session}_{now:%Y%m%dT%H%M%S%fZ}.pdf",
                                      "mimeType": "application/pdf", "parents": [FOLDER],
                                      "appProperties": props}, raw)
            checked = client.get(uploaded["id"])
            if not _managed(checked) or checked.get("appProperties") != props:
                raise ValueError("Upload metadata not verified")
            if client.download(checked) != raw:
                raise ValueError("Upload bytes not verified")
            after = client.list()
            if ({x["id"] for x in after} != {x["id"] for x in before} | {checked["id"]}
                    or len(after) != len(before) + 1):
                raise ValueError("Concurrent archive mutation; no deletion")
            if len(after) > LIMIT:
                victim = ordered[0]
                client.delete(victim)
                remaining = client.list()
                if {x["id"] for x in remaining} != {x["id"] for x in after} - {victim["id"]}:
                    raise ValueError("FIFO deletion unconfirmed")
            LOG.info("MSK_DRIVE_ARCHIVED symbol=%s session=%s", symbol, session)
            return True
    except Exception as exc:
        LOG.warning("MSK_DRIVE_ARCHIVE_FAILED type=%s", type(exc).__name__)
        return False
    finally:
        if client:
            client.close()
