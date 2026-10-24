# Market participation and portfolio fundamental review

## User flows

- **Breadth** or `/breadth`: completed-session advances/declines, 20/50/200-day SMA participation, 252-session closing highs/lows, fixed-cohort five-session change and industry participation.
- **Portfolio** or `/portfolio`: completed-session valuation, correct current-value allocation and explicit missing-price coverage.
- **Portfolio Review** or `/portfolio_review`: a PDF review of saved holdings in private chat.
- `/portfolio_upload`: opt in, then send a CSV/XLSX in private chat within ten minutes. Columns: `Symbol,Qty,Buy Price`; NSE symbols, at most 50 rows, 1 MB. Duplicate lots are merged with a disclosed weighted cost; correct any parser issues and resubmit. Uploads do not replace saved holdings.
- Existing `/buy` and `/sell` remain available in private chat. Removing a holding no longer deadlocks the portfolio lock.

## Breadth contract

Current Nifty 500 membership comes from the NSE constituent CSV. If unavailable, use the existing built-in stock sample and label it explicitly; never call that sample full Nifty 500. Prices use adjusted daily OHLCV; other features retain unadjusted downloads. Adjustment mode is included in cache keys.

Before 16:00 IST, discard the current date's bar. After the buffer, use the latest available Nifty 50 session, including weekends/holidays. Require both exact benchmark sessions for advance/decline classification. Missing or stale constituents do not count as unchanged. An unchanged move is within 0.005% of the previous close.

Each moving-average/high-low calculation discloses its own eligible denominator. Highs/lows are 252-session **closing** extremes, not intraday extremes. Five-session participation change uses the same cohort across all six required closes. Current membership does not establish a historical constituent backtest.

Below 80% daily coverage, suppress broad directional conclusions. Sessions over seven calendar days old suppress a current-market interpretation. A built-in sample conclusion is labelled sample-only. Results are cached 15 minutes; one scan at a time prevents overlapping bulk jobs.

## Portfolio contract

One common completed benchmark session determines eligible prices. Unpriced holdings remain unpriced. Subset value, cost and P&L use the same set of holdings; total portfolio value is explicitly unknown when incomplete. Exposure percentages use current market value and disclose their priced denominator.

Every submitted holding receives an attempted basic fundamental review, even when price data is unavailable. Per-stock failures remain visibly unanalysed. The report reuses the existing fundamentals/financial-statement adapters, without invoking the broken old `holding_audit` / `assemble(holding=...)` interface. The detailed single-stock research menu remains available separately.

Business quality, growth, leverage/cash conversion, valuation and technical timing are displayed separately. No combined price-momentum score is presented as business quality. Fundamental conclusions require current annual/quarterly evidence; missing and stale data block a positive conclusion. Dated, consecutive annual periods (four for a three-year CAGR) and the observed metrics support a transparent review, not a return forecast.

Banks/NBFCs/financial businesses do not receive industrial leverage/cash-conversion conclusions. GNPA/NNPA, credit costs, ROA and capital adequacy are not supplied by the existing verified feed; their fundamental conclusion remains incomplete. Unclassified industries also remain incomplete. Verified pledge/auditor/governance data and historical valuation ranges are not yet integrated and are not inferred. No fabricated peer median, weighted portfolio P/E, price target or automatic trade instruction.

The 15% holding and 35% sector concentration levels are disclosed **review triggers**, not personalised target allocations. Fundamental review attempts and their value coverage are distinguished from sufficient verified evidence.

## Operations

One portfolio job runs at a time, in a request-specific temporary directory removed even after delivery failure. CSV/XLSX parsing is bounded and rejects nonfinite numbers. XLSX support adds `openpyxl==3.1.5`. New flows do not require extra secrets or paid AI calls.

Saved `/buy` holdings still use the existing local JSON path. Writes are atomic and `/sell` locking is corrected; durable holdings across host rebuilds still require persistent storage and are not claimed here. Upstream data providers can be unavailable/rate-limited; no live quote or feed SLA is claimed.

## Validation

Run from repository root:

```bash
python -m unittest -v test_product_analytics
TELEGRAM_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi WEBHOOK_URL= MPLBACKEND=Agg python -c 'import main; print("import ok")'
```

Product tests cover exact-session alignment, intraday exclusion, missing constituents, per-measure denominators, fixed cohorts, divergence, stale sessions, adjusted-cache separation, current-value weights, unknown prices, nonfinite values, stale/future statements, dated CAGR/quarter continuity, lender exclusions, duplicate lots/XLSX, all-holding analysis, failed-analysis handling, private delivery, invalid-upload rejection, temporary cleanup and the saved-holding deadlock.

The synthetic PDF was generated, rendered and visually inspected. No live Telegram messages or Render deployment were performed.

Baseline comparison: the existing reliability/fundamental-pipeline suite runs 45 tests with 11 failures, 15 errors and 1 skip on both the unchanged base and this branch; failing/error test identities are identical. Existing failures include missing research-result fields (`asof`, `completeness`, `eps_src`, etc.) and are outside this feature change. This is a review branch, not a claim that the repository-wide suite is green.
