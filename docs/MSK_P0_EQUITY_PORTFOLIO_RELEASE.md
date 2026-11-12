# MSK Bot P0 — Equity-data and holdings-safety release gates

Scope LIMITED to Indian equity market data and portfolio safety. Payment gateway,
subscriptions, other products and AiAutoUpdate are excluded.

## Defects and development mitigations

- Yahoo HTTP 401 / missing quotes: reject zero, NaN, negative and corrupt numbers.
  Preserve NSE index tickers. Use a bounded (maximum 12 per bulk request) fallback to
  NSE historical equity and direct chart data, never invented candles. This does
  NOT guarantee that provider-side HTTP 401 can be bypassed.
- JSON holdings: commit with a temporary file, fsync, and atomic replace; a failed
  save does not modify in-memory holdings or tell Telegram that it succeeded.
  Invalid JSON must fail startup rather than silently clearing client data.
- Portfolio P&L: an unavailable price is explicitly unavailable. The bot excludes
  that position from labelled PARTIAL P&L instead of reporting a false zero.
- PostgreSQL: a staged optional backend requires a verified migration marker and
  operator-approved cutover. Default legacy JSON is NOT durable on Render ephemeral
  storage even with atomic writes.

## Dated quote provenance gate (draft branch only)

- Yahoo v8 chart `regularMarketTime` is interpreted as an absolute Unix UTC
  timestamp; NSE `metadata.lastUpdateTime` is parsed as IST exchange-local time.
  HTTP retrieval time and cache insertion time are **never** substituted for a
  missing provider timestamp. A mismatched reported ticker is rejected.
- A quote must have a positive finite price, accepted source, valid timestamp
  and no more than two minutes future clock skew. During nominal Monday-Friday
  **09:15–15:30 IST**, reject observations older than **30 minutes**; this
  tolerates delayed feeds but **does not claim real-time execution pricing**.
  Outside that window permit at most **four calendar days** of dated last-session
  observations, explicitly labelled `last_observed_session`. This weekday
  gate does not know NSE holidays: on holidays during nominal trading hours it
  may refuse a valid previous close rather than inaccurately label it current.
- Numeric-only legacy price caches are not trusted. Cached quotes are rechecked
  against their original provider timestamps on every read. A one-hour
  fundamentals cache is not permission to reuse a one-hour-old price.
- The Telegram portfolio now shows each priced holding's source and as-of time,
  distinguishing recent observations from last-session marks. Missing/undated
  quotes are excluded from P&L; displayed P&L remains indicative.
- `tests/pr94_quote_provenance_integration.py` adds a **read-only, explicit
  opt-in** external provider gate for SBIN, RELIANCE, TCS, Nifty and Bank Nifty.
  Requires 4/5 provenance-verified quotes including both indices, records an
  as-of timestamp per symbol in CI, and does not publish prices or send messages.
  It is not full-index coverage, an accuracy/backtest score, or customer-level
  production acceptance. Real provider rejection is an honest failed test.
- Future release gates: cross-check price and date against a second independent
  trusted exchange/market source, visually inspect Telegram output, validate
  any stale holiday handling, and confirm the exact deployed SHA and rollback.

## Fundamental debt/equity unit audit — 12 November 2026

- **Root cause:** Yahoo `debtToEquity` is percentage-scaled: a raw value
  `125` represents a **1.25x** debt/equity ratio, not `125x`.
  The previous Fundamental-button and advisory fallbacks displayed that
  unconverted number. The shared Yahoo adapter already provided an optional
  `debt_equity_ratio` field normalized by a factor of 0.01, but downstream
  consumers ignored it.
- The canonical `normalized_debt_equity_ratio` reads the already-normalized
  Yahoo field first, or divides Yahoo's raw `debtToEquity` by 100. Negative,
  boolean, nonfinite, and absent values remain unavailable; **zero is valid**.
  Primary Yahoo, yfinance fallback, the Fundamental button and the full
  advisory card now share this conversion. Ratios already supplied by Screener
  are not divided again.
- Four synthetic regression tests cover conversion, invalid/zero values,
  Yahoo primary and fallback paths, Screener values, and Telegram rendering.
  These tests are **unit-consistency evidence only**, not proof of company
  statement accuracy or independent reconciliation.
- Release acceptance still requires a dated, authoritative financial statement
  and a second-source comparison for the selected company's liabilities/equity,
  with matching consolidated/standalone scope and fiscal period. If source
  units or periods cannot be established, withhold a verified valuation claim.

## Migrated holdings replay and source-byte integrity — Issue #6

- **Previously unsafe in this integration PR:** the PostgreSQL cutover importer
  parsed `portfolio_data.json` and re-read the file later for its SHA-256
  marker. An in-between file change could bind that marker to different rows.
  It also returned `already-imported` when a checksum matched even if the
  database subsequently lost or altered positions.
- **Repair:** capture immutable source bytes exactly once; validate rows and
  hash the same buffer. After first import AND on a repeat of the original
  source, compare **every** stored (Telegram user, symbol, quantity, average
  cost) with the frozen snapshot. A matching SHA or count alone is not proof.
  A mismatch refuses verification with no silent repair or overwrite.
- Regression tests cover missing, additional, changed-quantity and changed-cost
  positions, source-file change between reads, mutable-byte refusal and invalid
  Telegram UID. A separate **isolated synthetic PostgreSQL 16** test alters a
  position after cutover and checks that replay is refused without changing it.
  No actual customer holdings or production database are touched.
- **Operational caution:** after legitimate post-cutover trades, the database
  must naturally differ from the original JSON. Never rerun the original
  migration importer to validate live positions after trading begins; use
  independent backups, audited current ledger data, and restore testing.
- **Unfinished real-data gate:** obtain the actual Render production
  `portfolio_data.json` through a secure approved operator mechanism, record
  digest and per-position baseline, retain an immutable encrypted off-host
  backup, and reconcile the complete approved DB migration/restore before
  deployment. CI synthetic tests are NOT customer migration acceptance.
- PR #15 independently contains related safety work but is based on older bot
  code; this PR consolidates the required migration integrity safeguards
  without merging outdated `main.py` handlers.

## Financial fiscal-period and consolidated/standalone disclosure gate — 12 November 2026

**Root causes discovered:** `fundamentals._fetch_screener()` previously
took the last numeric Sales row cell without mapping it to a P&L column
header. The last number is commonly **TTM**, not the latest full fiscal year;
it has no explicit fiscal-year end and cannot safely be labelled `FY`.
Additionally `get_fundamentals()` combines data_engine, Screener, Finnhub and
yfinance ratios without recording which source originally populated ROE,
EPS, revenue or debt/equity. A 404 on Screener's consolidated page silently
falls back to standalone results, causing scope mixing.

**Corrected (current draft PR #94 only):**

- `financial_quality.annual_sales_from_table()` pairs each P&L numeric
  column with its **matching header**; only explicit `Mar YYYY` fiscal-year
  revenue is used. TTM is never silently called FY. Misaligned headers,
  invalid/future/old dates, negative/invalid revenue and malformed rows fail
  closed (Revenue N/A from that source). Crores are converted to INR.
- Screener notes whether it fetched consolidated or standalone data,
  while each aggregate financial field records its actual **adapter**
  origin, scope if known, and field-specific revenue FY end if known.
  `data_engine (mixed)` is intentionally NOT attributed to Yahoo alone:
  that adapter itself can include Yahoo, NSE and Finnhub inputs.
- Both Fundamental and full Analysis Telegram cards clearly distinguish
  **secondary indicative data** from independent NSE issuer filings. Each
  available revenue, ROE, EPS and D/E field has a source/date/scope label.
  Unknown basis is explicit. Do not call a mixed-source value audited,
  independently verified or directly comparable.
- Historical `fund_` cache entries lacked lineage; versioned cache
  `fund_provenance_v1_` prevents old cached fields being mislabeled.
- Tests in `tests/test_msk_financial_provenance.py` use synthetic P&L
  tables, standalone fallback, TTM-last/old/future/non-numeric/misaligned
  cases, source-specific origin, Telegram disclosure and issuer
  verification refusal. These tests do NOT verify financial numbers for any
  actual company or certify the completeness of all provider HTML variants.

**Release gate still required for audited/verified metrics:** An authorized
reviewer must retrieve each chosen company's official issuer financial results
from NSE Integrated Filing — Financials (especially filings for periods ending
March 2025 onward), record symbol, quarter/year end, issuer broadcast/revision
time, *consolidated vs standalone* basis, audited/unaudited and exact statement
units. ROE, EPS, revenue and debt/equity must be independently reconciled to
those disclosures for the SAME scope and period, with a second-source check.
If the issuer filing is missing or a source period/scope cannot be established,
the bot must retain the **indicative/unverified** classification.

NSE official reference:
https://www.nseindia.com/companies-listing/corporate-integrated-filing

No market quote timestamp proves the **financial-statement reporting period**.
No real issuer filing was uploaded, signed, parsed or reconciled by this patch.
No paid provider, production data, Telegram message or Render deployment touched.

## Mandatory cutover steps BEFORE deployment

1. Freeze buy/sell, obtain the actual live portfolio_data.json through an approved
   secure operator process, and make an encrypted off-instance immutable snapshot.
   This file is not in GitHub; if it cannot be recovered, DO NOT initialize the
   target database as empty and claim a successful migration.
2. Record a digest and verify every user ID, symbol, quantity and cost basis.
   Configure a dedicated MSK-only managed PostgreSQL database with TLS, restricted
   credentials, backup and restore testing. NEVER reuse AiAutoUpdate data.
3. Dry-run operator-only importer against secure snapshot. After explicit approval,
   execute --apply against the MSK database and compare EVERY row in PostgreSQL.
   Save a separate database backup and prove restore in a staging database.
4. In a controlled Render release configure PORTFOLIO_BACKEND=postgres and
   PORTFOLIO_CUTOVER_APPROVED=true and PORTFOLIO_DATABASE_URL in protected environment
   settings. Without a verified marker the application must fail startup.
5. Verify staging /buy, /sell, /portfolio, no quote P&L, concurrent requests,
   and Telegram duplicated updates. Restart/redeploy and compare all persisted
   holdings. Keep rollback artifacts; deploy production only after acceptance.

## Market data acceptance

- Run isolated CI fixtures for malformed prices, NSE fallback and invalid candles.
- Reconcile historical symbols/renames with official exchange constituents.
  Yahoo missing-history warnings are NOT proof of delisting.
- Inspect Render 401 logs, data freshness, confirmed source timestamps and actual
  fallback success rate before claiming equity-data reliability.
- Never claim a live price is authoritative when timestamp/source is unknown.

## Verified vs not verified

Development CI can show code behavior on synthetic fixtures. It does not prove
production holdings preservation, real NSE endpoint access, Yahoo availability,
durable Postgres backup or Telegram delivery. Those require a controlled staging
cutover, live market-data evidence and owner acceptance.
