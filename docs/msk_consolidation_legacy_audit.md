# MSK Bot consolidation and legacy quarantine plan

Scope: ONLY savan2004/AAA_GitHub_Auto_AI_Advisory.py. Baseline main: 01a407d2ad9e369470102fe9ca7d4f29434fad6f.

## Strict rules
1. One final integration PR, based on current main. Do not merge outdated PR branches wholesale.
2. Preserve existing entrypoints, imports, routes, Telegram commands, schedulers, Render configuration and database compatibility.
3. Do not move/delete executable code until import/call-site/configuration references, tests and runtime entrypoints have been checked.
4. Quarantine only proven unused code, with original path, reason, references checked, rollback instructions and test evidence. Never move secrets, databases, logs, generated user files or active migrations.
5. Keep legacy code excluded from runtime import paths; preserve history in Git. Do not duplicate active modules under an importable archive package.
6. Run targeted and full tests, verify GitHub CI, then staging/live smoke tests before closing superseded PRs.

## PR consolidation matrix
- #88 + #89: market breadth error handling, Swing safety and truthful R:R; #90 Status removal is already merged and MUST NOT be reverted.
- #91 + #94: 10 distinct holdings, quote validation, fail-closed persistence, PostgreSQL migration and backup/restore. No cutover without reconciliation.
- #92: SMTP transport only; integrate after PDF, opt-in, recipient verification, idempotency and unsubscribe.
- #93: Monthly Outlook independent feature; require approved report and Telegram acceptance.

## Inventory and classification
For each file classify ACTIVE, SHARED, INCOMPLETE, UNUSED-CANDIDATE, GENERATED, or UNKNOWN. UNKNOWN stays in place.
Record references from Python imports, dynamic imports, __main__, subprocess calls, CLI, web routes, Telegram callbacks, cron/workflows, Render start commands, templates, test fixtures and README/config.
Archive candidates only after two-stage evidence: static reference search AND relevant integration tests. Use archive/legacy_review/ only for safe non-runtime artifacts.

## Release gates
- Baseline and post-change test comparison, no unexpected changes to unrelated code.
- No /status public handler or button regression.
- 10-holding limit, holdings reconciliation, missing-quote P&L, corruption/restart/restore tests.
- Email disabled by default until verified opt-in, unsubscribe and delivery ledger.
- Full CI green; no deployment or PR closure on speculative success.

## Current status
This is an audit/organization plan only. No production code moved or retired; candidate inventory and consolidated implementation remain pending.
