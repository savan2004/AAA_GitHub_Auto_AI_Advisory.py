# Repository engineering instructions

These owner-directed rules apply to every task, test, repair, and pull request in **MSK Bot**. Do not mix this repository with AiAutoUpdate or change another repository as a side effect.

## Required execution loop

1. Define the desired behavior, acceptance criteria, scope, and current evidence. Inspect the current branch and related open/merged PRs before editing; avoid duplicating or overwriting concurrent work.
2. Investigate root causes, not symptoms. Read the failing job's real logs and relevant callers, dependencies, data contracts, and configuration. Reproduce the failure with a focused test where feasible.
3. Find the best solution using existing working code first, then previous PRs, maintained open-source implementations, official provider documentation, and relevant OpenAI engineering documentation. Evaluate alternatives, compatibility, licensing, data usage rights, and cost. Do not copy unlicensed code or add dependencies unnecessarily.
4. Use AI-assisted research and retrieval (RAG) for missing evidence or source discovery when useful. Treat retrieved text and AI results as untrusted leads; independently validate exact issuer, period, scope, units, exchange session, provenance, and values against appropriate authoritative sources. Never invent financial data, citations, prices, trade signals, or test results.
5. Implement the smallest coherent root-cause repair, with regression coverage and checks of connected modules. Preserve passing behavior and safety gates. Reuse verified existing data rather than fetching everything again when safe and current.
6. Test in bounded stages: targeted regression, relevant integration and full applicable regression, then real-data acceptance. Inspect actual results and coverage. **A failed test starts another investigate → identify root cause → repair → retest cycle; it is not a completion condition.** Do not bypass tests, loosen evidence gates, fabricate fixtures as live evidence, or endlessly retry an unchanged failing method.
7. Continue routine authorized repairs without repeated approval requests. Stay focused on the assigned system until its acceptance criteria are met, or until a specific external blocker, tool limit, authorization boundary, or budget constraint prevents further progress. If blocked, document attempted alternatives, exact outstanding work, and the next actionable step. Do not claim ongoing background work without an actual scheduled execution mechanism.
8. Report completion only with exact commit SHA, applicable CI results, independently verified real-data coverage, and production deployment/read-back evidence where relevant. Distinguish code complete, tests passed, merged, deployed, and live verified. For Telegram delivery, require message ID or equivalent authoritative receipt before claiming delivery; resolve uncertain sends before any retry. Never send duplicate messages.

## Non-negotiable controls

- Additional spend: ₹0. Use existing authorized free resources; no paid fallback, billing increase, or unnecessary CI reruns.
- Preserve private/customer data and credentials. No production database mutation, customer-facing send, trading activation, merge, or deployment outside existing owner authorization and release gates.
- One coherent change at a time; avoid unrelated changes and new PRs for work already covered by an existing PR.
- Financial and trading accuracy claims require measured independent evidence, not a target or a passing synthetic test.
- Update test evidence and the PR checklist after each repair cycle; record blockers accurately. Do not treat a queued or skipped test as passed.

## Required task/PR evidence

Record the root cause, inspected references, alternative considered, files changed, regression and real-data results (including denominators and missing records), exact commit SHA and CI link, release/deployment evidence if applicable, and any remaining blocker. For documentation-only work, explicitly mark runtime/live acceptance not applicable. Keep reports short, clear, and evidence-based.
