# MSK Bot — Overnight Full NSE Index Research

## Scope and hard rules
This is **MSK Telegram Bot** only, not AiAutoUpdate channel or MSK Web Advisory. Bot buttons retain their labels:
- **Large Cap Top 5** → official Nifty 100 (100 expected members)
- **Mid Cap Top 5** → official Nifty Midcap 150 (150 expected members)
- **Small Cap Top 5** → official Nifty Smallcap 250 (250 expected members)

We record **EVERY member** returned by the official constituent CSV, even if price/financial information is missing. An index may have fewer members than its name on a particular date; the official count is recorded rather than inventing membership. No >28 shortcut is used by the new report pipeline.

## IST collection and release timetable

| Stage | IST | Action |
|---|---:|---|
| Indian equity market close | 15:30 | Completed session intended for report |
| First collection | 16:13 Mon–Fri | Get NSE index members, attempt all company fundamentals, price histories |
| Source retry | 22:13 Mon–Fri | Resume incomplete rows; TejHQ EOD becomes eligible after its evening publication |
| Final retry | 05:13 Mon–Sat | Refresh missing financial/price evidence; **1.35-hour budget** |
| Finalize and save | 07:13 Mon–Sat | Require **all members attempted** and at least **90%** same-session price coverage |
| Bot user taps button | ~08:00 onward | Read cached prepared report from Neon; zero 500-stock scans on request |

A GH scheduled run is **best effort**: delayed/dropped events are possible. An 8:00 deadline cannot be honestly guaranteed without independent reliable scheduling/monitoring. Financials can remain missing even when an attempt was made.

## Free data, quality and limitations
- NSE official constituent CSV from `index_constituents.py`.
- Yahoo/yfinance bulk 6-month historical OHLCV via existing `market_data.bulk_history`.
- After evening release, TejHQ public keyless, raw NSE bhavcopy EOD fallback **only** for missing/stale symbols; exchange+symbol+date+OHLC validation. TejHQ publishes around 20:30 IST; free-source use and commercial redistribution rights require separate verification.
- Existing free-source `top5_company_ratings.fetch_company_evidence` uses `fundamentals.get_fundamentals` + `research_data.get_statements`. Aggregate data are **not audited issuer filings**; validate D/E units and EPS (separate pending PR #77).
- Records distinguish `fund_attempted`, `fund_status`, latest `technical.asof`, `price_source`, `price_reason`, `fund_attempts`, industry, membership hash and source date.
- Only `VALIDATED` **existing rule-gate** fundamentals with complete same-session technicals, risk under profile stop cap and `CONDITIONAL BUY` are considered for conditional watchlist. No preliminary fundamental score is converted to a final ranking.
- `READY` means **all members had a fundamental source review attempt** and >=90% fresh EOD price histories, not that all 500 companies passed auditor-grade fundamental verification. A report explicitly displays evidence coverage and missing reasons.
- `PARTIAL` withholds top picks; stale previous-session data are not displayed as the current morning report.

## Storage
Dedicated separate Neon Free project **MSK-Index-Overnight-Research**, ID `blue-truth-79769718`, Singapore, database `msk_index_research`. Tables:
- `msk_index_evidence_v1`: per session/profile/symbol stock analysis, status, reasons, source
- `msk_index_reports_v1`: one prepared per-session/profile watchlist, state and audit counts

Use `MSK_INDEX_DATABASE_URL` privately in **GitHub Actions repository Secret** and in **Render MSK Bot environment** (not in source code, PR, logs or documents). The GitHub connector currently exposes no secret-write action, so this configuration remains a release prerequisite. Never paste the password in chat.

## Release steps (before claiming LIVE)
1. Review/merge financial data-quality PR #77, reconcile dependent tests.
2. Review and merge overnight PR #79; only GitHub's **default main branch** runs scheduled actions.
3. Set protected `MSK_INDEX_DATABASE_URL` secret in GitHub Actions and same key on Render Bot, using Neon project's connection URL. Keep database URL out of code and public logs.
4. Optionally set `MSK_INDEX_PREPARED_ONLY=1` on Render Bot (default is enabled in source).
5. Manually run `MSK Overnight Full Index EOD Pipeline` **collect** across all three profiles; wait for source updates and retry. Then run **finalize**.
6. Query Neon rows: expected 100/150/250 (actual official membership recorded), `fund_attempted`, `price_fresh`, `PARTIAL/READY`.
7. Only after a GitHub Actions run succeeds and Render serves the merged commit, test all 3 Telegram buttons. No automatic posting to a separate channel is included.
8. Add a 07:45 IST independent readiness monitor/alert if the 8 AM deadline needs an operational SLA.

### Manual developer commands
```bash
python -m unittest discover -s tests -p 'test_overnight_index_reports.py' -v
# local-only for development, NOT shared production:
MSK_INDEX_DATABASE_URL=sqlite:////tmp/msk-index-test.db python -m overnight_index_reports status --profile moderate
# actual collection (will make free external calls), only with durable DB configured:
python -m overnight_index_reports run --profile all
python -m overnight_index_reports finalize --profile all
```

## Quality checklist
- [x] Official 100, 150, 250 membership integration
- [x] Iterate every company rather than 28
- [x] Resumable company-by-company ledger and source failure reasons
- [x] Date-correct historical prices, alternate free EOD, no fake OHLCV
- [x] Evidence gates reject preliminary Top 5 and outsized stops
- [x] Bot buttons use prepared snapshots by default
- [x] Unit tests with all 500 synthetic company attempts
- [x] Neon schema initialized
- [ ] GitHub repository secret configured and overnight scheduled workflow merged
- [ ] Actual live 500-company collection and verified per-profile coverage
- [ ] Render bot deployed to merged SHA, actual three-button Telegram checks
- [ ] Demonstrated 8 AM completion over multiple real market sessions
