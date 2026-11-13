# MSK Bot — Integrated deep-audit repair and final release gates

**Scope:** `savan2004/AAA_GitHub_Auto_AI_Advisory.py` only. Existing draft PR
#94, no merges or Render deployments. Never mix with AiAutoUpdate, MSK Web or
MSK-Algo-Lab.

## Concrete defects found after an earlier 12/12 CI run

| Defect | Implemented safety rule | Test |
|---|---|---|
| Group member could change another member's shared portfolio using group chat ID | `/buy`, `/sell` and `/portfolio` require a real private chat whose ID equals the verified sender ID; group access denied | `test_msk_release_safety.py`, mock Telegram session |
| JSON/PostgreSQL distinct holding limits inconsistent | Maximum 10 distinct symbols, quantity <= 1e9 and valid purchase price; existing position can be increased within limits | `test_msk_release_safety.py`, `test_msk_postgres_migration.py`, real PG16 concurrency |
| Two real PostgreSQL workers could both observe 9 positions | Per-user transaction-scoped advisory lock, count and INSERT in the same transaction; 16 simultaneous new symbols must admit exactly 10 | `tests/pr94_postgres_integration.py` |
| Analysis and Technical cards labelled last historical close `LTP` | Reject stale (>4 calendar days), future, undated, reversed and invalid daily candles; always display dated **historical close**. Show independent quote source/time separately | `technical_price_safety.py`, `test_msk_release_safety.py` |
| Chart-based ATR targets and AI price commentary could use undated old EOD | Withhold price targets/price-based AI commentary unless a current-session quote with supported provider, explicit timezone and valid original source time is available | `technical_price_safety.py`, isolated tests |
| Quote considered accepted without provider-reported symbol | Require exact returned Yahoo/NSE instrument identity; otherwise refuse that source and try safe fallback | `data_engine.py`, `test_msk_release_safety.py` |
| Mixed Yahoo fraction vs Finnhub percent ROE, including <1% | Stamp provider `roe_unit`, convert before rounding, reject unknown/nonfinite values, bump caches to exclude previous untyped figures | `data_engine.py`, `financial_quality.py`, `fundamentals.py`, provenance tests |
| Backup report lost users with zero holdings | Capture complete user list from original *same frozen bytes*; report zero-holding users and retain all account IDs in `portfolio_accounts` on migration/repeated readback/restart and pg_dump/restore | `portfolio_migration.py`, `portfolio_backup_preflight.py`, `postgres_portfolio.py` and synthetic/PG16 restore tests |
| Webhook status and actual delivered messages conflated | Acknowledge update dispatch separately from optional durable **Telegram Bot API send receipts** (chat ID/message ID/media kind, no content). Never fabricate ACK for a failed send or retry on receipt DB failure | `msk_durable_state.py`, `main.py`, ACK tests and isolated PG16 restore |
| Render probes `/` while DB readiness is only `/health/ready` | Existing `/` responds HTTP 503 when the durable ledger or executors are unavailable; no risky live Render configuration cutover | `main.py`, `test_health_endpoints.py` |

### Testing and evidence model

- Default-branch `.github/workflows/startup-regression.yml` executes the
  integrated *mock/synthetic* audit as part of the existing PR CI. It covers
  portfolio migration, backup, financial source/units, technical last-session
  handling, entitlement and Telegram acknowledgment paths.
- `.github/workflows/pr94-live-integration.yml` includes isolated real
  PostgreSQL 16 with synthetic 3-position holdings, including a zero-holding
  subscriber, 40 concurrent same-symbol adds, 16 concurrent distinct symbol
  buys, update ID 1-of-8 concurrency, entitlement expiry, three synthetic
  Telegram API ACKs, private snapshot readback and independent pg_dump/restore.
- Its separate opt-in market-data job probes up to 50 equity daily histories,
  two index histories and timestamped Yahoo/NSE quote availability. These
  runs are *sampled technical data checks*, NOT issuer financial statement
  audits, proven forecasting success rates, actual Telegram customer delivery
  or a guarantee every symbol/provider works.
- No paid provider, customer holdings, production DB or Telegram chat is
  involved in these synthetic tests.

### Non-negotiable unresolved **external release** requirements

1. The production Render Free instance is LIVE on `main` and has no
   persistent disk. Obtain the **actual original** `portfolio_data.json`
   through an already authorized source with chain of custody, encrypted
   off-host backup, source SHA256, all account/position counts and independent
   restore. Do not redeploy, restart, spin down or expose a new recovery
   endpoint merely to try to extract the file; it may be irrecoverable.
2. Provision and verify a dedicated backed-up **MSK Bot** PostgreSQL instance
   with access controls, retention, restoration, isolated staging and a
   full live-data reconciliation before setting the `PORTFOLIO_BACKEND`
   cutover flag. **Do not point at AiAutoUpdate's channel database.**
3. Set `MSK_DURABLE_UPDATES_ENABLED=true` only with a validated private
   staging ledger, check update duplicates, failed/inflight reconciliation
   and Bot API receipt readbacks. Empty/unknown subscriptions receive no
   premium entitlements automatically; payment/paid-access policy remains
   separately governed and disabled.
4. Conduct a **private Telegram** end-to-end run for every actual button,
   `/buy`, `/sell`, portfolio restart, Analysis, Fundamental, Technical,
   100/150/250 scanner, chart, report/sector PDF and callbacks. Save actual
   `message_id`, response source and output-quality verdict; a fake/mock
   Bot API receipt is not a real customer delivery.
5. Reconcile real FY and quarterly ROE/EPS/revenue/D-E for representative
   NSE symbols to dated issuer filings and consolidated/standalone scope.
   Production research PDF must pass factual and page-by-page visual QA.
6. Compare/reconcile overlapping PRs #15, #63/#64, #77, #79, #91/#104 and
   #94; retain separate product projects, and use a single exact-SHA release
   candidate. Update the operator rollback plan, monitor `/health/ready`
   and root readiness, confirm token safety, then deploy **only after**
   full acceptance gates pass.

### Truthful completion labels

- **Code repaired & CI green:** exact code paths with deterministic or
  isolated synthetic evidence only.
- **Private staging accepted:** actual bot replies, readbacks and isolated
  restore recorded at a specific SHA.
- **Production accepted:** original customer records preserved and reconciled,
  live messages verified and rollback demonstrated. **Not yet established.**

A GitHub PR CI result must NEVER by itself set
`production_cutover_approved=true` or imply a 9/10 live accuracy score.
