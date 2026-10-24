# Issue #6 — portfolio durability: migration safety and recovery runbook

## Current status (not production-ready)

- \`main.py\` currently writes holdings to local \`portfolio_data.json\`.
- Draft PR #2 introduces a \`holdings(uid,symbol,qty,avg)\` SQLite table but does **not**
  import existing JSON holdings. Merging that PR alone can make old holdings appear
  missing, even if the JSON file still exists.
- \`render.yaml\` currently specifies \`plan: free\` and no persistent disk. Changing
  from JSON to an SQLite file on the **same ephemeral filesystem does not solve
  restart/redeploy durability**.
- This change adds an **offline, manually invoked** migration and SQLite backup.
  It does **not** change the running bot, live users, service configuration, or billing.
- It cannot recover a lost \`portfolio_data.json\`. Obtain a verified backup from
  the existing running instance before any deployment or restart.

## Operator-only migration workflow

1. **Freeze portfolio updates** and obtain the actual production JSON file through
   an approved secure operator process. Never add client positions to GitHub.
2. Store the original JSON read-only in a secured, access-controlled backup location;
   record checksum, date, count, and recovery owner. Do not rely on Render's ephemeral
   disk for the only backup.
3. Choose the intended *verified durable* database destination (volume or managed
   database). Do not assume \`portfolio_data.sqlite3\` on the free instance is durable.
4. Inspect without any write:

   \`\`\`sh
   python portfolio_migration.py /secure/portfolio_data.json /durable/portfolio_data.sqlite3
   \`\`\`

5. With a maintenance window and operator approval, explicitly import and generate
   a SQLite-consistent snapshot at a NEW location:

   \`\`\`sh
   python portfolio_migration.py /secure/portfolio_data.json /durable/portfolio_data.sqlite3 \
     --apply --backup-to /secure-backups/portfolio-import-YYYYMMDD.sqlite3
   \`\`\`

6. Verify count, individual user holdings and weighted cost basis against the original,
   confirm SQLite \`PRAGMA integrity_check\`, and restore the snapshot to an isolated
   database for an independent readback. The source JSON is never modified or deleted.
7. **Do not release** until both migrations and restart/redeploy durability have passed
   end-to-end tests. Obtain approval for the durable storage provider/configuration,
   migration window, Rollback owner, and Render infrastructure change separately.

## Failure behavior

- Default is dry run; no database gets created.
- Invalid, duplicate, ambiguous, negative or nonfinite legacy values are rejected
  **before** any database write.
- Existing nonidentical target holdings are never overwritten: migration rolls back.
- Reimporting identical holdings is safe and returns \`already-imported\`.
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

\`\`\`sh
python -m unittest discover -s tests -p 'test_startup_portfolio_regression.py' -v
\`\`\`

The existing "Startup and Portfolio Regression" PR workflow invokes this suite.
