# MSK Paper-Signal Journal: private Neon staging test

Status: **cloud GitHub Actions staging-only acceptance**, not a production or performance test.

## Approved target

- Neon project: `MSK-Index-Overnight-Research`
- Branch: `msk-journal-staging-20261122` (`br-delicate-base-b3zz6z20`)
- Database: `msk_index_research`
- Staging host: `ep-divine-boat-b3wa5twm.c-4.ap-southeast-1.aws.neon.tech`

The host is deliberately pinned in
`scripts/verify_msk_journal_neon.py` so a production/main DSN is **rejected**.
Neon endpoint names and branch IDs are not passwords.

## Preferred: Cloud-only GitHub Actions acceptance (no computer required)

1. In GitHub, add a **Repository secret** named `MSK_JOURNAL_NEON_STAGING_DSN` under `savan2004/AAA_GitHub_Auto_AI_Advisory.py`.
2. Copy the **direct (non-pooled)** Neon connection string for staging branch `br-delicate-base-b3zz6z20`, database `msk_index_research`; use `sslmode=require`. The staging Neon compute currently has connection pooling disabled. If the saved secret uses this exact staging endpoint's pooler alias, the probe rewrites it to the pinned direct hostname before opening any connection; other hosts remain rejected. Direct connection is preferred. Copy the **complete URI**, not the password or hostname alone. The probe also accepts the exact Neon Connect `psql 'postgresql://...'` wrapper or a quoted URI as data, without executing a shell command. It rejects all host/database/TLS query overrides and unsupported options. If the secret contains only a password or a hostname, replace it with the full staging URI; the script cannot reconstruct credentials.
3. The `neon-staging-python-acceptance` GitHub job on PR #243 runs the script's `write` and `read` commands in separate Python processes. Only a successful job with all five evidence markers proves acceptance.
4. The script prints a **safe reason** for a failed staging guard without revealing credentials; other connection exceptions suppress details.

Never use the main/production DSN, and never share secret values.

## Optional: Run on a trusted Windows PowerShell computer

1. Check out the PR #243 branch `feature/msk-paper-signal-journal-20261122`.
2. In the Neon console, select the **staging branch above**, database
   `msk_index_research`, and copy its `postgresql://...` connection string.
   It must contain `sslmode=require`. **Never paste it into chat, a GitHub
   issue, workflow YAML or your Git history.**
3. In a trusted PowerShell terminal, paste these commands. The first prompt
   captures the connection string without displaying it in the terminal.

```powershell
$secretDsn = Read-Host "Paste STAGING Neon DSN (hidden)" -AsSecureString
$env:MSK_JOURNAL_NEON_STAGING_DSN = [System.Net.NetworkCredential]::new("", $secretDsn).Password
Remove-Variable secretDsn
$env:MSK_JOURNAL_NEON_STAGING_ONLY = "YES_STAGING_ONLY"
$env:MSK_JOURNAL_NEON_PROBE_ID = Get-Date -Format "yyyyMMddHHmmss"
python -m pip install "psycopg[binary]==3.2.9"
python scripts/verify_msk_journal_neon.py write
if ($LASTEXITCODE -ne 0) { throw "STAGING journal write failed" }
python scripts/verify_msk_journal_neon.py read
if ($LASTEXITCODE -ne 0) { throw "STAGING journal read failed" }
Remove-Item Env:MSK_JOURNAL_NEON_STAGING_DSN
Remove-Item Env:MSK_JOURNAL_NEON_STAGING_ONLY
Remove-Item Env:MSK_JOURNAL_NEON_PROBE_ID
```

Run `write` and `read` in **separate Python invocations** with the SAME
probe ID. Do not generate a fresh probe ID between them. This establishes
persistence across new Python processes; **not** a real Render restart.

The staging writer and reader both use the pinned direct endpoint with libpq `sslmode=require` and `gssencmode=disable`, so a non-TLS client connection cannot succeed. The probe checks `current_database()` separately. Neon proxy termination means backend `pg_stat_ssl` is not reliable evidence of the client TLS connection. No TLS fallback or production access is allowed.

## Required evidence

- `PYTHON_TO_NEON_STAGING_WRITE_PASS`
- `SYNTHETIC_PAPER_LIFECYCLE_AND_DEDUP_PASS`
- `PYTHON_TO_NEON_STAGING_READBACK_PASS`
- `FRESH_PROCESS_PERSISTENCE_AND_IDEMPOTENCY_PASS`
- `SYNTHETIC_TEST_ONLY_NO_PERFORMANCE_CLAIM`

The probe inserts precisely three synthetic `MSKPROBE` events
(OBSERVED, PAPER_OPEN, PAPER_CLOSE). They are **never real trade results** and
must not be included in win rates. A unique ID is required for each new run.
No user/chat/account data or live Telegram messages are involved.

The script avoids printing credentials and intentionally suppresses raw
connection exception messages. If it fails, record the test ID, generic failure
type and whether the staging branch is reachable, without sharing a DSN.

## Security and release limits

- Do **not** copy the staging DSN into Render production, repository secrets
  used by `pull_request` events, or any other project's database.
- The code connects only to the explicitly verified staging endpoint (including safe normalization of its exact pooler alias); other
  databases or SSL-disabled connections fail **before** a connection is made.
- The existing CI Postgres integration test still runs without Neon secrets.
  Separate offline tests verify that this Neon probe refuses non-staging DSNs.
- Avoid promoting PR #243 or enabling scanner writes based on local smoke tests
  alone. Separate approval is required for least-privilege schema permissions,
  Render deployment/restart proof, source provenance, and live paper collection.
- No win-rate/profit-factor/drawdown claim is supported by synthetic events.

## Internal paper accuracy calculation

The read-only `summarize_paper_accuracy` requires an independent market evidence verifier, an exact strategy/version and an explicit round-trip cost assumption in basis points. It counts only complete chronological verified paper lifecycles; excludes synthetic/staging/CI rows, observation-only, open and unverified trades; reports net-positive exits rather than target labels as wins. Identical inputs are deduplicated; conflicting immutable events fail closed. No scanner wiring or customer publication is authorized. No verified sample means no win rate. This is a calculation helper, not a market evidence collector or proof of actual returns.
