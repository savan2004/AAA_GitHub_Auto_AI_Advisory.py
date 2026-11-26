"""One verified public-company research PDF snapshot for the Telegram Report button.

Uses the separate MSK index-research Neon database, NOT portfolio storage.
Never generates picks, changes Telegram settings, or publishes synthetic data.
A cache miss fails open to the existing on-demand generator, never to stale data.
"""
import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime
import math

LOG = logging.getLogger("msk_report_snapshot")
MAX_BYTES = 8_000_000
REPORT_TABLE = "public.msk_research_pdf_snapshot_v1"
LOCAL_TTL_SECONDS = 24 * 60 * 60
LOCAL_MAX_BYTES = 32_000_000
LOCAL_MAX_ENTRIES = 32
_LOCAL_LOCK = threading.Lock()
_LOCAL = OrderedDict()
_LOCAL_DIR = None


def _drop_local(key):
    entry = _LOCAL.pop(key, None)
    if entry is not None:
        entry[0].unlink(missing_ok=True)


def _prune_local(now, session):
    for key, entry in list(_LOCAL.items()):
        if entry[1] <= now or key[1] != session:
            _drop_local(key)


def remember_local_report(sym, path, price_session, quality):
    """Keep only an already release-checked, dated public-company PDF.

    Private ephemeral disk, no Neon writes, bounded storage and TTL.
    A read never extends expiration. Failure to cache is non-fatal.
    """
    global _LOCAL_DIR
    new_path = None
    try:
        sym = _symbol(sym)
        session = datetime.strptime(price_session, "%d-%b-%Y").date().isoformat()
        if session != _current_session() or quality["pages"] < 8:
            return
        size = int(quality["bytes"])
        if not 25_000 <= size <= MAX_BYTES:
            return
        raw = Path(path).read_bytes()
        if len(raw) != size or not raw.startswith(b"%PDF-"):
            return
        with _LOCAL_LOCK:
            now = time.monotonic()
            _prune_local(now, session)
            key = (sym, session)
            # Keep the first still-valid report, without extending its TTL.
            if key in _LOCAL:
                return
            while _LOCAL and (len(_LOCAL) >= LOCAL_MAX_ENTRIES or
                    sum(e[2] for e in _LOCAL.values()) + size > LOCAL_MAX_BYTES):
                _drop_local(next(iter(_LOCAL)))
            if size > LOCAL_MAX_BYTES:
                return
            if _LOCAL_DIR is None:
                _LOCAL_DIR = tempfile.TemporaryDirectory(prefix="msk-report-cache-")
            fd, filename = tempfile.mkstemp(suffix=".pdf", dir=_LOCAL_DIR.name)
            new_path = Path(filename)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            _LOCAL[key] = (new_path, now + LOCAL_TTL_SECONDS, size,
                           hashlib.sha256(raw).hexdigest())
            new_path = None
    except Exception as exc:
        LOG.warning("MSK_LOCAL_REPORT_CACHE_WRITE_FAILED type=%s", type(exc).__name__)
    finally:
        if new_path is not None:
            try:
                new_path.unlink(missing_ok=True)
            except OSError:
                pass


def load_recent_local_report(sym, *, output_dir="output/reports"):
    """Return an independent disposable copy, only in the same EOD session."""
    copy = None
    try:
        sym = _symbol(sym)
        session = _current_session()
        with _LOCAL_LOCK:
            _prune_local(time.monotonic(), session)
            key = (sym, session)
            entry = _LOCAL.get(key)
            if entry is None:
                return None
            path, expires, size, digest = entry
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                _drop_local(key)
                return None
            _LOCAL.move_to_end(key)
        if session != _current_session():
            return None
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=f"{sym}_{session}_",
                                        suffix=".pdf", dir=target)
        copy = Path(filename)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        # The Telegram worker independently rechecks the full release gate.
        return str(copy), session
    except Exception as exc:
        LOG.warning("MSK_LOCAL_REPORT_CACHE_READ_FAILED type=%s", type(exc).__name__)
        if copy is not None:
            try:
                copy.unlink(missing_ok=True)
            except OSError:
                pass
        return None


def report_quote_line(quote):
    """Format only a quote already accepted by get_verified_quote."""
    unavailable = "Latest verified price unavailable. See dated PDF prices."
    if not isinstance(quote, dict):
        return unavailable
    try:
        price = float(quote["price"])
        source = quote["quote_source"]
        stamp = datetime.fromisoformat(quote["quote_asof_ist"])
        status = quote["quote_status"]
        if (not math.isfinite(price) or price <= 0 or stamp.utcoffset() is None
                or source not in ("NSE India", "Yahoo Finance v8")
                or status not in ("recent_observation", "last_observed_session")):
            return unavailable
        from datetime import timezone, timedelta
        stamp = stamp.astimezone(timezone(timedelta(hours=5, minutes=30)))
        label = ("Recent market observation" if status == "recent_observation"
                 else "Last observed session")
        return f"{label}: ₹{price:,.2f} | {source} | {stamp:%d-%b %H:%M} IST"
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable


def _dsn():
    value = os.getenv("MSK_INDEX_DATABASE_URL", "")
    if value.startswith(("postgres://", "postgresql://")) and "sslmode=require" in value:
        return value
    return None


def _symbol(value):
    sym = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", sym):
        raise ValueError("Invalid research ticker")
    return sym


def _current_session():
    from market_breadth import expected_completed_session
    return str(expected_completed_session())


def _verified(path, sym):
    from pdf_release_gate import validate_research_pdf
    result = validate_research_pdf(path, expected_symbol=sym, min_pages=8)
    size = result["bytes"]
    if not 25_000 <= size <= MAX_BYTES:
        raise ValueError("Research PDF exceeds bounded database size")
    # This gate checks layout and disclosures, NOT issuer accounting validity.
    return result


def save_verified_report(sym, path, session=None):
    """Atomic write only after a REAL issuer report passes the release gate."""
    sym = _symbol(sym)
    session = session or _current_session()
    path = Path(path)
    quality = _verified(path, sym)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("Dedicated research Neon database unavailable")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=8) as conn:
        conn.execute("SET LOCAL statement_timeout = '15000ms'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.msk_research_pdf_snapshot_v1 ("
            "symbol TEXT PRIMARY KEY, session_date DATE NOT NULL,"
            "pdf_data BYTEA NOT NULL, sha256 CHAR(64) NOT NULL,"
            "pages SMALLINT NOT NULL, byte_count INTEGER NOT NULL,"
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        conn.execute(
            "INSERT INTO public.msk_research_pdf_snapshot_v1 "
            "(symbol,session_date,pdf_data,sha256,pages,byte_count) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE "
            "SET session_date=EXCLUDED.session_date,pdf_data=EXCLUDED.pdf_data,"
            "sha256=EXCLUDED.sha256,pages=EXCLUDED.pages,"
            "byte_count=EXCLUDED.byte_count,created_at=NOW()",
            (sym, session, raw, digest, quality["pages"], quality["bytes"])
        )
    LOG.info("MSK_RESEARCH_PDF_SAVED symbol=%s session=%s pages=%d bytes=%d",
             sym, session, quality["pages"], quality["bytes"])
    return digest


def load_verified_report(sym, *, output_dir="output/reports"):
    """Return (temp_pdf_path, session) only for verified completed-session data.

    No stale report can be returned, even if the DB retained yesterday's row.
    """
    sym = _symbol(sym)
    dsn = _dsn()
    if not dsn:
        return None
    session = _current_session()
    path = None
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            row = conn.execute(
                "SELECT pdf_data,sha256,pages,byte_count "
                "FROM public.msk_research_pdf_snapshot_v1 "
                "WHERE symbol=%s AND session_date=%s "
                "AND created_at >= NOW() - INTERVAL '24 hours' AND created_at <= NOW()",
                (sym, session)
            ).fetchone()
        if row is None:
            return None
        raw, expected_sha, pages, byte_count = bytes(row[0]), row[1], int(row[2]), int(row[3])
        if (not 25_000 <= byte_count <= MAX_BYTES or len(raw) != byte_count or
                pages < 8 or hashlib.sha256(raw).hexdigest() != expected_sha or
                not raw.startswith(b"%PDF-")):
            raise ValueError("Stored research PDF data integrity failed")
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=f"{sym}_{session}_",
                                        suffix=".pdf", dir=target)
        path = Path(filename)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        verified = _verified(path, sym)
        if verified["pages"] != pages or verified["bytes"] != byte_count:
            raise ValueError("Report pages or size changed after read-back")
        LOG.info("MSK_RESEARCH_PDF_CACHE_READY symbol=%s session=%s pages=%d bytes=%d",
                 sym, session, pages, byte_count)
        return str(path), session
    except Exception as exc:
        LOG.warning("MSK_RESEARCH_PDF_CACHE_UNAVAILABLE type=%s", type(exc).__name__)
        if path:
            path.unlink(missing_ok=True)
        return None
