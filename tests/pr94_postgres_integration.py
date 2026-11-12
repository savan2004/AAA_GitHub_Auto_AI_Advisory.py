"""PR94 opt-in REAL PostgreSQL integration test, with SYNTHETIC holdings only.

Designed exclusively for an isolated local CI PostgreSQL database; refuses any
non-local host, unexpected database name or user. Never reads production holdings.
"""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

import psycopg

from msk_durable_state import DurableBotState, DurableStateError
from portfolio_backup_preflight import (
    create_local_snapshot, verify_source_against_backup
)

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
        # Preflight must be proven before even a SYNTHETIC DB import.
        # This private local copy is intentionally NOT an off-host backup.
        backup = Path(work) / "synthetic-snapshot.json"
        preflight = create_local_snapshot(source, backup)
        assert preflight["positions"] == 3
        assert preflight["users"] == 2
        assert preflight["source_and_copy_equal"]
        assert preflight["backup_mode"] == "0600"
        assert not preflight["off_host_verified"]
        restored_file_report = verify_source_against_backup(source, backup)
        assert restored_file_report["sha256"] == preflight["source_sha256"]
        assert not restored_file_report["production_cutover_approved"]
        dryrun = import_legacy_to_postgres(backup, dsn)
        assert dryrun == {"status": "dry-run", "positions": 3}, dryrun
        assert import_legacy_to_postgres(backup, dsn, apply=True)["status"] == "imported"

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

        # 16 simultaneous new symbols for an empty synthetic user must
        # persist EXACTLY 10 (never 11) despite concurrent transactions.
        def competing_new_symbol(number):
            try:
                PostgresPortfolioStore(dsn).add(
                    300, f"LIMIT{number}", 1, 10 + number
                )
                return True
            except ValueError as exc:
                assert "10 distinct" in str(exc)
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            admitted = list(pool.map(competing_new_symbol, range(16)))
        assert admitted.count(True) == 10, admitted
        assert admitted.count(False) == 6, admitted
        assert len(PostgresPortfolioStore(dsn).get(300)) == 10

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

        # MSK durable webhook ledger restart and subscriber governance:
        # only synthetic test user IDs and update IDs; no real Telegram calls.
        state = DurableBotState(dsn)
        assert state.claim(801, user_id=100)
        assert not state.claim(801, user_id=100)
        assert state.get_subscriber(100)["status"] == "inactive"
        assert not state.is_entitled(100, "research_pdf")
        state.finish(801, dispatched=True)
        assert state.get_update_stage(801) == "dispatched"

        state2 = DurableBotState(dsn)  # independent DB connection/process-equivalent
        assert not state2.claim(801, user_id=100), "Duplicate after restart"
        assert state2.claim(802, user_id=200)
        state2.finish(802, dispatched=False)
        assert not state.claim(802, user_id=200), "Failed dispatch must not replay"
        assert state.claim(803, user_id=200)  # crash-before-finish simulation
        assert not state2.claim(803, user_id=200), "In-flight duplicate replay"
        assert state2.get_update_stage(803) == "claimed"
        # Eight independent concurrent claim attempts must dispatch once.
        def competing_claim(_):
            return DurableBotState(dsn).claim(805, user_id=200)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            wins = list(pool.map(competing_claim, range(8)))
        assert wins.count(True) == 1, "Multiple workers claimed same update"
        assert wins.count(False) == 7
        state.finish(805, dispatched=True)
        assert not state2.claim(805, user_id=200)

        assert state.claim(804, user_id=200)
        state.abandon_unscheduled(804)  # submit() rejected before any handler ran
        assert state2.claim(804, user_id=200), "Safe retry after no submission"
        state2.finish(804, dispatched=True)

        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        assert state.apply_reviewed_membership(
            100, "active", expiry, ("research_pdf",),
            event_key="owner-review-event-000001",
            reviewer_ref="owner-approved-001", expected_version=0
        )
        assert state.is_entitled(100, "research_pdf")
        assert not state.is_entitled(100, "portfolio_premium")
        assert not state.is_entitled(
            100, "research_pdf", now=expiry + timedelta(days=1)
        ), "Expired subscription still entitled"
        assert not state2.apply_reviewed_membership(
            100, "active", expiry, ("research_pdf",),
            event_key="owner-review-event-000001",
            reviewer_ref="owner-approved-001", expected_version=0
        ), "Duplicate approval must not increment version"
        try:
            state2.apply_reviewed_membership(
                100, "blocked", None, (),
                event_key="owner-review-event-000001",
                reviewer_ref="owner-approved-001", expected_version=1
            )
        except DurableStateError:
            pass
        else:
            raise AssertionError("Conflicting review event was accepted")
        assert state.get_subscriber(100)["version"] == 1
        assert state2.apply_reviewed_membership(
            100, "blocked", None, (),
            event_key="owner-review-event-000002",
            reviewer_ref="owner-approved-001", expected_version=1
        )
        assert state2.get_subscriber(100)["version"] == 2
        # A stale reviewed approval with a NEW event ID must not re-enable.
        try:
            state.apply_reviewed_membership(
                100, "active", expiry, ("research_pdf",),
                event_key="owner-review-event-000004",
                reviewer_ref="owner-approved-001", expected_version=1
            )
        except DurableStateError as exc:
            assert "Stale subscriber version" in str(exc)
        else:
            raise AssertionError("Stale approval reactivated blocked subscriber")
        assert state2.get_subscriber(100)["version"] == 2
        assert not state.is_entitled(100, "research_pdf")
        assert state.get_subscriber(200)["status"] == "inactive"
        try:
            state.apply_reviewed_membership(
                200, "active", datetime(2026, 11, 12),
                ("research_pdf",),
                event_key="owner-review-event-000003",
                reviewer_ref="owner-approved-001", expected_version=0
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Naive subscription expiry was accepted")

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
        restored_state = DurableBotState(restore_dsn)
        assert restored_state.get_update_stage(801) == "dispatched"
        assert restored_state.get_update_stage(802) == "failed"
        assert restored_state.get_update_stage(803) == "claimed"
        assert not restored_state.claim(801, user_id=100)
        assert restored_state.get_subscriber(100)["status"] == "blocked"
        assert restored_state.get_subscriber(100)["version"] == 2
        assert not restored_state.is_entitled(100, "research_pdf")
        assert restored_state.get_subscriber(200)["status"] == "inactive"


    print(json.dumps({
        "test_kind": "real_local_postgresql16_with_synthetic_holdings",
        "private_snapshot_readback": "passed",
        "source_snapshot_sha256_matched": "passed",
        "off_host_backup_tested": False,
        "source_position_count": 3,
        "concurrent_insert_count": 40,
        "restored_position_count": len(restored_rows),
        "full_row_readback": "passed",
        "restart_persistence": "passed",
        "corrupt_input_rejected": "passed",
        "conflicting_import_rejected": "passed",
        "pg_dump_restore": "passed",
        "concurrent_portfolio_capacity": "10_of_16_accepted",
        "concurrent_duplicate_claims": "1_of_8_accepted",
        "stale_subscriber_approval": "rejected",
        "durable_telegram_restart_duplicate": "rejected",
        "durable_failed_dispatch_replay": "blocked",
        "durable_inflight_replay": "blocked",
        "safe_no_submit_retry": "passed",
        "subscriber_review_idempotency": "passed",
        "subscriber_expiry_and_revocation": "passed",
        "subscriber_and_update_ledger_restored": "passed",
        "customer_data_accessed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
