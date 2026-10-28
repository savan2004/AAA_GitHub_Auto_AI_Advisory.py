"""Standalone fundamental collector. No imports from the Telegram application.

Requires Python 3.11+, requests, psycopg (v3), and a separately provisioned
PostgreSQL database. Run as an independent background worker only.
"""
import argparse
import json
import logging
import os
import random
import re
import signal
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests

LOG = logging.getLogger("fundamental_collector")
STOP = False
SYMBOL = re.compile(r"^[A-Z0-9&-]{1,25}$")
FIELDS = ("pe", "roe_pct", "revenue_growth_pct", "debt_equity_ratio",
          "market_cap", "roce_pct", "profit_growth_pct")
SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_universe (
 symbol TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE,
 next_due TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 last_attempt TIMESTAMPTZ, last_success TIMESTAMPTZ,
 last_error TEXT, failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS collector_fundamentals (
 symbol TEXT PRIMARY KEY REFERENCES collector_universe(symbol),
 provider TEXT NOT NULL, source_url TEXT NOT NULL,
 statement_period TEXT NOT NULL, collected_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS collector_attempts (
 id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 outcome TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS collector_due_idx ON collector_universe(next_due)
 WHERE enabled = TRUE;
"""

def stop(*_):
    global STOP
    STOP = True

def connection():
    import psycopg
    url = os.environ["COLLECTOR_DATABASE_URL"]
    if not url.startswith("postgresql://") and not url.startswith("postgres://"):
        raise ValueError("COLLECTOR_DATABASE_URL must be PostgreSQL")
    # psycopg defaults to TLS verification based on supplied URL parameters.
    # Require explicit verified TLS for remote connections.
    return psycopg.connect(url, connect_timeout=10, sslmode="verify-full")

def initialize(conn):
    with conn.transaction():
        conn.execute(SCHEMA)

def seed(conn, symbols):
    normalized = sorted({s.strip().upper() for s in symbols if s.strip()})
    if any(not SYMBOL.fullmatch(s) for s in normalized):
        raise ValueError("Invalid NSE symbol")
    with conn.transaction():
        for s in normalized:
            conn.execute("INSERT INTO collector_universe(symbol) VALUES (%s) ON CONFLICT DO NOTHING", (s,))
    return len(normalized)

def due_symbols(conn, limit=20):
    return [row[0] for row in conn.execute(
        "SELECT symbol FROM collector_universe WHERE enabled AND next_due <= NOW() "
        "ORDER BY next_due, symbol LIMIT %s", (limit,)).fetchall()]

def normalize(symbol, raw):
    if not isinstance(raw, dict) or raw.get("symbol") != symbol:
        raise ValueError("provider symbol mismatch")
    period = raw.get("statement_period")
    if not isinstance(period, str) or not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period):
        raise ValueError("missing/invalid statement period")
    dt = datetime.strptime(period, "%Y-%m").replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - dt).days
    if age < -32 or age > 550:
        raise ValueError("financial statement period out of date")
    source = raw.get("source_url")
    if not isinstance(source, str) or urlsplit(source).scheme != "https" or not urlsplit(source).hostname:
        raise ValueError("missing HTTPS source URL")
    provider = raw.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("missing provider attribution")
    metrics = {}
    for field in FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid numeric field: " + field)
        import math
        if not math.isfinite(value):
            raise ValueError("nonfinite field: " + field)
        metrics[field] = value
    if not metrics:
        raise ValueError("no verified financial metrics")
    return {"symbol": symbol, "provider": provider[:100], "source_url": source[:500],
            "statement_period": period, "metrics": metrics}

def fetch(symbol, session=None):
    """Only calls an explicitly configured authorized JSON API.

    Expected response: {symbol,provider,source_url,statement_period,
    pe,roe_pct,revenue_growth_pct,debt_equity_ratio,market_cap,...}.
    """
    base = os.environ.get("COLLECTOR_API_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Configure authorized HTTPS COLLECTOR_API_BASE_URL")
    # Fixed URL base; ticker is strictly validated before insertion.
    if not SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid symbol")
    token = os.environ.get("COLLECTOR_API_TOKEN", "")
    headers = {"Accept": "application/json", "User-Agent": "MSK-FundamentalCollector/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    client = session or requests
    response = client.get(base + "/fundamentals/" + quote(symbol, safe=""),
                          headers=headers, timeout=(5, 20), allow_redirects=False)
    response.raise_for_status()
    if len(response.content) > 200_000:
        raise ValueError("Oversized API response")
    return normalize(symbol, response.json())

def collect_one(conn, symbol, session=None):
    now = datetime.now(timezone.utc)
    try:
        record = fetch(symbol, session=session)
        with conn.transaction():
            conn.execute(
                "INSERT INTO collector_fundamentals(symbol,provider,source_url,statement_period,collected_at,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(symbol) DO UPDATE SET "
                "provider=EXCLUDED.provider,source_url=EXCLUDED.source_url,"
                "statement_period=EXCLUDED.statement_period,collected_at=EXCLUDED.collected_at,payload=EXCLUDED.payload",
                (symbol, record["provider"], record["source_url"], record["statement_period"],
                 now, json.dumps(record)))
            conn.execute("UPDATE collector_universe SET last_attempt=%s,last_success=%s,"
                         "next_due=%s + INTERVAL '7 days',failures=0,last_error=NULL WHERE symbol=%s",
                         (now, now, now, symbol))
            conn.execute("INSERT INTO collector_attempts(symbol,outcome) VALUES (%s,'ok')", (symbol,))
        return True
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        # Preserve last verified snapshot. Never overwrite it with a failed fetch.
        detail = (type(exc).__name__ + ": " + str(exc))[:300]
        with conn.transaction():
            conn.execute("UPDATE collector_universe SET last_attempt=%s,"
                         "failures=failures+1,last_error=%s,"
                         "next_due=NOW() + LEAST(INTERVAL '24 hours',"
                         "INTERVAL '15 minutes' * POWER(2, LEAST(failures,6))) "
                         "WHERE symbol=%s", (now, detail, symbol))
            conn.execute("INSERT INTO collector_attempts(symbol,outcome,detail) "
                         "VALUES (%s,'failed',%s)", (symbol, detail))
        LOG.warning("Collector failed for %s: %s", symbol, type(exc).__name__)
        return False

def worker(conn, once=False, interval=60):
    while not STOP:
        try:
            symbols = due_symbols(conn)
            for symbol in symbols:
                if STOP:
                    break
                collect_one(conn, symbol)
                if not once:
                    time.sleep(random.uniform(1.0, 3.0))
            if once:
                return
        except Exception:
            LOG.exception("Collector cycle failed")
            conn.rollback()
            if once:
                raise
        time.sleep(interval)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-file", help="Text file with one NSE ticker per line")
    parser.add_argument("--once", action="store_true", help="Run one due batch then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with connection() as conn:
        initialize(conn)
        if args.seed_file:
            with open(args.seed_file, encoding="utf-8") as handle:
                count = seed(conn, (line.split("#", 1)[0] for line in handle))
            LOG.info("Seeded %d symbols", count)
        worker(conn, once=args.once)

if __name__ == "__main__":
    main()
