# Portfolio report completion brief

Reviewed: 19 November 2026 (IST).
Code baseline: `73fee686cff6163da76b714bb952fad907b27520`.
Scope: MSK Bot private Portfolio Doctor and Portfolio Audit only.
Status: four-role AI review and implementation brief; no production repair or new financial verification claimed.
The roles below are AI review perspectives, not real client interviews, licensed advice, or independent professional sign-off.

## Decision

Reuse the existing strict input, in-memory worker, evidence normalization, research parsers and Audit analytics. Complete their data contracts and validate the actual private worker before changing the customer report route. Do not build another parallel report template.

A longer PDF is not evidence of a more complete audit. No 8/10 readiness claim until a real authorized private delivery is inspected.

## Four-role discussion

| Role | Question / finding | Agreed instruction |
|---|---|---|
| Shareholder/client perspective | Can I distinguish unrealised gain against entered cost from a reconstructed historical path? Why are the peer set and benchmark incomplete? | Separate return definitions, show meaningful comparable businesses, explain flags and missing evidence in plain language. Use the same input when comparing report versions. |
| Company analyst perspective | Useful annual/quarterly/ownership parsers already exist, but the Audit adapter cannot receive some of their rows and loses evidence metadata. Broad sector labels do not establish direct competitors. | Preserve dated financial facts end-to-end. Use business-specific metrics. Missing or conflicting evidence must remain missing, never zero or fabricated. |
| Coding expert perspective | There are two report paths, a conflicting timeout contract, chart-window and financial-comparability defects, and an unproven fresh-worker chart initialization path. | Test the actual entry point, source adapters, comparisons, time budgets and read-only worker. Reuse pure calculations; do not blindly import an entire legacy pipeline. |
| Project lead perspective | Repeated report rewrites create competing outputs. Historical features include useful calculations and unsafe/dormant components. | One implementation owner, one shared evidence model, one acceptance record, explicit rollback. Repair plumbing before presentation, then test release evidence. |

Client reviewer challenged the initial technical proposal: route visibility and data fields alone are insufficient. The final PDF must actually contain dated evidence, a relevant peer set, aligned benchmark curves and understandable observations. An empty 14-page report would still fail acceptance.

## Important route finding

- `main.py:portfolio_doctor_start` explicitly clears pending Audit mode. Doctor menu / `/doctor` invokes the existing report.
- `main.py:portfolio_audit_start` sets the expiring Audit mode for `/audit`.
- `portfolio_doctor_live.py:worker_main` and `portfolio_audit_bridge.py:build_verified_audit` implement the separate audit worker path.
- A two-holding Doctor output can contain four main pages, one historical-risk page and two company pages: seven pages is expected for that renderer.
- PR #198 deliberately introduced the separate 10-14-page Audit canary; PR #199 repaired the worker signature. Neither replaced the Doctor menu.
- The reviewed attachment uses the Doctor renderer. Its title/page count alone does not establish which command was originally attempted.
- Give future output an explicit report mode/version and market as-of date so the two products cannot be confused.

## Code to reuse

All current file references below are pinned to the reviewed baseline.

| File / function | Useful capability | Conditions |
|---|---|---|
| `portfolio_doctor_input.py:decode_holdings` | Strict plain/numbered rows, exchange identity and duplicate checks | Preserve private-input routing and current limits |
| `portfolio_doctor_calculations.py:position_values`; `portfolio_doctor_report.py:_report_records` | Decimal cost/value/P&L reconciliation and all-holding validation | Current share counts/costs are client input; no inferred transactions |
| `portfolio_doctor_advanced.py:normalize_fundamentals`, `_screener_same_page_evidence` | Same-page statement scope and per-metric date/basis/unit metadata | Secondary source remains secondary; undated ratios cannot become verified |
| `research_data.py:parse_years`, `parse_quarters`, `parse_shareholding`, `freshness` | Annual, quarterly and ownership parsing plus period freshness | Adapt pure parsers into the privacy-safe collector; preserve source/scope |
| `peer_groups.py:resolve` | Candidate business families including Bank/HFC/Microfinance/NBFC | Verify current membership and business comparability; curated lists are candidates |
| `research_engine.py:compute_fundamentals` | Growth, margins, cash conversion and comparable peer calculations | Reuse calculations only, not scores, upside or trade plans |
| `fundamentals.py:get_fundamentals` | Unit handling and field-level provider origins | Mixed-source/undated values need a common evidence contract |
| `portfolio_doctor_risk_history.py:calculate_historical_risk` | Observed-date portfolio volatility, drawdown, beta and correlations | Reconstructed current holdings, not actual investor performance |
| `portfolio_audit_report.py:_risk_metrics` | Two benchmarks, rolling volatility, covariance contributions, VaR/ES, monthly observations and stress arithmetic | Align actual dates and adjustment basis; explain model assumptions |
| `portfolio_audit_report.py:create_report`; `portfolio_audit_bridge.py:audited_data` | Existing richer model/PDF and verified-price bridge | Supply real data through repaired adapters; test actual read-only worker |
| `report_generator.py`, `report_quarterly_charts.py` | Presentation and company-report chart ideas | Reuse selected RAM-compatible helpers; do not import file output or advice sections |

## Historical recovery assessment

- PR #176/#177: useful dated metric contracts, statement-scope matching and lender applicability already live in Advanced Doctor.
- PR #178: cash/whole-share scenario arithmetic is optional; it is not a prerequisite for completing the factual audit.
- PR #186: retired runner, ledger and old report were removed after reference review. Do not restore saved holdings, a file-backed ledger or stale invocation paths.
- PR #195: historical risk was already recovered into the stateless module; no need to resurrect `portfolio_report.py`.
- PR #189: superseded/closed design draft; do not reopen or merge a competing report.
- PR #198/#199: richer Audit path exists and should be evaluated rather than rewritten from scratch.
- Dormant `evaluate_opinion` labels and replacement/personalized action logic are unsuitable for this educational report.

## Confirmed defects and integration gaps

| ID | Evidence | Consequence / required repair |
|---|---|---|
| D1 | `MSKRepoProvider.fundamentals` reads `raw["quarterly"]` and `raw["ownership"]`; `normalize_fundamentals` returns neither | Default Audit quarterly/ownership arrays stay empty. Preserve validated rows and explicitly map parser schemas. Same-page collector must also call the ownership parser. |
| D2 | `MSKRepoProvider.fundamentals` drops metric scope/unit/basis; source document URL is not retained | Preserve provenance through every adapter and renderer. Retrieval time is not a fiscal period. |
| D3 | `audited_data` gives fundamentals 3 seconds; collector allows 9 seconds per scope, potentially two scopes, within a shared 58-second budget | Valid slow sources can be cut off; later holdings can lose enrichment. Allocate budgets fairly and report timeouts separately from absent data. |
| D4 | Doctor peers samples eight candidates per broad industry; bank/broker businesses can share Financial Services | Use verified business/sub-industry comparability before technical screening. Disclose eligible/attempted/fresh/rejected/qualified counts. |
| D5 | Audit `_financial_flags` compares CFO/profit without equal fiscal period and scope; industrial rules lack bank applicability | Require same-period/scope operands and explicit applicable/not-applicable status. Add bank/industrial fixtures. |
| D6 | Audit `_make_page5` takes `.tail(260)` after the full comparison was rebased | Visible window can fail its start=100 label. Slice the aligned window first, then rebase all displayed curves. |
| D7 | Legacy historical-risk renderer plots Nifty only when every holding date matches, although beta uses a valid inner join | Use a separately disclosed common-date chart window or explain why the line is absent. Never forward-fill prices. |
| D8 | Bridge supplies broad industries, empty market-cap buckets and empty events | These sections require genuine new metadata/filing feeds; page templates cannot invent them. |
| D9 | Audit route is described as owner-only but shares `_doctor_allowed`, including the general private-customer flag | No current access leak established. Before broad Doctor enablement, enforce the intended separate canary entitlement. |

Needs verification, not yet a confirmed runtime defect: can a fresh isolated Audit worker initialize Matplotlib/fonts after the read-only guard is installed? Existing mocked subprocess/in-process tests do not establish this.

## Required evidence contract

Every financial fact should carry:

- Canonical security identity: symbol, exchange, ISIN and issuer name where verified.
- Business model/sub-industry, classification source and date.
- Metric key, numeric value or null, unit and applicability.
- Reporting period start/end and FY/quarter/TTM/point-in-time basis.
- Consolidated/standalone scope.
- Exact source URL/document identity, publication date where known, retrieval timestamp.
- Verification state: issuer-reconciled, secondary, missing or conflict.
- For derived figures: formula and input fact IDs.
- Missing/rejection reason, including timeout, stale source, scope conflict or insufficient history.

Never assign a guessed period to an undated vendor ratio. Recompute ratios only from sourced comparable operands and a stated price date.

## Additional useful data, ranked by value

1. Four or more comparable annual periods and five or more quarterly periods for year-on-year analysis; dated ownership history where available.
2. Bank-specific: NII/NIM, ROA/ROE, advances/deposits growth, GNPA/NNPA, provisioning, capital adequacy, CASA and dated book-value evidence. No reviewed collector currently supplies all these.
3. Industrial/capital-goods: revenue/profit/margins, ROCE, interest coverage, operating cash flow, receivables/working capital, and sourced order-book/execution/segment disclosures where applicable.
4. Business-comparable peer facts for the held company and a small explained peer set, on matched periods and scope.
5. Corporate-action, dividend and event evidence with explicit price/total-return definitions.
6. Two-year histories and Nifty 50/500 comparison on matching observed dates, with actual received coverage.
7. Client transaction dates/cash flows only if actual holding-period or cash-flow-aware returns are requested. Fees/dividends/tax inputs are needed before claiming net or total return. A factual audit must still work without a lengthy questionnaire.

## Implementation order and release gates

### P0: correct route and evidence transport

- Report mode/version visible; Doctor/Audit routing and rollback tested.
- Quarterly/ownership rows reach the model and PDF from real source schemas.
- Scope/unit/basis/source URL survive transport.
- Same-period financial comparisons; bank-specific applicability.
- Fair bounded collection with timeout/absence reasons for every holding.

### P1: correct analytical presentation

- Slice then rebase same-date benchmark windows; display both indices when valid.
- Separate gain against entered cost from reconstructed window change.
- Peer model based on comparable businesses, with denominator and rejection counts.
- Explain flags as observation, calculation, relevance and limitation.
- Show gain contribution separately from value weight and covariance-based risk contribution.

### P2: verified data enrichment and readable output

- Prepare dated public reference data separately where useful; do not fetch 166 peers serially inside a 150-second private worker.
- No customer holdings enter shared metadata preparation.
- Add dated company interpretation and a source appendix.
- Readable tables/charts and content-led page count; do not pad to a target length.

### Completion requires

- Deterministic tests for full/partial/missing/stale/conflicting evidence, financial scope and bank applicability.
- All holdings included; totals and percentages reconcile.
- Actual fresh isolated Audit subprocess under read-only guard; no saved holdings/PDFs/cache artifacts.
- Every rendered page visually inspected and important content/source labels checked.
- Passing CI at exact SHA, exact Render LIVE SHA, authorized private Telegram delivery and PDF receipt/content.
- Synthetic fixtures labelled as tests, never production evidence.
- No BUY/SELL/ADD/EXIT, implied upside, target allocation, automated orders or numerical health score in the educational audit.

## Source links

Baseline source directory: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/tree/73fee686cff6163da76b714bb952fad907b27520
Current report routing: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/main.py
Audit adapter: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/portfolio_audit_bridge.py
Audit generator/provider: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/portfolio_audit_report.py
Financial collector: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/portfolio_doctor_advanced.py
Research parsers: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/research_data.py
Peer selection: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/portfolio_doctor_peers.py
Historical risk: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/73fee686cff6163da76b714bb952fad907b27520/portfolio_doctor_risk_history.py
Historical audit deployment: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/198
Worker compatibility repair: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/199

This brief deliberately contains no customer quantities, costs, Telegram IDs or account details.
