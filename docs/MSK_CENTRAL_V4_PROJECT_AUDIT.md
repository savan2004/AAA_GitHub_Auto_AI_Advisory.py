# MSK V4 project-wide migration checklist

Read-only inventory at main 5a45db4217744e2aaff899e130e77332d3a39355:
365 tree entries, 261 Python files including tests, 59 workflow files.
19 open PRs including #300. This is an inventory and focused source audit,
not a full line-by-line review or proof of every production path.

## Findings and ownership
- Stocks Buzz: 1000 target, minimum 800. Migrate count/coverage/captions/
  snapshot keys together; avoid silently relabeling an incomplete scan.
- Index lists: index_constituents, smart_chart_universe, universe, Builder
  shortlist overlap; use official membership per index and canonical ISIN.
- Daily/history: market_data, data_engine, smart_chart_eod_recovery,
  portfolio_doctor_bhavcopy, research_pdf_fresh_history and overnight sources
  have overlapping fallback logic. Reuse adapters without deleting existing
  production methods. NSE final archive shadow candidate adapter added here.
- Fundamentals: fundamentals, research_data, fundamental_pipeline,
  nifty500_collector and standalone collector overlap. Route through a
  field-level evidence store; verified filing receipts first.
- Top 5 / Smart Chart / Stocks Buzz / Market Health / Intraday / Swing:
  consume shared daily evidence and separate completed intraday data.
- Research PDF / Doctor / Builder: use shared public issuer data, never
  persist private holdings in a public cache. Keep PDF quality, Telegram
  and investment qualification gates intact.
- PR #282 incremental history: open, evaluate separately in shadow before
  enabling. #233 research bank: open, needs independent release evidence.
- #117 index recovery and #215 on-demand intraday: open; check compatibility
  and tests before porting. #299/#286 Doctor layout/report, #243 signal journal
  and older feature drafts are not fixed by central data work. Magazine
  #265/#279/#280 and website #277 remain independent.
- Prior #300 head 6fcaccd: startup, breadth and owner acceptance succeeded;
  explicit live integration skipped; TA-Lib workflow failed with no jobs
  returned by the API. Do not bypass it or claim all CI passed.

## Current safeguards
- Bounded process-local verified evidence cache, TTL and revision invalidation,
  in-flight coalescing, capacity and concurrency limits.
- Provider circuit breaker with cooldown and single recovery probe.
- AI lead receipt URL must match the requested lead; malformed verifier results
  cannot become evidence. Conflicts are withheld.
- NSE shadow adapter makes one logical archive request for the admitted set,
  validates row shape and integral volume; candidates are NOT verified
  publication receipts until exact source bytes/URL, ISIN and session are proven.
- Separate offline safety workflow, no provider/secret/Neon/Telegram use.

## Remaining release gates
1. Official roster and persistent 700 cap with atomic admissions; actual
   market-calendar and issuer mapping validation.
2. Authorized data transports with hard deadlines and retry budgets; official
   source receipt capture and field-level parsing. No AI numbers.
3. Durable shadow storage, unique constraints and atomic upsert; retention,
   source revision lineage and incremental history; measured Neon budget.
4. Compare actual 500-stock market data against official ground truth and
   verified technical/fundamental computations. Measure coverage, peak RAM,
   error recovery and latency; targeted live test must never send Telegram.
5. Migrate consumers one at a time under default-off gates, independent
   regression, rollback and isolated delivery proof. Do not merge/deploy
   until approved. No production config changes or paid services.
