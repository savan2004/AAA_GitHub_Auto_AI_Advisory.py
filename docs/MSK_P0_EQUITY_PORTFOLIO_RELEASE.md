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
