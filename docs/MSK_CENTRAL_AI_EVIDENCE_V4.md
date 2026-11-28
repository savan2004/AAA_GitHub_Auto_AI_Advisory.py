# Evidence-first AI engineering for MSK V4 (shadow only)

ASI and IQ are not measurable product guarantees. Engineering objective:
high verified coverage and honest abstention rather than hallucination.

OpenAI design references (reviewed 2026-11-28):
- https://developers.openai.com/api/docs/guides/function-calling — bounded
  application-owned tool calls; strict schemas.
- https://developers.openai.com/api/docs/guides/structured-outputs — validate
  typed results and handle refusal/malformed output.
- https://developers.openai.com/api/docs/guides/evaluation-best-practices —
  measure ground truth and provider-failure cases. No paid OpenAI API is
  activated; do not depend on the retiring legacy Evals platform.

## Implemented in this phase
Exact-key evidence retrieval contract (symbol, ISIN, field, dataset, start/end
period, scope and unit); original document URL, hash, locator, published date;
independent verifier required; deterministic source fallback; strict bounded
AI source leads only; explicit conflict/missing withholding. This is a
retrieval/recovery interface and synthetic tests, not a live RAG deployment.
No production integration or external requests.

## Remaining implementation gates
1. Implement official roster and source adapters (reuse final_archive_rows
   in smart_chart_eod_recovery.py, which accepts Yahoo-style .NS symbols,
   and retrieve official final archive once for the entire admitted roster).
   Preserve exact successful source URL and document hash; validate session,
   exchange calendar, symbol/ISIN, raw OHLCV and integral volume. Verify
   alternate Yahoo history separately; no mixing adjusted/raw history.
2. Implement durable read-only shadow evidence retrieval keyed by the above
   identity; store public source receipts, revision/version and as-of dates.
   RAG uses exact structured lookup first, then authorized filing text search.
   Keep financial statement narratives separate from numeric evidence. Use
   no paid vector store; evaluate local or existing Postgres search with
   measured resource limits. Implement cache freshness and revision invalidation
   before enabling any reuse; do not contact every provider on each live read.
3. Reuse existing financial and chart validation, AI source discovery from
   msk_ai_data_recovery.py (configured free provider only), and deterministic
   calculations. Expand field schemas separately for all technical indicators,
   results, annual/quarterly/TTM, ratios, shareholding and corporate actions.
   Source verifier must independently retrieve and hash original bytes, check
   issuer and field identity, source rights, time, period, unit and scope.
   No arbitrary network requests from model text; restrict redirect hosts,
   response size and timeouts. No model-created prices or numerical records.
   Provider-specific budgets, durable deduplication, zero-cost admission,
   concurrency, breaker and recovery scheduling require integration tests.
4. Gate promotion on real-market evidence: 500 official members, exact
   coverage counts per dataset, 700 hard cap including concurrent admissions,
   100% provenance on accepted fields, wrong-value/period rejection, no fake
   prices, no duplicate sends, peak memory and Neon usage within existing
   measured limits. Compare against known issuer filings and technical
   reference calculations. Track failure reasons rather than promising 100%
   availability or 100% trading accuracy. Existing production remains unchanged.
5. Migrate only one consumer at a time behind default-off gates; validate
   outputs, CI, isolated Telegram receipt and rollback before enabling.
