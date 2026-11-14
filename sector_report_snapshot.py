"""Real NSE completed-session Sector PDF snapshot. Separate research Neon; read-only bot path."""
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger("msk_sector_snapshot")
MAX_BYTES = 15_000_000
MIN_BYTES = 10_000
TABLE = "public.msk_sector_report_snapshot_v1"


def _dsn():
    url = os.getenv("MSK_INDEX_DATABASE_URL", "")
    if url.startswith(("postgres://", "postgresql://")) and "sslmode=require" in url:
        return url
    return None


def _expected_session():
    from sector_report import _last_completed_session
    return _last_completed_session()


def _check_file(path, *, session):
    from pypdf import PdfReader
    path = Path(path)
    size = path.stat().st_size
    if not MIN_BYTES <= size <= MAX_BYTES:
        raise ValueError("Sector report PDF size outside verified range")
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("Sector report has invalid PDF header")
    reader = PdfReader(str(path), strict=True)
    if len(reader.pages) < 3:
        raise ValueError("Sector report has missing analysis pages")
    cover = reader.pages[0].extract_text() or ""
    label = session.strftime("%d-%b-%Y")
    if label not in cover:
        raise ValueError("Sector PDF cover does not match completed NSE session")
    if "Sectoral Update" not in cover:
        raise ValueError("Sector analysis missing from PDF cover")
    return len(reader.pages), size


def _check_metadata(report):
    expected = _expected_session()
    if datetime.strptime(report["asof"], "%d-%b-%Y").date() != expected:
        raise ValueError("Sector analysis is stale; no snapshot released")
    covered, total = report.get("index_coverage", (0, 0))
    if total < 8 or covered < 8 or covered / total < .5:
        raise ValueError("Sector index comparison has insufficient coverage")
    unique = report.get("n_unique", report.get("n_stocks", 0))
    configured = report.get("n_configured", unique)
    if configured < 80 or unique < 80 or unique / configured < .6:
        raise ValueError("Sector stock breadth has insufficient coverage")
    return expected


def save_verified_sector_report(report, path, summary):
    """Persist only actual validated sector report, never blank synthetic output."""
    session = _check_metadata(report)
    if not isinstance(summary, list) or not summary or not all(
            isinstance(line, str) and line.strip() for line in summary):
        raise ValueError("Sector report summary missing")
    pages, byte_count = _check_file(path, session=session)
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("Independent research Neon DSN unavailable")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=8) as conn:
        conn.execute("SET LOCAL statement_timeout = '15000ms'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.msk_sector_report_snapshot_v1 ("
            "session_date DATE PRIMARY KEY, pdf_data BYTEA NOT NULL,"
            "sha256 CHAR(64) NOT NULL, pages INTEGER NOT NULL,"
            "byte_count INTEGER NOT NULL, summary_json JSONB NOT NULL,"
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        conn.execute(
            "INSERT INTO public.msk_sector_report_snapshot_v1 "
            "(session_date,pdf_data,sha256,pages,byte_count,summary_json) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb) "
            "ON CONFLICT(session_date) DO UPDATE SET pdf_data=EXCLUDED.pdf_data,"
            "sha256=EXCLUDED.sha256,pages=EXCLUDED.pages,"
            "byte_count=EXCLUDED.byte_count,summary_json=EXCLUDED.summary_json,"
            "created_at=NOW()",
            (session, data, digest, pages, byte_count,
             json.dumps(summary, ensure_ascii=False)))
    LOG.info("MSK_SECTOR_SNAPSHOT_SAVED session=%s pages=%d bytes=%d digest=%s",
             session, pages, byte_count, digest[:12])
    return digest


def load_verified_sector_report(*, output_dir=None):
    """Bounded readback; missing, stale or corrupt record falls back to normal generator."""
    dsn = _dsn()
    if not dsn:
        return None
    expected = _expected_session()
    path = None
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            row = conn.execute(
                "SELECT pdf_data,sha256,pages,byte_count,summary_json "
                "FROM public.msk_sector_report_snapshot_v1 WHERE session_date=%s",
                (expected,)).fetchone()
        if not row:
            return None
        raw, digest, pages, size, summary = row
        raw = bytes(raw)
        if (not MIN_BYTES <= len(raw) <= MAX_BYTES or len(raw) != int(size)
                or hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError("Stored Sector PDF integrity mismatch")
        if isinstance(summary, str):
            summary = json.loads(summary)
        if not isinstance(summary, list) or not summary or not all(
                isinstance(line, str) and line.strip() for line in summary):
            raise ValueError("Stored Sector summary unavailable")
        from report_generator import OUTPUT_DIR
        destination = Path(output_dir or OUTPUT_DIR)
        destination.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="Index_Sectoral_Update_",
                                     suffix=".pdf", dir=destination)
        path = Path(name)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        observed_pages, observed_size = _check_file(path, session=expected)
        if observed_pages != int(pages) or observed_size != int(size):
            raise ValueError("Stored Sector PDF page count changed")
        LOG.info("MSK_SECTOR_SNAPSHOT_HIT session=%s pages=%d bytes=%d",
                 expected, pages, size)
        return str(path), summary
    except Exception as exc:
        LOG.warning("MSK_SECTOR_SNAPSHOT_UNAVAILABLE type=%s", type(exc).__name__)
        if path is not None:
            path.unlink(missing_ok=True)
        return None


def main():
    """Use a scheduled research worker; this action never sends Telegram messages."""
    from sector_report import collect, assemble, build_pdf, summary_lines
    from report_generator import OUTPUT_DIR
    target = Path(OUTPUT_DIR)
    target.mkdir(parents=True, exist_ok=True)
    hist = collect()
    report = assemble(hist)
    _check_metadata(report)  # No PDF based on stale or partial market evidence.
    path = target / ("Index_Sectoral_Update_" + report["asof"].replace("-", "_") + ".pdf")
    build_pdf(report, str(path))
    summary = summary_lines(report)
    digest = save_verified_sector_report(report, path, summary)
    restored = load_verified_sector_report(output_dir=str(target))
    if not restored:
        raise RuntimeError("No verifiable research Neon PDF readback")
    print("SECTOR_REPORT_SNAPSHOT_READY session=%s sha256=%s" %
          (report["asof"], digest[:12]))


if __name__ == "__main__":
    main()
