"""Append-only, paper-only MSK strategy observation and outcome journal.

No Telegram identifiers, account holdings or broker orders. This module never
generates BUY/SELL recommendations or calculates an accuracy claim. It has no
automatic imports from the bot and cannot silently fall back to ephemeral disk.
Use an independently configured PostgreSQL DSN when explicitly enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

STRATEGIES = frozenset({"swing_safe", "swing_aggressive", "intraday_nifty200"})
KINDS = frozenset({"OBSERVED", "WITHHELD", "PAPER_OPEN", "PAPER_CLOSE"})
DIRECTIONS = frozenset({"LONG", "SHORT", "NONE"})
OUTCOMES = frozenset({"STOP", "TARGET1", "TARGET2", "TIME", "MANUAL"})
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&.\-]{0,29}\Z")
_VERSION = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_SOURCE = re.compile(r"[A-Za-z0-9_:/#.\-]{8,200}\Z")
_DSN_ENV = "MSK_SIGNAL_JOURNAL_DATABASE_URL"

_SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS msk_signal_journal",
    """CREATE TABLE IF NOT EXISTS msk_signal_journal.events_v1 (
       event_id CHAR(64) PRIMARY KEY,
       signal_id CHAR(64) NOT NULL,
       kind TEXT NOT NULL CHECK (kind IN
           ('OBSERVED','WITHHELD','PAPER_OPEN','PAPER_CLOSE')),
       event_at TIMESTAMPTZ NOT NULL,
       payload JSONB NOT NULL,
       digest CHAR(64) NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       UNIQUE(signal_id, kind)
    )""",
    "CREATE INDEX IF NOT EXISTS msk_journal_signal_v1 ON msk_signal_journal.events_v1(signal_id, event_at)",
)


class JournalError(RuntimeError):
    """Unavailable durable storage, conflicting event or invalid transition."""


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Time must have timezone information")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _price(value, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} requires a positive finite price")
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} requires a positive finite price") from None
    if not v.is_finite() or v <= 0:
        raise ValueError(f"{field} requires a positive finite price")
    return format(v, "f")


def _digest(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_event(*, strategy: str, version: str, symbol: str, direction: str,
               candle_at: datetime, kind: str, event_at: datetime,
               source_ref: str = "", entry=None, stop=None, target1=None,
               target2=None, exit_price=None, outcome=None) -> dict:
    """Validate one independent paper event; no orders or estimated outcomes.

    OBSERVED: completed-bar watchlist with conditional levels, NOT a fill.
    WITHHELD: no qualifying setup/data. Must use symbol=UNIVERSE, direction=NONE.
    PAPER_OPEN: independently confirmed paper entry, with source evidence.
    PAPER_CLOSE: independently recorded paper exit, with source evidence.
    """
    if strategy not in STRATEGIES or not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("Invalid strategy/version")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid NSE symbol")
    if direction not in DIRECTIONS or kind not in KINDS:
        raise ValueError("Invalid direction/event kind")
    candle = _timestamp(candle_at)
    event = _timestamp(event_at)
    if event < candle:
        raise ValueError("Event cannot precede its completed candle")
    if kind == "WITHHELD":
        if symbol != "UNIVERSE" or direction != "NONE":
            raise ValueError("WITHHELD events must be universe-only")
        if any(v is not None for v in (entry, stop, target1, target2, exit_price, outcome)):
            raise ValueError("WITHHELD must not contain artificial prices")
    elif direction not in ("LONG", "SHORT"):
        raise ValueError("Qualified paper events need a direction")
    if kind != "WITHHELD":
        if not isinstance(source_ref, str) or not _SOURCE.fullmatch(source_ref):
            raise ValueError("Evidence source reference is required")
    elif source_ref and (not isinstance(source_ref, str) or not _SOURCE.fullmatch(source_ref)):
        raise ValueError("Invalid evidence reference")

    data = {
        "schema": 1, "strategy": strategy, "version": version,
        "symbol": symbol, "direction": direction,
        "candle_at": candle, "kind": kind, "event_at": event,
        "source_ref": source_ref,
    }
    signal_key = {k: data[k] for k in ("strategy", "version", "symbol", "direction", "candle_at")}
    data["signal_id"] = _digest(signal_key)
    if kind != "WITHHELD":
        p = {name: _price(val, name) for name, val in (
            ("entry", entry), ("stop", stop), ("target1", target1), ("target2", target2))}
        c, s, t1, t2 = (Decimal(p[key]) for key in ("entry", "stop", "target1", "target2"))
        if not ((s < c < t1 < t2) if direction == "LONG" else (s > c > t1 > t2)):
            raise ValueError("Stop and targets have wrong direction")
        data.update(p)
    if kind == "PAPER_CLOSE":
        data["exit_price"] = _price(exit_price, "exit_price")
        if outcome not in OUTCOMES:
            raise ValueError("PAPER_CLOSE needs a recorded exit reason")
        px = Decimal(data["exit_price"])
        if (outcome == "STOP" and (px > s if direction == "LONG" else px < s)):
            raise ValueError("Exit price does not confirm stop outcome")
        if (outcome == "TARGET1" and (px < t1 if direction == "LONG" else px > t1)):
            raise ValueError("Exit price does not confirm target1 outcome")
        if (outcome == "TARGET2" and (px < t2 if direction == "LONG" else px > t2)):
            raise ValueError("Exit price does not confirm target2 outcome")
    elif exit_price is not None or outcome is not None:
        raise ValueError("Only PAPER_CLOSE may include an outcome/exit")
    if kind == "PAPER_OPEN" and (not source_ref or source_ref.startswith("synthetic")):
        raise ValueError("PAPER_OPEN needs non-synthetic evidence")
    if kind == "PAPER_CLOSE" and (not source_ref or source_ref.startswith("synthetic")):
        raise ValueError("PAPER_CLOSE needs non-synthetic evidence")
    if kind == "PAPER_CLOSE":
        data["outcome"] = outcome
    data["event_id"] = _digest({"signal_id": data["signal_id"], "kind": kind})
    return data


class PostgresSignalJournal:
    """Append-only event store with strict transition and conflict rules.

    Database account needs access only to the dedicated journal schema.
    Each event is inserted once. No update/delete or customer data APIs exist.
    """

    def __init__(self, dsn: str | None = None, *, connector=None):
        self._dsn = dsn if dsn is not None else os.environ.get(_DSN_ENV, "")
        if (not isinstance(self._dsn, str)
                or not self._dsn.startswith(("postgresql://", "postgres://"))
                or "sslmode=require" not in self._dsn):
            raise JournalError("Dedicated TLS PostgreSQL journal is not configured")
        if connector is None:
            try:
                import psycopg
            except ImportError as exc:
                raise JournalError("psycopg dependency required for journal") from exc
            connector = lambda dsn: psycopg.connect(dsn, connect_timeout=5)
        self._connector = connector

    def append(self, event: dict) -> bool:
        """True on new write, False on identical retry; conflicting retries fail."""
        if not isinstance(event, dict):
            raise ValueError("Journal event must be a dictionary")
        try:
            clean = make_event(
                strategy=event["strategy"], version=event["version"],
                symbol=event["symbol"], direction=event["direction"],
                candle_at=datetime.fromisoformat(event["candle_at"]),
                kind=event["kind"], event_at=datetime.fromisoformat(event["event_at"]),
                source_ref=event["source_ref"], entry=event.get("entry"),
                stop=event.get("stop"), target1=event.get("target1"),
                target2=event.get("target2"), exit_price=event.get("exit_price"),
                outcome=event.get("outcome"),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed journal event") from exc
        if clean != event:
            raise ValueError("Journal event checksum or fields have been modified")

        payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connector(self._dsn) as conn:
            conn.execute("SET LOCAL statement_timeout = '12000ms'")
            # Coordinate schema creation across independent Render/worker starts.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (9821130101,))
            for stmt in _SCHEMA:
                conn.execute(stmt)
            kind = clean["kind"]
            if kind in ("PAPER_OPEN", "PAPER_CLOSE"):
                required = "OBSERVED" if kind == "PAPER_OPEN" else "PAPER_OPEN"
                prior = conn.execute(
                    "SELECT event_at, payload FROM msk_signal_journal.events_v1 "
                    "WHERE signal_id=%s AND kind=%s FOR SHARE",
                    (clean["signal_id"], required),
                ).fetchone()
                if not prior:
                    raise JournalError(f"{kind} requires a previously recorded {required}")
                prior_time = prior[0]
                if isinstance(prior_time, datetime):
                    prior_time = _timestamp(prior_time)
                else:
                    prior_time = str(prior_time)
                if clean["event_at"] < prior_time:
                    raise JournalError("Paper event timestamp precedes prior event")
                earlier = prior[1]
                if isinstance(earlier, str):
                    earlier = json.loads(earlier)
                # A paper fill can differ from the original indicative entry;
                # its stop and targets cannot be silently rewritten. Closing
                # must retain the actual opening fill for correct future P&L.
                levels = ("stop", "target1", "target2") if kind == "PAPER_OPEN" else (
                    "entry", "stop", "target1", "target2")
                if any(earlier.get(k) != clean.get(k) for k in levels):
                    raise JournalError("Paper trade levels differ from prior event")
            inserted = conn.execute(
                "INSERT INTO msk_signal_journal.events_v1 "
                "(event_id, signal_id, kind, event_at, payload, digest) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT DO NOTHING RETURNING event_id",
                (clean["event_id"], clean["signal_id"], kind, clean["event_at"],
                 payload, digest),
            ).fetchone()
            if inserted:
                return True
            prior = conn.execute(
                "SELECT digest FROM msk_signal_journal.events_v1 "
                "WHERE signal_id=%s AND kind=%s",
                (clean["signal_id"], kind),
            ).fetchone()
            if not prior or str(prior[0]).strip() != digest:
                raise JournalError("Event already exists with different immutable data")
            return False
