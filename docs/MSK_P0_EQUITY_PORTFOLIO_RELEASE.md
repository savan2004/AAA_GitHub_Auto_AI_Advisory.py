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
