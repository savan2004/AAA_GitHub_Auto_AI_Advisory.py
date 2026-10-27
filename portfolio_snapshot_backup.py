"""Explicit, verified backup of a frozen legacy portfolio JSON snapshot.

This module never reads Render production automatically. The operator must
first export a frozen source and choose an independently durable destination.
No user holdings or credentials are logged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from portfolio_migration import PortfolioMigrationError, load_legacy_holdings


def backup_legacy_json(source, destination, *, apply=False):
    """Fail-closed JSON backup with exclusive creation and independent readback.

    A live source must be frozen before invoking this function. It cannot
    guarantee a point-in-time snapshot of a file concurrently being written.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise PortfolioMigrationError("Backup destination must differ from source")

    # Read, validate, and ensure the source did not change during validation.
    try:
        original = source.read_bytes()
    except OSError as exc:
        raise PortfolioMigrationError("Cannot read source snapshot") from exc
    rows = load_legacy_holdings(source)
    if source.read_bytes() != original:
        raise PortfolioMigrationError("Source changed during backup validation")
    if not apply:
        return {"status": "dry-run", "positions": len(rows)}
    if not destination.parent.is_dir():
        raise PortfolioMigrationError("Backup destination directory is missing")

    try:
        # O_EXCL refuses to replace an existing file or follow a final symlink.
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PortfolioMigrationError("Refusing to overwrite an existing backup") from exc
    except OSError as exc:
        raise PortfolioMigrationError("Cannot create backup file") from exc

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        if (destination.read_bytes() != original
                or load_legacy_holdings(destination) != rows
                or source.read_bytes() != original):
            raise PortfolioMigrationError("Backup readback/source consistency failed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "status": "verified-backup",
        "positions": len(rows),
        "sha256": hashlib.sha256(original).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_json", type=Path)
    parser.add_argument("backup_json", type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="Create backup (default: validation-only dry run)")
    args = parser.parse_args()
    print(json.dumps(backup_legacy_json(
        args.legacy_json, args.backup_json, apply=args.apply
    ), sort_keys=True))


if __name__ == "__main__":
    main()
