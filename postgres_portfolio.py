"""Opt-in PostgreSQL portfolio backend for a verified, durable database.

Default bot storage is unchanged. Operators must import a frozen legacy snapshot,
verify the cutover marker, and explicitly approve activation. Never log a DSN.
"""
import hashlib
import math
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from portfolio_migration import load_legacy_holdings, load_legacy_account_ids

_HOLDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    uid BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    qty NUMERIC(24,8) NOT NULL CHECK (qty > 0),
    avg NUMERIC(24,8) NOT NULL CHECK (avg >= 0),
    PRIMARY KEY(uid, symbol)
)
"""
_ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_accounts (
    uid BIGINT PRIMARY KEY CHECK (uid > 0)
)
"""
_CUTOVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_cutover (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    source_sha256 CHAR(64) NOT NULL,
    initial_position_count INTEGER NOT NULL CHECK (initial_position_count >= 0)
)
"""
_SYMBOL = re.compile(r"[A-Za-z0-9&._-]{1,50}\Z")
_MAX_UID = 9223372036854775807
_MAX_NUM = Decimal("9999999999999999.99999999")


class PortfolioStorageError(RuntimeError):
    """Fail closed rather than silently drop or overwrite holdings."""


def _validate_dsn(dsn):
    if not isinstance(dsn, str) or not dsn:
        raise PortfolioStorageError("PORTFOLIO_DATABASE_URL is required")
    try:
        parsed = urlsplit(dsn)
        query = parse_qs(parsed.query)
        sslmode = query.get("sslmode", [""])[0]
    except (ValueError, TypeError) as exc:
        raise PortfolioStorageError("Invalid PostgreSQL connection URL") from exc
    if (parsed.scheme not in ("postgres", "postgresql")
            or not parsed.hostname
            or sslmode not in ("require", "verify-ca", "verify-full")):
        raise PortfolioStorageError(
            "PostgreSQL URL must use a host and sslmode=require/verify-ca/verify-full"
        )
    return dsn


def _connect_postgres(dsn):
    try:
        import psycopg
    except ImportError as exc:
        raise PortfolioStorageError("Install psycopg to use PostgreSQL portfolio storage") from exc
    return psycopg.connect(dsn, connect_timeout=10)


def _create_schema(conn):
    conn.execute(_ACCOUNTS_SCHEMA)
    conn.execute(_HOLDINGS_SCHEMA)
    conn.execute(_CUTOVER_SCHEMA)


def _uid(value):
    if type(value) is not int or value <= 0 or value > _MAX_UID:
        raise ValueError("Invalid Telegram user ID")
    return value


def _symbol(value):
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError("Invalid portfolio symbol")
    return value


def _amount(value, *, positive):
    if type(value) not in (int, float, Decimal):
        raise ValueError("Invalid portfolio numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid portfolio numeric value") from exc
    if not result.is_finite() or result > _MAX_NUM or (result <= 0 if positive else result < 0):
        raise ValueError("Invalid portfolio numeric range")
    if result.as_tuple().exponent < -8:
        raise ValueError("Portfolio numeric precision exceeds eight decimal places")
    return result


class PostgresPortfolioStore:
    """Compatible with legacy PortfolioManager.get/add/remove signatures."""

    def __init__(self, dsn, *, connector=None):
        self._dsn = _validate_dsn(dsn)
        self._connector = connector or _connect_postgres
        # Do not permit an empty/unmigrated DB to masquerade as an empty portfolio.
        with self._connector(self._dsn) as conn:
            _create_schema(conn)
            cutover = conn.execute(
                "SELECT source_sha256 FROM portfolio_cutover WHERE id = 1"
            ).fetchone()
            if not cutover:
                raise PortfolioStorageError(
                    "Portfolio cutover marker missing; migrate/verify legacy snapshot first"
                )

    def get(self, uid):
        uid = _uid(uid)
        with self._connector(self._dsn) as conn:
            rows = conn.execute(
                "SELECT symbol, qty, avg FROM holdings WHERE uid = %s", (uid,)
            ).fetchall()
        return {symbol: {"qty": float(qty), "avg": float(avg)}
                for symbol, qty, avg in rows}

    def add(self, uid, sym, qty, price):
        """Atomic 10-position maximum for real PostgreSQL concurrent writers.

        The advisory transaction lock serializes all buys for a user before
        checking capacity. Existing holdings update without consuming a slot.
        Do not delete or truncate any preexisting migrated holdings.
        """
        uid, sym = _uid(uid), _symbol(sym)
        quantity = _amount(qty, positive=True)
        cost = _amount(price, positive=False)
        if quantity != quantity.to_integral_value() or quantity > 10**9:
            raise ValueError("Holding quantity must be a positive integer <= 1e9")
        if cost <= 0 or cost > 10**12:
            raise ValueError("Portfolio purchase price out of range")
        average = cost.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
        with self._connector(self._dsn) as conn:
            # uid-based transaction-scoped lock prevents 2 workers from
            # both observing 9 holdings and inserting the 10th/11th.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (uid,))
            current = conn.execute(
                "SELECT qty FROM holdings WHERE uid=%s AND symbol=%s",
                (uid, sym)
            ).fetchone()
            if current is None:
                count = conn.execute(
                    "SELECT COUNT(*) FROM holdings WHERE uid=%s", (uid,)
                ).fetchone()[0]
                if count >= 10:
                    raise ValueError("Portfolio maximum is 10 distinct holdings")
            elif _amount(current[0], positive=True) + quantity > 10**9:
                raise ValueError("Holding quantity exceeds limit")
            conn.execute(
                "INSERT INTO portfolio_accounts(uid) VALUES(%s) "
                "ON CONFLICT(uid) DO NOTHING", (uid,)
            )
            conn.execute(
                """INSERT INTO holdings(uid, symbol, qty, avg)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (uid, symbol) DO UPDATE
                   SET avg = ROUND(
                       (holdings.qty * holdings.avg +
                        EXCLUDED.qty * EXCLUDED.avg) /
                       (holdings.qty + EXCLUDED.qty), 2),
                       qty = holdings.qty + EXCLUDED.qty""",
                (uid, sym, quantity, average),
            )

    def remove(self, uid, sym):
        uid, sym = _uid(uid), _symbol(sym)
        with self._connector(self._dsn) as conn:
            result = conn.execute(
                "DELETE FROM holdings WHERE uid = %s AND symbol = %s", (uid, sym)
            )
            return result.rowcount > 0


def _holdings_match_snapshot(conn, rows):
    """Verify all imported positions; an identical SHA alone is not a restore proof.

    Read only. The caller keeps this check inside the import transaction;
    never repair existing holdings silently or mask legitimate later trades.
    """
    stored = conn.execute(
        "SELECT uid, symbol, qty, avg FROM holdings"
    ).fetchall()
    if len(stored) != len(rows):
        return False

    def normalized(items):
        return sorted(
            (int(uid), symbol, _amount(qty, positive=True),
             _amount(avg, positive=False))
            for uid, symbol, qty, avg in items
        )

    try:
        return normalized(stored) == normalized(rows)
    except (TypeError, ValueError, InvalidOperation):
        return False


def import_legacy_to_postgres(source, dsn, *, connector=None, apply=False, allow_empty=False):
    """Import all holdings and commit a cutover marker atomically.

    Dry-run by default. Requires an operator-frozen, securely backed-up JSON source.
    No existing positions are overwritten; a different cutover is rejected.
    """
    dsn = _validate_dsn(dsn)
    source = Path(source)
    try:
        frozen_bytes = source.read_bytes()
    except OSError as exc:
        raise PortfolioStorageError("Could not capture legacy portfolio snapshot") from exc
    rows = load_legacy_holdings(source, snapshot_bytes=frozen_bytes)
    users = load_legacy_account_ids(source, snapshot_bytes=frozen_bytes)
    digest = hashlib.sha256(frozen_bytes).hexdigest()
    if not apply:
        return {"status": "dry-run", "positions": len(rows)}
    if not rows and not allow_empty:
        raise PortfolioStorageError(
            "Empty legacy portfolio snapshot; explicit allow_empty required for import"
        )
    connect = connector or _connect_postgres
    with connect(dsn) as conn:
        _create_schema(conn)
        marker = conn.execute(
            "SELECT source_sha256 FROM portfolio_cutover WHERE id = 1"
        ).fetchone()
        if marker:
            if marker[0] != digest:
                raise PortfolioStorageError("Different legacy snapshot was already imported")
            if not _holdings_match_snapshot(conn, rows):
                raise PortfolioStorageError(
                    "Stored holdings differ from frozen migration snapshot; "
                    "refusing already-imported verification"
                )
            stored_users = [x[0] for x in conn.execute(
                "SELECT uid FROM portfolio_accounts ORDER BY uid"
            ).fetchall()]
            if stored_users != users:
                raise PortfolioStorageError(
                    "Stored portfolio account IDs differ from frozen snapshot"
                )
            return {"status": "already-imported", "positions": len(rows)}
        count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        accounts_count = conn.execute(
            "SELECT COUNT(*) FROM portfolio_accounts"
        ).fetchone()[0]
        if count or accounts_count:
            raise PortfolioStorageError(
                "Target already has holdings/accounts but no cutover marker; refusing overwrite"
            )
        for uid in users:
            conn.execute(
                "INSERT INTO portfolio_accounts(uid) VALUES(%s)", (uid,)
            )
        for uid, symbol, qty, avg in rows:
            conn.execute(
                "INSERT INTO holdings(uid, symbol, qty, avg) VALUES (%s,%s,%s,%s)",
                (uid, symbol, _amount(qty, positive=True),
                 _amount(avg, positive=False)),
            )
        # A matching count or marker alone is insufficient; compare all rows
        # while this transaction can still roll back on a failed readback.
        if not _holdings_match_snapshot(conn, rows):
            raise PortfolioStorageError("Imported portfolio readback mismatch")
        observed_users = [x[0] for x in conn.execute(
            "SELECT uid FROM portfolio_accounts ORDER BY uid"
        ).fetchall()]
        if observed_users != users:
            raise PortfolioStorageError("Imported account readback mismatch")
        conn.execute(
            "INSERT INTO portfolio_cutover(id, source_sha256, initial_position_count)"
            " VALUES (1, %s, %s)", (digest, len(rows))
        )
    return {"status": "imported", "positions": len(rows)}


def build_portfolio_backend(legacy_factory, *, environ=None, connector=None):
    """No PostgreSQL connections or imports when default legacy mode is active."""
    env = os.environ if environ is None else environ
    backend = env.get("PORTFOLIO_BACKEND", "legacy_json").strip().lower()
    if backend == "legacy_json":
        return legacy_factory()
    if backend != "postgres":
        raise PortfolioStorageError("Unknown PORTFOLIO_BACKEND; refusing fallback")
    if env.get("PORTFOLIO_CUTOVER_APPROVED", "").lower() != "true":
        raise PortfolioStorageError("PostgreSQL cutover requires explicit approval")
    return PostgresPortfolioStore(
        env.get("PORTFOLIO_DATABASE_URL"), connector=connector
    )

def main():
    """Operator-only command: use a secret environment variable, never a CLI DSN."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Safely import a frozen legacy JSON portfolio into PostgreSQL"
    )
    parser.add_argument("legacy_json", type=Path)
    parser.add_argument("--allow-empty", action="store_true",
                        help="Explicitly approve importing an empty legacy snapshot")
    parser.add_argument("--apply", action="store_true",
                        help="Perform the import (default is read-only dry-run)")
    args = parser.parse_args()
    dsn = os.getenv("PORTFOLIO_DATABASE_URL")
    outcome = import_legacy_to_postgres(
        args.legacy_json, dsn, apply=args.apply, allow_empty=args.allow_empty
    )
    # Emit aggregate counters only; never emit credentials or client holdings.
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
