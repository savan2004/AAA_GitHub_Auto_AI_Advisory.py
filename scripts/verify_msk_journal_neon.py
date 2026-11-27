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
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

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


def _validate_staging_host(host, port):
    """Allow only the verified staging endpoint and its exact pooler alias.

    The alias is rewritten to the pinned direct endpoint before any SQL call;
    it is never used as a network destination. All other hosts remain rejected.
    """
    pooled = STAGING_HOST.replace(".", "-pooler.", 1)
    if host not in (STAGING_HOST, pooled):
        category = "host_missing" if not host else "other_host"
        raise ProbeError("Connection is NOT the approved staging direct host; "
                         "select branch br-delicate-base-b3zz6z20, non-pooled; "
                         "category=" + category)
    if port not in (None, "", 5432, "5432"):
        raise ProbeError("Connection is NOT the approved staging direct host/port; "
                         "category=wrong_port")
    return host == pooled


def _conninfo_to_url(raw):
    """Normalize a standard libpq key/value string after strict staging checks."""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        raise ProbeError("Staging connection info is malformed") from None
    allowed = {"host", "port", "dbname", "user", "password", "sslmode", "channel_binding"}
    values = {}
    for token in tokens:
        if "=" not in token:
            raise ProbeError("Staging connection info contains an unsupported option")
        key, value = token.split("=", 1)
        if key not in allowed or key in values:
            raise ProbeError("Staging connection info contains unsupported or duplicate options")
        values[key] = value
    required = {"host", "dbname", "user", "password", "sslmode"}
    missing = sorted(k for k in required if k not in values or not values[k])
    if missing:
        # Field names are safe to report; never print the corresponding values.
        raise ProbeError("Staging connection info missing fields: " + ",".join(missing))
    _validate_staging_host(values["host"], values.get("port", "5432"))
    if values["dbname"] != STAGING_DATABASE:
        raise ProbeError("Wrong staging database; select msk_index_research")
    if values["sslmode"] != "require":
        raise ProbeError("Staging connection must use sslmode=require")
    if values.get("channel_binding", "") not in ("", "require", "prefer"):
        raise ProbeError("Staging connection channel binding option unsupported")
    query = {"sslmode": "require"}
    if values.get("channel_binding"):
        query["channel_binding"] = values["channel_binding"]
    authority = quote(values["user"], safe="") + ":" + quote(values["password"], safe="") + "@" + STAGING_HOST
    return urlunsplit(("postgresql", authority, "/" + quote(STAGING_DATABASE, safe=""), urlencode(query), ""))


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
    # Neon Connect can copy a URL, psql URL wrapper, or libpq key/value
    # connection info. Parse as data only; never execute a command or print secrets.
    dsn = dsn.strip()
    if dsn.startswith("psql ") or dsn[:1] in ("'", '"'):
        try:
            tokens = shlex.split(dsn)
        except ValueError:
            raise ProbeError("Staging DSN wrapper is malformed") from None
        if tokens and tokens[0] == "psql":
            args = tokens[1:]
            if len(args) == 1 and args[0].startswith(("postgres://", "postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://", "postgresql+psycopg3://")):
                dsn = args[0]
            elif args and all("=" in token for token in args):
                dsn = " ".join(args)
            else:
                raise ProbeError("Staging DSN wrapper is unsupported")
        elif len(tokens) == 1 and dsn[:1] in ("'", '"'):
            dsn = tokens[0]
        else:
            raise ProbeError("Staging DSN wrapper is unsupported")
    # Common Neon "Copy as psql command" key/value format has no URL scheme.
    # Convert only after pinning host, database and TLS options.
    if "=" in dsn and "://" not in dsn:
        dsn = _conninfo_to_url(dsn)
    try:
        if not dsn or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in dsn):
            raise ValueError("invalid URL")
        parsed = urlsplit(dsn)
        host = parsed.hostname
        port = parsed.port
        user = parsed.username
        password = parsed.password
    except (ValueError, TypeError):
        raise ProbeError("Staging DSN is not a valid PostgreSQL URL") from None

    # Report only fixed, non-secret configuration categories so a GitHub
    # Actions failure can be corrected without revealing the DSN or password.
    if parsed.scheme not in ("postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+psycopg3"):
        # Even an invalid scheme might contain secret material; never echo it.
        raise ProbeError("Staging DSN scheme unsupported: missing" if not parsed.scheme
                         else "Staging DSN scheme unsupported: unrecognized")
    pooled_alias = _validate_staging_host(host, port)
    if parsed.path != "/" + STAGING_DATABASE:
        raise ProbeError("Wrong staging database; select msk_index_research")
    if not user or not password:
        raise ProbeError("Staging DSN lacks database username or password")
    # libpq URI query parameters can override the host, dbname, user or TLS.
    # Only the exact harmless Neon connection options are allowed.
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                         max_num_fields=4)
    except ValueError:
        raise ProbeError("Staging DSN query is malformed") from None
    if set(query)-{"sslmode", "channel_binding"} or any(len(v)!=1 for v in query.values()):
        raise ProbeError("Staging DSN query contains unsupported or duplicate options")
    if query.get("sslmode") != ["require"]:
        raise ProbeError("Staging DSN must include sslmode=require")
    if "channel_binding" in query and query["channel_binding"] not in (["require"], ["prefer"]):
        raise ProbeError("Staging DSN channel binding option unsupported")
    if parsed.fragment:
        raise ProbeError("Staging DSN has an unexpected URL fragment")
    # SQLAlchemy adds a driver suffix that psycopg.connect does not accept.
    # Normalize only the scheme after the pinned host/database/TLS checks.
    if parsed.scheme in ("postgresql+psycopg", "postgresql+psycopg2", "postgresql+psycopg3"):
        parsed = parsed._replace(scheme="postgresql")
    if pooled_alias:
        # Preserve the encoded userinfo exactly. Never replace arbitrary host
        # substrings, and never let query options override the pinned endpoint.
        authority = parsed.netloc.rsplit("@", 1)[0] + "@" + STAGING_HOST
        if port is not None:
            authority += ":5432"
        parsed = parsed._replace(netloc=authority)
    return parsed.geturl(), run_id


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
        # A newly provisioned staging branch may not yet have the table.
        # The actual writer owns idempotent schema creation.
        table = conn.execute(
            "SELECT to_regclass('msk_signal_journal.events_v1')"
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT count(*) FROM msk_signal_journal.events_v1 "
            "WHERE signal_id = %s", (plan["signal_id"],)
        ).fetchone()[0] if table is not None else 0
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
