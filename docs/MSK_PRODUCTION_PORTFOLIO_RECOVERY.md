# MSK Bot — Production Portfolio Recovery / Backup Preflight

> P0 / **No production cutover until actual holdings are independently recovered.**
> This runbook is for the MSK Bot repository only; never use AiAutoUpdate's
> channel ledger, MSK Web Advisory, or MSK-Algo-Lab as a holdings backup.

## Observed production environment, 12 November 2026

- Render service `IndianstockAIbot`, service ID
  `srv-d67gst3uibrs73cld03g`, live commit
  `01a407d2ad9e369470102fe9ca7d4f29434fad6f` since 1 Nov.
- Service plan: **Free**, Python, 1 instance, `main` auto-deploy on passing
  checks, startup command `python main.py`. No Render Postgres instance
  in the selected workspace. There is **no persistent disk**.
- Legacy production `main.py` reads/writes `portfolio_data.json` on its
  **ephemeral filesystem**. The file does not exist as a versioned GitHub
  repository artifact. Its actual live bytes and completeness are UNKNOWN.
- The production app exposes health, status, owner usage and Telegram webhook
  routes, **but no authenticated portfolio snapshot export route**.
- Render Free services **do not support SSH or Dashboard shell**. Changing
  code or environment and redeploying/restarting the *existing* free service
  can erase its current local filesystem. Even an idle spin-down does so.
  Do **not** add an unauthenticated download route or deploy a new exporter
  onto this production instance as a recovery shortcut.
- Render documentation:
  https://render.com/docs/free
  https://render.com/docs/ssh

## One-shot preflight, once the ORIGINAL snapshot has been obtained

`portfolio_backup_preflight.py` is offline, dependency-free except for the
repo's existing `portfolio_migration.py` parser, and read-only on its source.

The operator must first obtain an exact **existing, authentic copy** from
a previously secure backup or another authorized source, with provenance
and capture time. Do not guess that `{}` or a reconstructed list of holdings
is the original source. If the current free instance has already restarted
and no independent backup exists, lost ephemeral holdings might be
**irrecoverable**; label the shortfall, never silently initialize empty.

Use a private local directory (`chmod 700`), on a trusted computer with
the real recovered file already present. **Never put holdings in a GitHub
issue, build log, Telegram chat, CI artifact, or publicly accessible URL.**

```bash
mkdir -m 700 -p /secure/msk-recovery
# Place the obtained original at a restricted location first.
python portfolio_backup_preflight.py \
  --source /secure/source/portfolio_data.json \
  --snapshot-to /secure/msk-recovery/portfolio-snapshot.json

# Copy the backup to an independently secured, encrypted OFF-HOST store
# using the organization's established approved backup process.
# Retrieve a separate copy of that off-host backup to a private location.
python portfolio_backup_preflight.py \
  --source /secure/source/portfolio_data.json \
  --verify-candidate /secure/independent-restore/portfolio-snapshot.json
```

The tool's output contains **only** aggregate user and position counts,
file size, SHA-256 and verification status — never user IDs, symbols, costs
or connection credentials. It rejects symlinks, invalid/duplicate JSON keys,
corrupt/missing holdings, nonfinite figures, empty portfolios by default,
concurrent source changes, existing destinations and insecure output
directories. It writes the snapshot with file mode 0600, exclusive creation,
fsync and byte-for-byte readback. It **never** overwrites the source.

A local backup is **not** an off-host backup and a successful byte comparison
does not prove its provenance. The output ALWAYS leaves
`off_host_verified=false`, `independent_restore_verified=false`, and
`production_cutover_approved=false`; these require separate, audited
operator attestations. A zero-position source requires explicit
`--allow-empty` and is NOT proof that prior customer positions were zero.

## Real-data release evidence required

1. Capture/source custody: storage origin, operator, original capture time,
   source SHA-256, source file size, customer count, position count. Obtain
   authorization for accessing and storing customer financial information.
2. Freeze all `/buy`/`/sell` and portfolio write operations before taking
   an authoritative snapshot and retain the freeze through cutover. Reject any
   in-flight or replayed Telegram write that is not independently reconciled.
   The offline script's double-read check is not a substitute for this freeze.
3. Encrypted off-host copy in a storage destination **not sharing Render's
   ephemeral filesystem**. Confirm access controls, credentials, custody,
   backup retention and secure destruction of temporary copies.
4. Restore to an isolated, dedicated MSK Bot database. Verify complete
   user ID / symbol / precise quantity / average-cost equality, including
   users with zero positions, as applicable. Confirm independently the
   original source SHA, transaction count and no duplicate update processing.
5. Restore **subscribers and Telegram update-deduplication ledger** from its
   own source/backup. The absence of historical entitlements must not grant
   them automatically. Prove a second restart and `pg_dump/pg_restore`.
6. Private Telegram command acceptance for `/start`, `/buy`, `/sell`,
   `/portfolio`, lookup/buttons, callbacks and fail-closed outage paths.
   Record exact build/deployment SHA, masked log timestamps and message IDs.
7. Only then create a reviewed release candidate. The live free service
   cannot be treated as a safe backup source, and an automated CI pass on
   synthetic data must not be translated into production customer recovery.

## Code evidence

- `tests/test_portfolio_backup_preflight.py` covers byte-identical copying,
  0600 permissions, no overwrite, corrupt/duplicate JSON, symlinks, edits
  while copying, empty source, insecure backup folder and altered candidates.
- `tests/pr94_postgres_integration.py` additionally makes a private
  **synthetic** snapshot and checks its digest before actual isolated
  PostgreSQL 16 migration, restart, transaction and restore tests.
- These tests can establish that the *tool* fails closed. They **cannot**
  recover customer holdings from a Render Free instance without a
  pre-existing authorized source.

## Status

**Code/preflight tests may be READY while actual recovery remains BLOCKED.**
Do not mark Issue #6 production complete, merge, deploy, rotate, spin down,
restart or auto-upgrade the live bot solely to obtain the original file.
