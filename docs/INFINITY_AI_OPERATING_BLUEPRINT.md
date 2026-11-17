# Infinity AI — MSK Bot Research & Advisory Blueprint
**Version:** 1.0 · **Established:** 2026-11-18 · **Owner:** savan2004 · **Companion system:** AiAutoUpdate

> **Mission:** Maintain a verifiable, useful Indian stock-market research and advisory-information system that can supply approved evidence-based reports to the continuously operating AiAutoUpdate publishing channel. Self-healing and periodic AI-assisted improvement must be *safe, bounded, and testable*, not uncontrolled code rewriting or unsupervised financial advice.

## 1. Nontechnical owner instructions become real work orders
The owner is not required to write specifications, diagnose errors or know code terms. Treat any practical request (e.g., "PDF not working", "improve stock AI", "start work") as authorization to scope and execute a normal engineering work order, under the standing project permissions.

**Task translation protocol:**
1. Identify the user-visible outcome, affected features and constraints.
2. Read current `main`, existing GitHub issues, PRs, tests and any available logs before inventing new work. Preserve valuable prior development.
3. Write measurable acceptance criteria and identify source-data/privacy/release hazards.
4. Select priority P0 (broken/security/data integrity), P1 (reliability/quality) or P2 (enhancement).
5. Implement on a narrow branch, add tests, verify results, and open a reviewable PR.
6. Report links and exact outcomes; never claim success from an issue, mock, PR description or unrun tests.

Ask the owner only for a truly indispensable decision, required artifact or protected live-action approval. Choose safe technical defaults without repeatedly demanding clarification.

## 2. Meaning of "START WORK"
"Start work" means **execute immediately within the active session**, not merely propose a plan:
- Select the named task or highest-priority safe unresolved work.
- Inspect → reproduce/measure → patch → test → inspect results → repeat while authorized and feasible.
- Do not stop just because the first test fails; investigate root cause, isolate failures and implement a tested fix.
- Do not say "100% done" unless *all applicable, observable acceptance criteria* have evidence. Keep unfinished or unverified work open.
- If CI or external infrastructure blocks tests, prove the blocker with run/job details, perform legitimate independent checks, and mark the CI-dependent release **BLOCKED**.
- Do not silently wait or promise that ChatGPT will keep coding after a chat ends. Unattended execution needs a separately configured, cost-bounded GitHub Actions/hosting automation with narrowly scoped privileges.
- At a genuine blocker, preserve code and context, record evidence and exactly what resumes the work; request owner input only if essential.

**No excuse** means no vague unsupported deflection. It does **not** authorize fabricated results, bypassing approval gates, infinite retries, security shortcuts or unbounded expense.

## 3. Evidence is the only definition of completion
Track each deliverable with the following proof:

| Requirement | Minimum verifiable receipt |
| --- | --- |
| Initial failure | Reproduction, source location and log/error class or baseline |
| Modification | Repo, branch, exact commit SHA, diff and rollback |
| Functional result | Deterministic regression tests, negative cases, actual command/outcome |
| CI | Exact candidate SHA, workflow URL and steps actually executed (not runner start failure) |
| Market/financial accuracy | Issuer/exchange/provider source, as-of session, statement scope, units, age and unavailable-field disclosures |
| PDF/Telegram | Real PDF integrity/readability evidence and genuine Telegram message ID, **only if authorized/observed** |
| Status | DONE / IN PROGRESS / BLOCKED / NOT VERIFIED, with remaining criteria explicitly named |

**100% completion** means all agreed acceptance criteria passed and any required owner signoff exists. It is not a claim of 100% permanent service uptime or 100% financial prediction accuracy.

## 4. Architecture: produce research, do not become a second publisher
- **MSK Bot (this repo)** owns evidence-based technical/fundamental research, stock scanning, Portfolio Doctor, generated PDFs, analyses and optional controlled on-demand Telegram replies.
- **AiAutoUpdate** (`savan2004/AiAutoUpdate`) owns scheduled channel publishing, calendar/delivery/idempotency, durable Telegram receipts, monitoring, bounded auto-recovery and publication-level governance.
- No duplicate channel publisher. MSK research may be handed off through a **reviewed versioned read-only feed or artifact**.
- Required artifact metadata: schema version, source identity, symbols/universe and coverage, exchange/trading-session timestamps in IST, financial statement scope/period, generated_at, expires_at, approved state, checksum/digest, validator version and explicit gaps.
- The receiving publisher must reject stale/ambiguous/missing/unapproved or corrupted artifacts rather than inventing or silently substituting a report.
- Never share Telegram tokens, database credentials, client holdings or other secrets across repositories. All external write integrations require separate authorization and review.

## 5. Financial integrity rules — non-negotiable
- No fabricated market prices, candles, company financials, ratios, future predictions, backtest performance, model accuracy or Telegram deliveries.
- Never blend incompatible financial periods, consolidated vs standalone reports, conflicting calendars, prices from different sessions or arbitrary providers into one implied source.
- Check market freshness relative to the **completed** NSE trading session; fail closed when coverage or quality gates are not met.
- Separate education/research from personalized investment instructions. No automatic order execution or unapproved customer-facing BUY/SELL/EXIT directives.
- Report uncertainty, missing sources, limited sample coverage and editorial approval honestly.
- Keep personal customer holdings ephemeral where designed; do not leak them through public issues, sample fixtures or logs.

## 6. Safe self-healing and auto-upgrade approach
**Self-heal only reversible operations:** transient source retries with backoff, bounded provider substitution with proven provenance, worker restart by approved supervisor and recovery of incomplete non-public computations. Circuit-break repeated failure; don't loop indefinitely.

**Self-upgrade through reviewable changes:** use existing `.github/workflows/msk-codex-autodev.yml` and focused task selection as candidate machinery. AI agents can propose code plus tests on separate branches and draft PRs, but do not self-merge, change credentials, deploy, modify protected production config, publish live financial reports or create a second scheduled Telegram publisher.

Keep flags OFF by default until fully verified and owner-approved. GitHub Actions schedules are best effort; persistent monitoring needs verified hosting/scheduling and real delivery receipts.

## 7. Current execution priorities
| Order | Work | Tracking |
| --- | --- | --- |
| P0-1 | Restore verified private Portfolio Doctor report path, completed-session prices and no-storage/duplicate guards | [#180](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/180), [PR #179](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/179) |
| P0-2 | Verify real market-data provenance/statement scope and PDF release gates; fix regressions | [#87](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/87), [#17](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/17) |
| P0-3 | Define approved versioned MSK → AiAutoUpdate research handoff, including honest stale/missing source behavior | [#181](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/181) |
| P1-1 | Repair website workflows only when actual deployed Chromium/UI behavior and data can be verified | [#53](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/53) |
| P1-2 | Complete magazine source, PDF quality and owner-approved Telegram delivery gates | [#96](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/issues/96) |
| P2-1 | Enable guarded AI code proposals only after deterministic tests and governance | `.github/workflows/msk-codex-autodev.yml` |

**Companion publisher program:** [AiAutoUpdate #204](https://github.com/savan2004/AiAutoUpdate/issues/204). Any work in AiAutoUpdate needs an explicit separate work order rather than unreviewed cross-repo modification.

## 8. Release safety
The owner authorizes routine source review, scoped branch development, edits/rewrites/refactors/deletions, tests, issues and PRs. Deletions must check dependencies and preserve Git rollback.

**Obtain explicit owner approval** before production deploy, public/channel publishing, destructive database operations, credential modifications, exposure of confidential customer data or activation of regulated/personalized financial-advice behavior.

Release readiness requires final candidate SHA tests, reviewed market-data lineage, privacy/security review, real acceptance receipts if applicable, documented rollback and explicit approval. An AI-generated report with no real data evidence cannot be released just because it looks polished.

## 9. Practical owner examples
- **"Start work"** → choose the highest-risk actionable issue, implement and test as far as possible now, hand off verifiable status.
- **"PDF wrong"** → identify exact PDF generator and sample; reproduce clipping/data defect, patch, add regression, inspect actual pages, report SHA and evidence.
- **"Auto channel not posting"** → first determine whether the failure belongs to AiAutoUpdate publisher or MSK research producer; never trigger an unapproved live post or blindly replay unknown sends.
- **"Improve AI"** → choose measurable functionality, design nonfabrication gates and focused tests, make a reviewed source patch; do not promise predictive accuracy.

## 10. Mandatory handoff
At the end of every active work session provide **completed evidence, remaining unverified requirements, verified blockers with action needed and the first next action**. No filler status; no unsupported 100% claims.

This blueprint documents how work is to be conducted. It does **not** mean all automation is implemented, monitoring is active or production publishing is approved.
