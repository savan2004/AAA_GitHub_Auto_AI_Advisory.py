# MSK AI Advisor — release gates

This is a research **prototype**, not a SEBI-registered investment advisory service.

## Current scope
- Opt-in Telegram button and `/ask` route.
- One user question per LLM call, no conversation or portfolio sent.
- Financial education; no verified live market feed connected.
- Source-aware evidence contract; rejects stale, nonfinite, invalid-domain and unattributed snapshots.
- Prompt-level rules plus basic output checks; neither guarantees factual accuracy or compliance.
- **Disabled by default:** `MSK_AI_ADVISOR_ENABLED` must be explicitly set to `true` after review.
- No additional paid provider credentials or deployments are authorized by this PR.

## Release checklist (all required)
- [ ] GitHub CI green, including agent-specific tests.
- [ ] Independent review of input/output filters, privacy and data provenance.
- [ ] Source retrieval integrated using trusted, timestamped primary disclosures; verify exact document identity and source provenance.
- [ ] At least 100 benchmark questions with independently checked answers, bilingual cases, stale feeds, contradictory data, prompt injection, finance compliance and numerical hallucination cases.
- [ ] Define pass criteria before scoring: >=95% supported factual claims, zero fabricated live prices/quotes and zero unauthorized personalized trade instructions in evaluation set.
- [ ] Measure latency, rate limiting, provider costs and failure recovery; approve spending caps before enabling paid APIs.
- [ ] Private Telegram end-to-end tests with consent and no personal account data.
- [ ] Security, regulatory and user-facing wording reviewed before public launch.
- [ ] Human-approved merge and deployment, then staged monitoring.

## Rating
An 8/10 score is a **target**, not a measured result. Until these gates pass, do not label the assistant production-ready or advertise it as an investment adviser.
