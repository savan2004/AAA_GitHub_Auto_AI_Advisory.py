# Issue #6 — portfolio durability: migration safety and recovery runbook

## Current status (not production-ready)

- `main.py` currently writes holdings to local `portfolio_data.json`.
- Draft PR #2 introduces a `holdings(uid,symbol,qty,avg)` SQLite table but does **not**
  import existing JSON holdings. Merging that PR alone can make old holdings appear
  missing, even if the JSON file still exists.
- `render.yaml` currently specifies `plan: free` and no persistent disk. Changing
  from JSON to an SQLite file on the **same ephemeral filesystem does not solve
  restart/redeploy durability**.
- This change adds an **offline, manually invoked** migration and SQLite backup.
  It does **not** change the running bot, live users, service configuration, or billing.
- It cannot recover a lost `portfolio_data.json`. Obtain a verified backup from
  the existing running instance before any deployment or restart.

## Operator-only migration workflow

1. **Freeze portfolio updates** and obtain the actual production JSON file through
   an approved secure operator process. Never add client positions to GitHub.
2. Store the original JSON read-only in a secured, access-controlled backup location;
   record checksum, date, count, and recovery owner. Do not rely on Render's ephemeral
   disk for the only backup.
3. Choose the intended *verified durable* database destination (volume or managed
   database). Do not assume `portfolio_data.sqlite3` on the free instance is durable.
4. Inspect without any write:

   ```sh
   python portfolio_migration.py /secure/portfolio_data.json /durable/portfolio_data.sqlite3
   ```

5. With a maintenance window and operator approval, explicitly import and generate
   a SQLite-consistent snapshot at a NEW location:

   ```sh
   python portfolio_migration.py /secure/portfolio_data.json /durable/portfolio_data.sqlite3 \
     --apply --backup-to /secure-backups/portfolio-import-YYYYMMDD.sqlite3
   ```

6. Verify count, individual user holdings and weighted cost basis against the original,
   confirm SQLite `PRAGMA integrity_check`, and restore the snapshot to an isolated
   database for an independent readback. The source JSON is never modified or deleted.
7. **Do not release** until both migrations and restart/redeploy durability have passed
   end-to-end tests. Obtain approval for the durable storage provider/configuration,
   migration window, Rollback owner, and Render infrastructure change separately.

## Failure behavior

- Default is dry run; no database gets created.
- Invalid, duplicate, ambiguous, negative or nonfinite legacy values are rejected
  **before** any database write.
- Existing nonidentical target holdings are never overwritten: migration rolls back.
- Reimporting identical holdings is safe and returns `already-imported`.
- Backups do not overwrite an existing snapshot, and integrity is checked.
- For failure or dispute, preserve both immutable source JSON and DB snapshot; roll
  back the application to the previously verified data path. Never silently start
  users with an empty portfolio as though it were a successful migration.

## Required follow-on work (not implemented here)

- An explicitly configured, approved **durable** production storage backend, backups,
  and restore automation. Free Render disk is not a substitute for durable storage.
- Integration of this migration with the proposed storage adapter in PR #2, controlled
  cutover, and a documented operational restore/redeploy drill.
- Subscriber and entitlement models, Telegram update idempotency verification,
  production smoke tests and health alerting described in Issue #6.
- Review of potential data/privacy/compliance requirements before payment enablement.

## Offline regression

```sh
python -m unittest discover -s tests -p 'test_startup_portfolio_regression.py' -v
```

The existing "Startup and Portfolio Regression" PR workflow invokes this suite.

## Opt-in managed PostgreSQL storage (draft integration)

This branch also includes `postgres_portfolio.py` and an opt-in selection in `main.py`.
Without new environment settings, the existing legacy JSON manager remains the default,
including its data-loss risks. Nothing here provisions a database or authorizes costs.

**Do not turn PostgreSQL on without completing the migration and a real restore drill.**

1. Obtain an approved PostgreSQL service with verified durability, TLS, backup policy,
   access restrictions, recovery test, and a confirmed zero-cost or approved billing plan.
2. Freeze portfolio modifications. Obtain the actual live `portfolio_data.json`
   through an authorized secure operator workflow. Encrypt and retain an immutable
   off-instance backup, checksum and verified count of every client position.
3. Install the reviewed Python dependencies. Set the PostgreSQL connection URL as
   a secured `PORTFOLIO_DATABASE_URL` environment variable (TLS with `sslmode=require`
   or stronger). Never put the URL or client holdings in GitHub, chat, or logs.
4. Validate the exported JSON offline before opening any database:

   ```sh
   python postgres_portfolio.py /secure/portfolio_data.json
   ```

5. ONLY after source backup, maintenance freeze and separate operator approval,
   atomically import holdings and create the cutover marker:

   ```sh
   python postgres_portfolio.py /secure/portfolio_data.json --apply
   ```

6. Independently compare target rows, average prices and quantities against JSON.
   Make a managed PostgreSQL backup and restore it to a separate staging database.
   Existing CI verifies SQLite-based SQL emulation; it does NOT prove that a real
   PostgreSQL connection, restore, or Render redeployment succeeds.
7. For a later, approved production deployment only, configure these secure variables:

   ```text
   PORTFOLIO_BACKEND=postgres
   PORTFOLIO_CUTOVER_APPROVED=true
   PORTFOLIO_DATABASE_URL=<secure PostgreSQL connection URL with TLS>
   ```

   The application fails startup if cutover approval or the recorded import marker
   is missing; it never silently falls back to an empty JSON portfolio.
8. Verify a real staging restart/redeploy, duplicate Telegram updates, portfolio
   add/remove, data persistence, provider outage behavior, backup and restoration.

### Review dependencies and limitations

- Draft PR #2 also modifies `main.py` and adds local SQLite storage. Review and
  reconcile these implementations before any merge; do not merge blindly.
- Without the opt-in settings above, legacy JSON remains active and at risk.
- No deployment settings, environment variables, secrets, or live data were changed.
- Subscriber/entitlement schemas, webhook idempotency validation, payment readiness
  and production disaster recovery remain separate open parts of Issue #6.
- A feature flag by itself is NOT evidence that migration and restart testing passed.
