"""Read-only production portfolio preflight with an exclusive local snapshot.

This is the FIRST stage of an operator-approved recovery process. It does not
upload, email, publish, migrate or modify customer holdings. A local copy is
NOT an off-host backup and NEVER approves a production database cutover.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone

from portfolio_migration import PortfolioMigrationError, load_legacy_holdings

_MAX_BYTES = 64 * 1024 * 1024


class BackupPreflightError(RuntimeError):
    pass


def _read_regular(path):
    """Read one immutable buffer and reject symlinks, special files and races."""
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BackupPreflightError("Snapshot input unavailable or unsafe") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
            raise BackupPreflightError("Snapshot input is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise BackupPreflightError("Snapshot input exceeds size limit")
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        identity = lambda v: (v.st_dev, v.st_ino, v.st_size,
                              v.st_mtime_ns, v.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise BackupPreflightError("Source changed during snapshot read")
        return raw
    except OSError as exc:
        raise BackupPreflightError("Snapshot input read failed") from exc
    finally:
        os.close(fd)


def _inspect(source, raw, *, allow_empty=False):
    try:
        positions = load_legacy_holdings(source, snapshot_bytes=raw)
    except (PortfolioMigrationError, ValueError) as exc:
        raise BackupPreflightError("Legacy holdings validation failed") from exc
    if not positions and not allow_empty:
        raise BackupPreflightError(
            "Empty portfolio cannot certify production; use explicit allow-empty"
        )
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "users": len({row[0] for row in positions}),
            "positions": len(positions)}


def _private_output_dir(path):
    parent = Path(path).absolute().parent
    try:
        metadata = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise BackupPreflightError("Backup directory unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
        raise BackupPreflightError("Backup directory must exist with mode 0700")
    return parent


def create_local_snapshot(source, destination, *, allow_empty=False):
    """Copy exact source bytes to a new private file, then verify readback.

    The caller MUST copy the local snapshot off-host and independently test
    restoration; neither is performed or claimed by this function.
    """
    source, destination = Path(source), Path(destination)
    raw = _read_regular(source)
    info = _inspect(source, raw, allow_empty=allow_empty)
    parent = _private_output_dir(destination)
    if source.absolute() == destination.absolute():
        raise BackupPreflightError("Source and backup must differ")
    name = destination.name
    if name in ("", ".", ".."):
        raise BackupPreflightError("Invalid backup filename")

    # openat-style exclusive creation prevents overwriting another backup or
    # following a malicious destination symlink in the private directory.
    folder_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            out = os.open(name,
                          os.O_WRONLY | os.O_CREAT | os.O_EXCL
                          | getattr(os, "O_NOFOLLOW", 0),
                          0o600, dir_fd=folder_fd)
        except OSError as exc:
            raise BackupPreflightError("Backup destination already exists or unsafe") from exc
        try:
            with os.fdopen(out, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(folder_fd)
            # Confirm source did not change between capture and completed copy.
            if _read_regular(source) != raw:
                raise BackupPreflightError("Source changed during snapshot copy")
            candidate = parent / name
            copy_bytes = _read_regular(candidate)
            copy_info = _inspect(candidate, copy_bytes, allow_empty=allow_empty)
            if copy_bytes != raw or info != copy_info:
                raise BackupPreflightError("Backup readback/reconciliation mismatch")
            permissions = stat.S_IMODE(os.stat(candidate, follow_symlinks=False).st_mode)
            if permissions != 0o600:
                raise BackupPreflightError("Snapshot backup permissions are not 0600")
            return {
                "status": "local_copy_verified",
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_sha256": info["sha256"],
                "backup_sha256": copy_info["sha256"],
                "bytes": info["bytes"],
                "users": info["users"],
                "positions": info["positions"],
                "source_and_copy_equal": True,
                "backup_mode": "0600",
                "off_host_verified": False,
                "independent_restore_verified": False,
                "production_cutover_approved": False,
            }
        except Exception:
            try:
                os.unlink(name, dir_fd=folder_fd)
                os.fsync(folder_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(folder_fd)


def verify_source_against_backup(source, backup, *, allow_empty=False):
    """Check two separately obtained files. Does not infer off-host custody."""
    source, backup = Path(source), Path(backup)
    if source.absolute() == backup.absolute():
        raise BackupPreflightError("Source and candidate must differ")
    # Reject symlink/../ aliases and hard links: comparing a file to itself
    # must never be accepted as evidence of a second independently held copy.
    try:
        source_stat = os.stat(source, follow_symlinks=False)
        backup_stat = os.stat(backup, follow_symlinks=False)
    except OSError as exc:
        raise BackupPreflightError("Source or backup candidate unavailable") from exc
    if (source_stat.st_dev, source_stat.st_ino) == (
            backup_stat.st_dev, backup_stat.st_ino):
        raise BackupPreflightError("Source and candidate are the same physical file")
    original = _read_regular(source)
    candidate = _read_regular(backup)
    original_info = _inspect(source, original, allow_empty=allow_empty)
    copy_info = _inspect(backup, candidate, allow_empty=allow_empty)
    if original != candidate or original_info != copy_info:
        raise BackupPreflightError("Customer holdings snapshot differs from candidate")
    return {
        "status": "source_and_candidate_match",
        "sha256": original_info["sha256"],
        "users": original_info["users"],
        "positions": original_info["positions"],
        "bytes": original_info["bytes"],
        "off_host_verified": False,
        "independent_restore_verified": False,
        "production_cutover_approved": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Existing customer portfolio_data.json, never a guessed path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-to", type=Path)
    mode.add_argument("--verify-candidate", type=Path)
    parser.add_argument("--allow-empty", action="store_true",
                        help="Explicitly acknowledge empty source is expected")
    args = parser.parse_args(argv)
    try:
        result = (create_local_snapshot(
            args.source, args.snapshot_to, allow_empty=args.allow_empty)
            if args.snapshot_to else
            verify_source_against_backup(
                args.source, args.verify_candidate, allow_empty=args.allow_empty))
    except (BackupPreflightError, OSError) as exc:
        # Never include file bytes, Telegram IDs, holdings or DSN in logs.
        print(f"PORTFOLIO PREFLIGHT REFUSED: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
