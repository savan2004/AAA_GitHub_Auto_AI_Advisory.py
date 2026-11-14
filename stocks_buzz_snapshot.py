"""Durable, completed-session Stocks Buzz cache in the MSK research Neon database.

Never use portfolio storage, manufacture stocks, or send Telegram messages.
A miss or corrupt cache returns None; an independently computed real scan is
required to create/refresh the public-market snapshot.
"""
import hashlib
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta

LOG = logging.getLogger("msk_buzz_snapshot")
TABLE = "public.msk_stocks_buzz_session_v1"
MAX_BYTES = 1_000_000
MIN_COVERAGE = 0.80
_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,50}$")


def _dsn():
    value = os.getenv("MSK_INDEX_DATABASE_URL", "")
    if value.startswith(("postgres://", "postgresql://")) and "sslmode=require" in value:
        return value
    return None


def _session():
    from stocks_buzz import _last_completed_session
    return _last_completed_session()


def _verified(payload, expected=None):
    """Check date, sample coverage and actual finite prices/levels in scan."""
    if not isinstance(payload, dict):
        raise ValueError("Buzz cache payload must be a record")
    session = str(expected or _session())
    if payload.get("data_session") != session:
        raise ValueError("Buzz scan is not the completed NSE session")
    size, covered = payload.get("universe_size"), payload.get("downloaded")
    if (type(size) is not int or size < 800 or payload.get("degraded") or
            type(covered) is not int or covered > size or
            covered < math.ceil(size * MIN_COVERAGE)):
        raise ValueError("Buzz scan has incomplete index coverage")
    if not isinstance(payload.get("source"), str) or not payload["source"]:
        raise ValueError("Buzz source is absent")
    if not isinstance(payload.get("regime"), dict) or not payload["regime"].get("text"):
        raise ValueError("Nifty benchmark context missing")
    stamp = datetime.fromisoformat(payload.get("ts", ""))
    if stamp.tzinfo is None:
        raise ValueError("Buzz generation timestamp lacks a timezone")
    from stocks_buzz import IST
    now = datetime.now(IST)
    if (stamp > now + timedelta(minutes=2) or stamp.date() < date.fromisoformat(session)
            or (now.date() - date.fromisoformat(session)).days > 4):
        raise ValueError("Buzz scan timestamp or session is stale")
    for category in ("breakouts", "watch"):
        rows = payload.get(category)
        if not isinstance(rows, list) or len(rows) > size:
            raise ValueError("Invalid Buzz scan results")
        for entry in rows:
            if not isinstance(entry, dict) or not _SYMBOL.fullmatch(str(entry.get("sym", ""))):
                raise ValueError("Invalid Buzz symbol")
            if entry.get("kind") != ("BREAKOUT" if category == "breakouts" else "WATCH"):
                raise ValueError("Wrong Buzz setup classification")
            for key in ("ltp", "entry", "sl", "t1", "t2", "score"):
                value = entry.get(key)
                if type(value) not in (float, int) or not math.isfinite(value):
                    raise ValueError("Invalid Buzz market number")
                if key == "score":
                    if not 0 <= value <= 100:
                        raise ValueError("Invalid Buzz rating")
                elif value <= 0:
                    raise ValueError("Invalid Buzz market price")
            if not entry["sl"] < entry["entry"] < entry["t1"] < entry["t2"]:
                raise ValueError("Buzz trade level ordering invalid")
    return payload


def _encode(result):
    if not isinstance(result, dict):
        raise ValueError("No completed Stocks Buzz scan")
    candidate = dict(result)
    if isinstance(candidate.get("data_session"), date):
        candidate["data_session"] = candidate["data_session"].isoformat()
    if isinstance(candidate.get("ts"), datetime):
        candidate["ts"] = candidate["ts"].isoformat()
    payload = json.dumps(candidate, allow_nan=False, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("Buzz cache exceeds bounded size")
    validated = _verified(json.loads(payload))
    return validated, payload


def save_verified_scan(result):
    """Only actual >=80% complete-session scans are written atomically."""
    validated, raw = _encode(result)
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("Dedicated MSK index-research Neon database unavailable")
    digest = hashlib.sha256(raw).hexdigest()
    import psycopg
    with psycopg.connect(dsn, connect_timeout=8) as conn:
        conn.execute("SET LOCAL statement_timeout = '15000ms'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.msk_stocks_buzz_session_v1 ("
            "session_date DATE PRIMARY KEY, data BYTEA NOT NULL,"
            "sha256 CHAR(64) NOT NULL, universe_size INTEGER NOT NULL,"
            "downloaded INTEGER NOT NULL, written_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        conn.execute(
            "INSERT INTO public.msk_stocks_buzz_session_v1 "
            "(session_date,data,sha256,universe_size,downloaded) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(session_date) DO UPDATE "
            "SET data=EXCLUDED.data,sha256=EXCLUDED.sha256,"
            "universe_size=EXCLUDED.universe_size,downloaded=EXCLUDED.downloaded,"
            "written_at=NOW() "
            "WHERE public.msk_stocks_buzz_session_v1.downloaded <= EXCLUDED.downloaded",
            (validated["data_session"], raw, digest,
             validated["universe_size"], validated["downloaded"])
        )
    LOG.info("MSK_BUZZ_SNAPSHOT_SAVED session=%s reviewed=%s/%s sha256=%s",
             validated["data_session"], validated["downloaded"],
             validated["universe_size"], digest[:12])
    return digest


def load_verified_scan():
    """Bounded read-only lookup; no stale or tampered scan can reach Telegram."""
    dsn = _dsn()
    if not dsn:
        return None
    session = _session()
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            row = conn.execute(
                "SELECT data,sha256,universe_size,downloaded "
                "FROM public.msk_stocks_buzz_session_v1 WHERE session_date=%s",
                (session,)
            ).fetchone()
        if row is None:
            return None
        raw, digest, size, count = bytes(row[0]), row[1], int(row[2]), int(row[3])
        if (not raw or len(raw) > MAX_BYTES or
                hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError("Buzz cache integrity mismatch")
        payload = _verified(json.loads(raw), expected=session)
        if payload["universe_size"] != size or payload["downloaded"] != count:
            raise ValueError("Buzz DB coverage mismatch")
        from stocks_buzz import IST
        payload["ts"] = datetime.fromisoformat(payload["ts"]).astimezone(IST)
        payload["data_session"] = date.fromisoformat(payload["data_session"])
        LOG.info("MSK_BUZZ_SNAPSHOT_HIT session=%s reviewed=%d/%d",
                 session, count, size)
        return payload
    except Exception as exc:
        LOG.warning("MSK_BUZZ_SNAPSHOT_UNAVAILABLE type=%s", type(exc).__name__)
        return None


def main():
    """Run after NSE close from scheduled GitHub Actions; never send Telegram."""
    from stocks_buzz import run_scan
    result = run_scan()
    save_verified_scan(result)
    print("BUZZ_PRECOMPUTE_OK session=%s count=%s/%s" % (
          result["data_session"], result["downloaded"], result["universe_size"]))


if __name__ == "__main__":
    main()
