"""PR94 opt-in REAL PostgreSQL integration test, with SYNTHETIC holdings only.

Designed exclusively for an isolated local CI PostgreSQL database; refuses any
non-local host, unexpected database name or user. Never reads production holdings.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

import psycopg

from postgres_portfolio import (
    PostgresPortfolioStore,
    PortfolioStorageError,
    import_legacy_to_postgres,
)

NAME = "msk_pr94_integration"
RESTORED = "msk_pr94_restore"


def ensure_isolated_dsn(raw):
    p = urlsplit(raw)
    if (p.scheme not in ("postgres", "postgresql")
            or p.hostname not in ("localhost", "127.0.0.1")
            or p.path != f"/{NAME}" or p.username != "msk_test"
            or "sslmode=require" not in p.query):
        raise RuntimeError("Refusing any DB outside local isolated PR94 test instance")
    return p


def switch_db(p, name):
    return urlunsplit((p.scheme, p.netloc, f"/{name}", p.query, ""))


def read_rows(dsn):
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT uid, symbol, qty, avg FROM holdings ORDER BY uid, symbol"
        ).fetchall()
        marker = conn.execute(
            "SELECT source_sha256, initial_position_count FROM portfolio_cutover WHERE id=1"
        ).fetchone()
        return rows, marker


def main():
    if os.getenv("MSK_ALLOW_POSTGRES_TEST") != "YES":
        raise RuntimeError("PostgreSQL test not explicitly opted in")
    dsn = os.getenv("MSK_PR94_TEST_DATABASE_URL", "")
    parts = ensure_isolated_dsn(dsn)
    fixture = {
        "100": {
            "SBIN": {"qty": 2, "avg": 500.25},
            "HDFCBANK": {"qty": 4, "avg": 1500.50},
        },
        "200": {"RELIANCE": {"qty": 3, "avg": 2400.00}},
    }
    with tempfile.TemporaryDirectory(prefix="msk-pr94-synthetic-") as work:
        source = Path(work) / "synthetic-holdings.json"
        source.write_text(json.dumps(fixture, sort_keys=True), encoding="utf-8")
        dryrun = import_legacy_to_postgres(source, dsn)
        assert dryrun == {"status": "dry-run", "positions": 3}, dryrun
        assert import_legacy_to_postgres(source, dsn, apply=True)["status"] == "imported"

        expected_rows, marker = read_rows(dsn)
        assert len(expected_rows) == 3, expected_rows
        assert marker and marker[1] == 3, marker
        assert import_legacy_to_postgres(source, dsn, apply=True)["status"] == "already-imported"

        # Genuine PostgreSQL readback integrity gate: identical source SHA is
        # not proof if the DB was damaged after cutover.
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "UPDATE holdings SET qty=qty+1 WHERE uid=%s AND symbol=%s",
                (100, "SBIN")
            )
        try:
            import_legacy_to_postgres(source, dsn, apply=True)
        except PortfolioStorageError as exc:
            assert "Stored holdings differ" in str(exc), str(exc)
        else:
            raise AssertionError("Tampered holdings accepted as already-imported")
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "SELECT qty FROM holdings WHERE uid=%s AND symbol=%s",
                (100, "SBIN")
            ).fetchone()[0] == 3, "Verification refusal should not overwrite rows"
            conn.execute(
                "UPDATE holdings SET qty=%s WHERE uid=%s AND symbol=%s",
                (2, 100, "SBIN")
            )
        assert read_rows(dsn)[0] == expected_rows
        assert import_legacy_to_postgres(source, dsn, apply=True)["status"] == "already-imported"

        restarted = PostgresPortfolioStore(dsn)
        assert restarted.get(100)["SBIN"] == {"qty": 2.0, "avg": 500.25}

        # Actual concurrent PostgreSQL transactions: 8 workers, 5 updates each.
        def concurrent_buy(_):
            store = PostgresPortfolioStore(dsn)
            for _ in range(5):
                store.add(100, "SBIN", 1, 600.25)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(concurrent_buy, range(8)))
        store = PostgresPortfolioStore(dsn)  # independent fresh DB connection
        sb = store.get(100)["SBIN"]
        assert sb["qty"] == 42.0, sb
        expected_avg = (2 * 500.25 + 40 * 600.25) / 42
        assert abs(sb["avg"] - expected_avg) <= 0.10, (sb, expected_avg)
        assert store.get(200)["RELIANCE"]["qty"] == 3.0

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=work,
                                         delete=False) as bad:
            bad.write('{"100":{"SBIN":{"qty":-5,"avg":500}}}')
            invalid_source = Path(bad.name)
        try:
            import_legacy_to_postgres(invalid_source, dsn, apply=True)
            raise AssertionError("Invalid holdings were accepted")
        except ValueError:
            pass
        # A different but valid source may not override migrated data.
        source.write_text(json.dumps({"100": {"SBIN": {"qty": 99, "avg": 100}}}),
                          encoding="utf-8")
        try:
            import_legacy_to_postgres(source, dsn, apply=True)
            raise AssertionError("Conflicting snapshot was accepted")
        except PortfolioStorageError:
            pass
        live_rows, live_marker = read_rows(dsn)
        assert len(live_rows) == 3 and live_marker == marker

        # Real pg_dump/pg_restore to a NEW isolated database.
        for cmd in ("pg_dump", "pg_restore"):
            if not shutil.which(cmd):
                raise RuntimeError(f"Missing PostgreSQL client command: {cmd}")
        dump = str(Path(work) / "portfolio.dump")
        subprocess.run(["pg_dump", "--dbname", dsn, "-Fc", "-f", dump],
                       check=True, timeout=90, capture_output=True)
        assert Path(dump).stat().st_size > 100
        sys_dsn = switch_db(parts, "postgres")
        with psycopg.connect(sys_dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS {RESTORED} WITH (FORCE)')
            admin.execute(f'CREATE DATABASE {RESTORED}')
        restore_dsn = switch_db(parts, RESTORED)
        subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--dbname",
                        restore_dsn, dump], check=True, timeout=90, capture_output=True)
        restored_rows, restored_marker = read_rows(restore_dsn)
        assert restored_rows == live_rows, "Real database restore row/cost mismatch"
        assert restored_marker == live_marker, "Migration cutover marker missing after restore"
        assert PostgresPortfolioStore(restore_dsn).get(100)["SBIN"]["qty"] == 42.0

    print(json.dumps({
        "test_kind": "real_local_postgresql16_with_synthetic_holdings",
        "source_position_count": 3,
        "concurrent_insert_count": 40,
        "restored_position_count": len(restored_rows),
        "full_row_readback": "passed",
        "restart_persistence": "passed",
        "corrupt_input_rejected": "passed",
        "conflicting_import_rejected": "passed",
        "pg_dump_restore": "passed",
        "customer_data_accessed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
