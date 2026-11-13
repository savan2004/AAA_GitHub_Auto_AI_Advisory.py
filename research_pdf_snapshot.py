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

LOG = logging.getLogger("msk_report_snapshot")
MAX_BYTES = 8_000_000
REPORT_TABLE = "public.msk_research_pdf_snapshot_v1"


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
                "WHERE symbol=%s AND session_date=%s",
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
