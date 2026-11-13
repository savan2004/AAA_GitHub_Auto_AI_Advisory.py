"""Verified daily smart-chart PNG snapshot in the separate MSK research Neon DB.

This module never connects to portfolio storage, changes Telegram settings,
stores customer data or fabricates trade recommendations.
"""
import hashlib
import html
import math
import os
import re
import tempfile
from pathlib import Path

LOG_PREFIX = "MSK_SMART_CHART"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 8 * 1024 * 1024
MIN_PNG_BYTES = 25_000
META = re.compile(
    r"^([A-Z0-9&._-]+\.NS)\|([^|]{2,100})\|"
    r"(WAIT|BUY|STRONG BUY|SELL|STRONG SELL)\|"
    r"([+-]?\d{1,2}/20)\|(\d+(?:\.\d+)?)\|"
    r"(\d+(?:\.\d+)?)\|(\d+(?:\.\d+)?)\|(\d+(?:\.\d+)?)\|"
    r"Sample: (\d+)/(\d+) verified NSE symbols from (\d+); "
    r"not a full-universe ranking; EOD (\d{4}-\d{2}-\d{2})$"
)


def _dsn():
    value = os.environ.get("MSK_INDEX_DATABASE_URL", "")
    if value.startswith(("postgresql://", "postgres://")) and "sslmode=require" in value:
        return value
    return None


def _validate(image, meta, session):
    if not isinstance(image, bytes) or not MIN_PNG_BYTES <= len(image) <= MAX_PNG_BYTES:
        raise ValueError("Chart PNG missing or outside safe size range")
    if not image.startswith(PNG_MAGIC) or image[12:16] != b"IHDR":
        raise ValueError("Not a real PNG image")
    width = int.from_bytes(image[16:20], "big")
    height = int.from_bytes(image[20:24], "big")
    if not (800 <= width <= 8000 and 500 <= height <= 5000):
        raise ValueError("Chart image dimensions invalid")
    match = META.fullmatch(meta or "")
    if not match or match.group(12) != str(session):
        raise ValueError("Chart metadata does not match completed market session")
    valid, sampled, universe = (int(match.group(x)) for x in (9, 10, 11))
    if not (0 < valid <= sampled <= universe <= 550):
        raise ValueError("Chart candidate coverage invalid")
    price = float(match.group(5))
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Chart price invalid")
    return match


def format_chart_meta(meta, session):
    """Preserve familiar Telegram caption without trusting unescaped metadata."""
    # Actual image is validated independently on upload and read-back.
    match = META.fullmatch(meta or "")
    if not match or match.group(12) != str(session):
        raise ValueError("Unverified chart metadata")
    symbol, name, signal, score, ltp, sl, t1, t2 = match.groups()[:8]
    result = (
        f"<b>{html.escape(symbol)} — {html.escape(name)}</b>\n"
        f"Signal: <b>{html.escape(signal)}</b>  Score: {html.escape(score)}\n"
        f"Entry: ₹{html.escape(ltp)}\n"
    )
    if signal == "WAIT":
        result += "⏸ No-Trade Zone — wait for ≥12/20 score"
    elif float(sl) > 0:
        result += (
            f"SL: ₹{html.escape(sl)}  |  "
            f"T1: ₹{html.escape(t1)}  |  T2: ₹{html.escape(t2)}"
        )
    else:
        result += "⚠️ Trade levels unavailable — no actionable signal"
    result += (
        f"\n🔎 Sample: {match.group(9)}/{match.group(10)} verified NSE symbols "
        f"from {match.group(11)}; not a full-universe ranking; EOD {session}"
    )
    return result


def save_verified_daily_chart(session, image, meta):
    """Write one current-session snapshot atomically, then verify with read-back."""
    _validate(image, meta, session)
    dsn = _dsn()
    if dsn is None:
        raise RuntimeError("Independent research database connection not configured")
    import psycopg
    digest = hashlib.sha256(image).hexdigest()
    with psycopg.connect(dsn, connect_timeout=8) as conn:
        conn.execute("SET LOCAL statement_timeout = '12000ms'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.msk_smart_chart_snapshots_v1 ("
            "session_date DATE PRIMARY KEY, image_data BYTEA NOT NULL, "
            "image_sha256 CHAR(64) NOT NULL, meta_text TEXT NOT NULL, "
            "generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        conn.execute(
            "INSERT INTO public.msk_smart_chart_snapshots_v1 "
            "(session_date,image_data,image_sha256,meta_text) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT(session_date) DO UPDATE "
            "SET image_data=EXCLUDED.image_data, "
            "image_sha256=EXCLUDED.image_sha256, "
            "meta_text=EXCLUDED.meta_text, generated_at=NOW()",
            (session, image, digest, meta)
        )
    return digest


def load_verified_daily_chart(session, output_dir="output"):
    """Read only a matching and hash-verified real current-session PNG.

    Returns (caption, local_png_path) or None. No fallback to stale images.
    """
    dsn = _dsn()
    if dsn is None:
        return None
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            row = conn.execute(
                "SELECT image_data,image_sha256,meta_text "
                "FROM public.msk_smart_chart_snapshots_v1 "
                "WHERE session_date=%s", (session,)
            ).fetchone()
        if row is None:
            return None
        image, digest, meta = bytes(row[0]), row[1], row[2]
        _validate(image, meta, session)
        if hashlib.sha256(image).hexdigest() != digest:
            return None
        caption = format_chart_meta(meta, session)
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        file_path = destination / f"chart_verified_{session}.png"
        if not file_path.exists() or hashlib.sha256(file_path.read_bytes()).hexdigest() != digest:
            fd, tmp = tempfile.mkstemp(prefix=".chart-", suffix=".png", dir=destination)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(image)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, file_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return caption, str(file_path)
    except (ValueError, OSError, ImportError, Exception) as exc:
        # Never log DSNs or Telegram content. A missing table is not a PASS.
        import logging
        logging.getLogger(__name__).warning(
            "MSK_SMART_CHART_DB_READ_FAILED type=%s", type(exc).__name__)
        return None
