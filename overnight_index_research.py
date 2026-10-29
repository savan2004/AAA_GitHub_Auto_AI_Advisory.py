"""Overnight full-universe research batches for MSK (separate from Telegram delivery).

Run repeatedly after the NSE close. Every constituent gets an explicit audit row,
including missing/invalid prices and unavailable fundamentals. PostgreSQL is
required because scheduled workers and the live bot may run on different hosts.
"""
import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from index_constituents import INDEXES, get_index_universe
from investment_screener import IST
from market_data import bulk_history
from top5_technical_ratings import technical_candidate
from top5_company_ratings import fetch_company_evidence

LOG = logging.getLogger(__name__)
PROFILES = ("conservative", "moderate", "aggressive")
ZONE = ZoneInfo("Asia/Kolkata")


def connection():
    url = os.environ.get("MSK_RESEARCH_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set MSK_RESEARCH_DATABASE_URL for durable shared research")
    import psycopg
    return psycopg.connect(url, connect_timeout=15)


def setup(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS msk_index_research (
            trade_date date NOT NULL, profile text NOT NULL, symbol text NOT NULL,
            state text NOT NULL, payload jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (trade_date, profile, symbol)
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS msk_index_reports (
            trade_date date NOT NULL, profile text NOT NULL,
            report text NOT NULL, reviewed integer NOT NULL,
            universe_size integer NOT NULL, generated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (trade_date, profile)
        )""")
    conn.commit()


def session_day(now=None):
    now = now or datetime.now(ZONE)
    if now.tzinfo is None:
        raise ValueError("session_day expects timezone-aware datetime")
    local = now.astimezone(ZONE)
    # Before 15:30 IST use the previous completed trading session; weekends
    # are rolled back. Exchange holidays are caught by candle-date validation.
    day = local.date() if local.hour * 60 + local.minute >= 15 * 60 + 30 else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def previous_results(conn, day, profile):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, state, payload FROM msk_index_research WHERE trade_date=%s AND profile=%s",
                    (day, profile))
        return {symbol: (state, payload) for symbol, state, payload in cur.fetchall()}


def save_audit(conn, day, profile, symbol, state, payload):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO msk_index_research(trade_date,profile,symbol,state,payload)
                       VALUES (%s,%s,%s,%s,%s::jsonb)
                       ON CONFLICT(trade_date,profile,symbol) DO UPDATE SET
                       state=EXCLUDED.state,payload=EXCLUDED.payload,updated_at=now()""",
                    (day, profile, symbol, state, json.dumps(payload, default=str, allow_nan=False)))
    conn.commit()


def candle_day(frame):
    if frame is None or frame.empty:
        return None
    latest = frame.index[-1]
    return latest.date() if hasattr(latest, "date") else None


def analyze_batch(conn, profile, day, limit=100, retry_failures=False):
    """One bounded pass; never mark data unavailable as having passed checks."""
    universe = get_index_universe(profile)
    symbols = list(universe["symbols"])
    stored = previous_results(conn, day, profile)
    if retry_failures:
        pending = [s for s in symbols if s not in stored or stored[s][0] != "REVIEWED"]
    else:
        pending = [s for s in symbols if s not in stored]
    selected = pending[:limit]
    if not selected:
        return {"profile": profile, "pending": 0, "completed": len(stored)}
    histories = bulk_history(selected, "1y", chunk_size=25, cache_ttl=0, adjusted=False, retries=1)
    for symbol in selected:
        frame = histories.get(symbol)
        base = {"symbol": symbol, "industry": universe["industries"].get(symbol, "Unclassified"),
                "price_session": str(candle_day(frame)), "source": "NSE membership / Yahoo EOD prices"}
        if frame is None or frame.empty or candle_day(frame) != day:
            save_audit(conn, day, profile, symbol, "PRICE_MISSING", base)
            continue
        try:
            rated = technical_candidate(symbol, frame, profile, datetime.combine(day, datetime.min.time(), tzinfo=IST))
            base["technical"] = rated
            # technical_candidate may reject a non-bullish setup; still review
            # its financials and record it in the audited universe.
            fundamental = fetch_company_evidence(symbol, base["industry"], profile, day)
            base["fundamental"] = fundamental
            base["fundamental_status"] = fundamental.get("fund_status", "UNVERIFIED")
            state = "REVIEWED" if base["fundamental_status"] == "VALIDATED" else "FINANCIAL_INCOMPLETE"
            save_audit(conn, day, profile, symbol, state, base)
        except Exception as exc:
            base["error_type"] = type(exc).__name__
            save_audit(conn, day, profile, symbol, "ERROR", base)
        time.sleep(0.2)
    counts = previous_results(conn, day, profile)
    return {"profile": profile, "attempted": len(selected), "audited": len(counts),
            "total": len(symbols), "remaining": len(symbols) - len(counts)}


def publish(conn, profile, day):
    """Publish report only when every current member has an audit record."""
    from index_top5 import _format_index_result
    universe = get_index_universe(profile)
    records = previous_results(conn, day, profile)
    symbols = universe["symbols"]
    if any(s not in records for s in symbols):
        return {"profile": profile, "ready": False, "missing": sum(s not in records for s in symbols)}
    rows = []
    price_count = 0
    validated = 0
    for symbol in symbols:
        status, item = records[symbol]
        if item.get("price_session") == str(day):
            price_count += 1
        tech = item.get("technical")
        fund = item.get("fundamental") or {}
        if fund.get("fund_status") == "VALIDATED":
            validated += 1
        if tech and fund:
            row = dict(tech, **fund)
            row["sector"] = item["industry"]
            fscore = row.get("fund_score")
            row["overall_score"] = (round(0.6 * fscore + 0.4 * tech["tech_score"], 1)
                                    if isinstance(fscore, (int, float)) else None)
            rows.append(row)
    universe["asof"] = str(day) + " NSE close (as reported by data source)"
    output = _format_index_result(profile, universe, rows, price_count,
                                  fund_checked=sum(bool(records[s][1].get("fundamental")) for s in symbols),
                                  fund_failed=len(symbols)-validated,
                                  total_rated=sum(bool(records[s][1].get("technical")) for s in symbols))
    output = ("🗓 PREPARED OVERNIGHT | " + str(day) +
              "\n🔍 Full-universe audit: " + str(len(symbols)) + "/" + str(len(symbols)) +
              " | Validated fundamentals: " + str(validated) + "/" + str(len(symbols)) + "\n" +
              output.replace("Not a full fundamental ranking of every index company; only reviewed stocks are compared.",
                             "All index constituents audited; unavailable financial data remains unverified."))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO msk_index_reports(trade_date,profile,report,reviewed,universe_size)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT (trade_date,profile)
                       DO UPDATE SET report=EXCLUDED.report,reviewed=EXCLUDED.reviewed,
                       universe_size=EXCLUDED.universe_size,generated_at=now()""",
                    (day, profile, output, len(records), len(symbols)))
    conn.commit()
    return {"profile": profile, "ready": True, "validated": validated, "audited": len(records)}


def ready_report(profile, now=None):
    """Live bot read: do not trigger API scanning from a user's button press."""
    if profile not in PROFILES:
        return None
    day = session_day(now)
    try:
        with connection() as conn:
            setup(conn)
            with conn.cursor() as cur:
                cur.execute("""SELECT report FROM msk_index_reports
                               WHERE profile=%s AND trade_date=%s
                               AND reviewed=universe_size""", (profile, day))
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as exc:
        LOG.warning("No durable MSK overnight report for %s: %s", profile, type(exc).__name__)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    day = session_day()
    with connection() as conn:
        setup(conn)
        for profile in PROFILES:
            if args.publish:
                result = publish(conn, profile, day)
            else:
                result = analyze_batch(conn, profile, day, limit=args.limit,
                                       retry_failures=args.retry_failures)
            print(json.dumps({"trade_date": str(day), **result}), flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
