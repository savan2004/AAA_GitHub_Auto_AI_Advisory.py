"""Manual, staging-only Python -> Neon acceptance test for MSK journal.

RUN ONLY with private MSK_JOURNAL_NEON_STAGING_DSN, never with production DSN.
The branch endpoint is pinned to a separately verified Neon staging branch.
No account IDs, Telegram messages, real NSE symbols, broker orders or genuine
performance data are created.  Outputs contain only generic PASS/FAIL markers.

Commands (separate Python processes, same probe ID):
    python scripts/verify_msk_journal_neon.py write
    python scripts/verify_msk_journal_neon.py read
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from msk_signal_journal import JournalError, PostgresSignalJournal, make_event

# Confirmed using the Neon connector for isolated branch
# br-delicate-base-b3zz6z20 of MSK-Index-Overnight-Research.
STAGING_HOST = (
    "ep-divine-boat-b3wa5twm.c-4.ap-southeast-1.aws.neon.tech"
)
STAGING_DATABASE = "msk_index_research"
DSN_ENV = "MSK_JOURNAL_NEON_STAGING_DSN"
PROBE_ENV = "MSK_JOURNAL_NEON_PROBE_ID"
CONFIRM_ENV = "MSK_JOURNAL_NEON_STAGING_ONLY"
BASE = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


class ProbeError(RuntimeError):
    """A staging safeguard or expected test result was not satisfied."""


def load_config(env=None):
    """Reject default/production Neon connections before making ANY SQL call."""
    env = os.environ if env is None else env
    if env.get(CONFIRM_ENV) != "YES_STAGING_ONLY":
        raise ProbeError("Explicit staging-only confirmation is required")
    dsn = env.get(DSN_ENV, "")
    run_id = env.get(PROBE_ENV, "")
    if not re.fullmatch(r"[0-9]{14}", run_id):
        raise ProbeError("Provide a unique 14-digit staging probe ID")
    if not isinstance(dsn, str) or not dsn:
        raise ProbeError("Private staging DSN is missing")
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname
        user = parsed.username
        password = parsed.password
    except (ValueError, TypeError):
        raise ProbeError("Staging DSN is not a valid PostgreSQL URL") from None

    # Report only fixed, non-secret configuration categories so a GitHub
    # Actions failure can be corrected without revealing the DSN or password.
    if parsed.scheme not in ("postgres", "postgresql", "postgresql+psycopg"):
        raise ProbeError("Staging DSN scheme must be postgresql://, postgres://, or postgresql+psycopg://")
    if host != STAGING_HOST:
        raise ProbeError("Connection is NOT the approved staging direct host; select branch br-delicate-base-b3zz6z20, non-pooled")
    if parsed.path != "/" + STAGING_DATABASE:
        raise ProbeError("Wrong staging database; select msk_index_research")
    if not user or not password:
        raise ProbeError("Staging DSN lacks database username or password")
    if parse_qs(parsed.query).get("sslmode", [""])[0] != "require":
        raise ProbeError("Staging DSN must include sslmode=require")
    if parsed.fragment:
        raise ProbeError("Staging DSN has an unexpected URL fragment")
    # SQLAlchemy adds a driver suffix that psycopg.connect does not accept.
    # Normalize only the scheme after the pinned host/database/TLS checks.
    if parsed.scheme == "postgresql+psycopg":
        dsn = parsed._replace(scheme="postgresql").geturl()
    return dsn, run_id


def synthetic_events(run_id):
    version = "neon-probe-" + run_id
    common = dict(
        strategy="swing_safe",
        version=version,
        symbol="MSKPROBE",  # deliberately not a real NSE recommendation
        direction="LONG",
        candle_at=BASE,
        entry="100",
        stop="96",
        target1="108",
        target2="114",
    )
    observed = make_event(
        **common,
        kind="OBSERVED",
        event_at=BASE + timedelta(minutes=2),
        source_ref="staging:synthetic:watch",
    )
    opened = make_event(
        **dict(common, entry="101"),
        kind="PAPER_OPEN",
        event_at=BASE + timedelta(minutes=3),
        source_ref="staging:synthetic:fill",
    )
    closed = make_event(
        **dict(common, entry="101"),
        kind="PAPER_CLOSE",
        event_at=BASE + timedelta(days=1),
        source_ref="staging:synthetic:exit",
        exit_price="108",
        outcome="TARGET1",
    )
    return observed, opened, closed


def _connect(dsn):
    import psycopg

    conn = psycopg.connect(dsn, connect_timeout=5)
    try:
        info = conn.execute(
            "SELECT current_database(), "
            "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid())"
        ).fetchone()
        if info != (STAGING_DATABASE, True):
            raise ProbeError("Neon database/TLS check failed")
        return conn
    except BaseException:
        conn.close()
        raise


def write(dsn, run_id):
    plan, opened, closed = synthetic_events(run_id)
    store = PostgresSignalJournal(dsn)
    # Separate connection verifies we are writing only to the approved DB.
    with _connect(dsn):
        pass
    with _connect(dsn) as conn:
        existing = conn.execute(
            "SELECT count(*) FROM msk_signal_journal.events_v1 "
            "WHERE signal_id = %s", (plan["signal_id"],)
        ).fetchone()[0]
        if existing:
            raise ProbeError("Probe ID already used; choose a NEW probe ID")
    if store.append(plan) is not True:
        raise ProbeError("Initial observation was not newly inserted")
    if store.append(plan) is not False:
        raise ProbeError("Identical repeat created a duplicate")
    altered = make_event(
        strategy="swing_safe",
        version="neon-probe-" + run_id,
        symbol="MSKPROBE",
        direction="LONG",
        candle_at=BASE,
        event_at=BASE + timedelta(minutes=2),
        kind="OBSERVED",
        source_ref="staging:synthetic:altered",
        entry="100", stop="96", target1="108", target2="114",
    )
    try:
        store.append(altered)
    except JournalError:
        pass  # expected: conflicting event ID cannot be replaced
    else:
        raise ProbeError("Conflicting retry was accepted")
    if store.append(opened) is not True or store.append(closed) is not True:
        raise ProbeError("Paper event lifecycle did not commit")
    print("PYTHON_TO_NEON_STAGING_WRITE_PASS")
    print("SYNTHETIC_PAPER_LIFECYCLE_AND_DEDUP_PASS")


def read(dsn, run_id):
    plan, opened, closed = synthetic_events(run_id)
    # A *new* invocation/process creates fresh database and journal sessions.
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT payload, digest FROM msk_signal_journal.events_v1 "
            "WHERE signal_id=%s ORDER BY event_at",
            (plan["signal_id"],),
        ).fetchall()
    expected = (plan, opened, closed)
    if len(rows) != 3:
        raise ProbeError("Expected three persistent synthetic events")
    import hashlib
    import json
    for (payload, digest), event in zip(rows, expected):
        if payload != event:
            raise ProbeError("Stored record does not match original event")
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
        computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if str(digest).strip() != computed:
            raise ProbeError("Stored record checksum does not match")
    store = PostgresSignalJournal(dsn)
    for event in expected:
        if store.append(event) is not False:
            raise ProbeError("Duplicate was not blocked across processes")
    print("PYTHON_TO_NEON_STAGING_READBACK_PASS")
    print("FRESH_PROCESS_PERSISTENCE_AND_IDEMPOTENCY_PASS")
    print("SYNTHETIC_TEST_ONLY_NO_PERFORMANCE_CLAIM")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["write"], ["read"]):
        print("Usage: python scripts/verify_msk_journal_neon.py {write|read}")
        return 2
    try:
        dsn, run_id = load_config()
        if argv[0] == "write":
            write(dsn, run_id)
        else:
            read(dsn, run_id)
        return 0
    except Exception as exc:
        # Do not print psycopg exceptions: they may include connection details.
        print("STAGING_PROBE_FAILED: " + type(exc).__name__ +
              (": " + str(exc) if isinstance(exc, ProbeError) else "") +
              " (credentials and connection details suppressed)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
