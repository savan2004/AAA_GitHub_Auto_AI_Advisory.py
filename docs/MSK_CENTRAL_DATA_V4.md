# MSK Central Data Engine V4 — isolated foundation

Status: draft, not integrated or deployed. MSK Bot only. No AiAutoUpdate edits.
Owner limits: 500 official Nifty 500 equities for regular collection; maximum
200 additional unique individually requested equities, 700 total. Extras are
admitted only after official symbol/ISIN verification in the future adapter.
Indices/benchmarks are separate non-equity instruments, not extra stock slots.

## Phase 1 (this PR)
- Pure universe and missing-work planner; exact 500 core membership count,
  canonical NSE symbols, deduplication, fail-closed 700 limit and 50-symbol batches.
- Synthetic contract tests. No changes to existing modules, environment,
  workflows, schedulers, PostgreSQL, Telegram or Render.
- No actual data collection, verification of issuer identity, persistent cap,
  provider calls, technical calculations, AI calls or runtime migration yet.

## Phase 2 (separate reviewed changes)
1. Use official current Nifty 500 membership (existing smart_chart_universe.py);
   verify symbol/ISIN and session, reconcile corporate actions/renames, retain
   last verified roster with explicit as-of and fail closed on stale evidence.
   Derive Top 5 index membership from official separate lists, intersect with
   the core universe; never invent index membership.
2. Reuse data_engine.py, market_data.py, portfolio_doctor_bhavcopy.py,
   smart_chart_eod_recovery.py and the verified portions of #274/#281/#289/#292.
   Fetch final NSE UDiFF archive once per completed session; parse bounded
   batches, verify exact session/symbol/raw OHLCV, recover missing history with
   authorized Yahoo or other approved sources only. Never splice adjusted/raw
   prices or treat a daily close as live intraday.
3. Reuse fundamentals.py, research_data.py and research_filing_evidence.py.
   Prefer dated exchange/issuer filings for financial statements, quarterly
   results, shareholding and actions. Source scope, consolidated/standalone,
   reporting period, currency/unit and field receipt are mandatory.
   Monthly review plus new filings; derive PE/PB and technicals from verified
   inputs rather than downloading derived indicators independently.
4. Existing AI Engine V3 and msk_ai_data_recovery.py may discover original
   filing links for missing fields. AI output never becomes a numerical record.
   No paid fallback, no new libraries without review, verify source usage rights.
5. Store canonical per-symbol/ISIN/dataset/period/price-basis records and source
   receipts in a separately approved shadow datastore first. Persistent atomic
   uniqueness/admission control, bounded concurrency, idempotent writes,
   freshness calendar, provenance, explicit missing/withheld status, retry
   budgets and retention require dedicated tests. Measure Neon use; no new paid
   service. Do not store private holdings in a public collection.
6. Compare shadow output against current production. Move one consumer at a
   time behind a default-off feature gate. Stocks Buzz currently targets 1000
   with an 800 minimum; migrate its coverage gates, captions, cache keys,
   freshness contracts and snapshot namespaces together only after proof.
   Preserve Top 5, Smart Chart, Stocks Buzz, Research PDF, Doctor and Builder
   behavior until each independent acceptance gate passes.
7. Require CI on exact commit, real-data 500-member coverage with explicit
   missing fields, cap tests including concurrent extra admissions, provider
   failure tests, memory and Neon measurement, rollback test, and isolated
   delivery proof before any activation. No production deployment in this PR.

## Offline test command
`python -m unittest discover -s tests -p test_msk_central_data_v4.py -v`

No API calls or additional dependencies are required for these tests.
