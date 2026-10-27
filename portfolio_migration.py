"""Offline, opt-in migration from legacy JSON holdings to the proposed SQLite schema.

Does NOT change the running bot, provision persistent storage, or delete legacy data.
Use only after obtaining the actual production JSON snapshot and a verified backup.
"""
import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3


class PortfolioMigrationError(ValueError):
    """An import was refused to avoid losing or overwriting positions."""


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS holdings ("
    "uid INTEGER NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, "
    "avg REAL NOT NULL, PRIMARY KEY(uid, symbol))"
)
_SYMBOL = re.compile(r"[A-Za-z0-9&._-]{1,50}\Z")


def _unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise PortfolioMigrationError("Duplicate JSON object key; refusing import")
        output[key] = value
    return output


def load_legacy_holdings(source):
    """Validate the complete input before opening or creating a database.

    Returns rows of (uid, symbol, qty, average_cost), preserving original values.
    """
    source = Path(source)
    if not source.is_file():
        raise PortfolioMigrationError("Source JSON file does not exist")
    try:
        with source.open("r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PortfolioMigrationError("Could not read valid legacy JSON") from exc
    if not isinstance(data, dict):
        raise PortfolioMigrationError("Legacy portfolio must be a JSON object")

    rows = []
    for uid_text, portfolio in data.items():
        if (not isinstance(uid_text, str) or not uid_text.isascii()
                or not uid_text.isdecimal() or str(int(uid_text)) != uid_text):
            raise PortfolioMigrationError("Invalid or ambiguous user ID")
        uid = int(uid_text)
        if uid <= 0 or uid > 9223372036854775807:
            raise PortfolioMigrationError("User ID must be a positive 64-bit integer")
        if not isinstance(portfolio, dict):
            raise PortfolioMigrationError("User holdings must be a JSON object")
        for symbol, position in portfolio.items():
            if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
                raise PortfolioMigrationError("Invalid symbol in legacy holdings")
            if not isinstance(position, dict) or set(position) != {"qty", "avg"}:
                raise PortfolioMigrationError("Position must contain only qty and avg")
            qty, avg = position["qty"], position["avg"]
            for number in (qty, avg):
                if type(number) not in (int, float) or not math.isfinite(number):
                    raise PortfolioMigrationError("Position has non-finite or nonnumeric values")
            if qty <= 0 or avg < 0:
                raise PortfolioMigrationError("Quantity must be positive and average nonnegative")
            rows.append((uid, symbol, float(qty), float(avg)))
    return sorted(rows)


def migrate_legacy_holdings(source, sqlite_path, *, apply=False):
    """Dry-run by default; --apply imports atomically without overwriting holdings.

    Compatible with the holdings(uid, symbol, qty, avg) table proposed in PR #2.
    An existing, identical imported dataset is treated as already migrated.
    """
    source, sqlite_path = Path(source), Path(sqlite_path)
    if source.resolve() == sqlite_path.resolve():
        raise PortfolioMigrationError("JSON source and SQLite target must differ")
    rows = load_legacy_holdings(source)
    if not apply:
        return {"status": "dry-run", "positions": len(rows)}
    if not sqlite_path.parent.is_dir():
        raise PortfolioMigrationError("Database parent directory does not exist")
    if sqlite_path.exists() and not sqlite_path.is_file():
        raise PortfolioMigrationError("Database target is not a regular file")

    with closing(sqlite3.connect(str(sqlite_path), timeout=30)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(SCHEMA)
            existing = sorted(conn.execute(
                "SELECT uid, symbol, qty, avg FROM holdings"
            ).fetchall())
            if existing:
                if existing != rows:
                    raise PortfolioMigrationError(
                        "Target already contains different positions; no overwrite permitted"
                    )
                conn.rollback()
                return {"status": "already-imported", "positions": len(rows)}
            if rows:
                conn.executemany(
                    "INSERT INTO holdings(uid, symbol, qty, avg) VALUES(?,?,?,?)", rows
                )
            verified = sorted(conn.execute(
                "SELECT uid, symbol, qty, avg FROM holdings"
            ).fetchall())
            if verified != rows:
                raise PortfolioMigrationError("Database verification failed; rolled back")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"status": "imported", "positions": len(rows)}


def backup_sqlite_database(source, backup_path):
    """Make a SQLite-consistent backup without overwriting any existing snapshot."""
    source, backup_path = Path(source), Path(backup_path)
    if not source.is_file():
        raise PortfolioMigrationError("Source database is missing")
    if source.resolve() == backup_path.resolve() or backup_path.exists():
        raise PortfolioMigrationError("Refusing to overwrite an existing database backup")
    if not backup_path.parent.is_dir():
        raise PortfolioMigrationError("Backup parent directory does not exist")
    try:
        with closing(sqlite3.connect(str(source))) as src:
            with closing(sqlite3.connect(str(backup_path))) as dest:
                src.backup(dest)
                result = dest.execute("PRAGMA integrity_check").fetchone()
                if result != ("ok",):
                    raise PortfolioMigrationError("Backup failed SQLite integrity check")
    except Exception:
        if backup_path.is_file():
            backup_path.unlink()
        raise
    return backup_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_json", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="Explicitly write positions")
    parser.add_argument("--backup-to", type=Path, help="Optional fresh SQLite backup path")
    args = parser.parse_args()
    if args.backup_to and not args.apply:
        parser.error("--backup-to requires --apply")
    if args.backup_to and args.backup_to.exists():
        parser.error("Backup destination already exists")
    result = migrate_legacy_holdings(args.legacy_json, args.database, apply=args.apply)
    if args.backup_to:
        backup_sqlite_database(args.database, args.backup_to)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
