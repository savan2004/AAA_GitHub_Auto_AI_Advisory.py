# MSK Bot — Six-Hour Work Command Book
**Target:** 19 Nov 2026 lean MVP. **Mode:** prioritized engineering instructions, not a promise of continuous background execution.

## Global instructions for any development agent
Work only on the stated GitHub task. Inspect actual code and current CI before changing anything. Make the smallest safe patch on a feature branch. Add tests that fail before the fix and pass afterward. Do not invent runtime results, financial market data, or test outcomes. Never expose tokens or credentials. Never merge or deploy: obtain human approval. Don't change Render settings, .github/workflows, billing, secret material, or live trading behavior. If blocked, report exactly why and move to the next independent safe task. Work sequentially, not concurrently.

## Hour 0–1 | Baseline and PR review
- Inspect main branch, PR #7 (Monthly Outlook + futures timeout), PR #8 (Codex automation), current CI and Render deployed commit.
- Check risk of webhook failure, scanner futures unfinished, outstanding security exposures, and automation permission boundaries.
- Produce a concise evidence-based baseline report. Do not merge PRs.
**Done when:** baseline contains commit SHAs, CI conclusions, production SHA, top 3 defects, explicit unverified items.

## Hour 1–2 | Fix highest-impact scanner issue
- Review PR #7's bounded future wait logic and exceptional futures; validate partial results, cleanup and no request-wide crash.
- Add or improve deterministic tests for slow, failed, and successful jobs; ensure the API is responsive.
**Done when:** tests verify partial results, timeouts are bounded, and errors are surfaced without crash.

## Hour 2–3 | Monthly Outlook archive
- Review existing /outlook command and Monthly Outlook button in PR #7; ensure a single approved edition for any month and deterministic repeat access.
- Handle missing report, corrupt data, month rollover, and unapproved edition. Do not fabricate a report or silently use last month's data.
**Done when:** test suite covers those cases and documents publication process.

## Hour 3–4 | Portfolio durability and quality
- Audit live portfolio write path, Render ephemeral storage risk, data validation, and lock/concurrent access safety.
- Draft minimal durable-store implementation or PR with safe migration and backup strategy; avoid real data migration without backup/approval.
**Done when:** storage design and tests are documented; no production data deleted.

## Hour 4–5 | Breadth and fundamental signals
- Review breadth data universe, timestamp, missing constituent behavior, sector weighting, fallback logic, and fundamentals freshness.
- Fix one reproducible correctness issue; show confidence/data-source and as-of timestamps.
**Done when:** issue-specific test and reproducible before/after result exist.

## Hour 5–6 | Release readiness and handoff
- Review all open work and CI; prepare human-reviewable PR(s), summarize verified results, remaining defects, and release risks.
- Write 24-hour next actions and next 25-day milestone priorities.
**Done when:** status report lists PR URLs, test runs, commits, what's NOT deployed, blockers and required approvals.

## Six-hour success threshold
1. PR #7 reviewed with scanner+outlook tests green, or a concrete failing test and repair PR.
2. One additional narrowly scoped correctness/storage improvement with tests, if time allows.
3. Clear handoff with no unapproved merges, API spending, or deployment.

## Automation reality
The five-minute GitHub workflow from PR #8 is **not active while PR #8 remains unmerged**. Its selector requires task labels and explicit API activation; scheduled paid attempts are limited to the configured UTC slot. This book does not automatically start six hours of coding. Daily and six-hour monitoring are separate scheduled tasks, not continuous development.
