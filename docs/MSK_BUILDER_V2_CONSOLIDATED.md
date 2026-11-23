# Consolidated MSK Portfolio Builder V2 — offline-only candidate

This focused draft is created from production main `1c1af8c4107dcf0dce3a53cbfb798baacf478b8b`. It preserves the
already deployed retirement of the legacy manual customer Builder and the
private owner-only fictional beta. It adds the **isolated** approved-research
model/PDF/Telegram receipt contract from old draft #246, plus offline tests.
There is **no change to main.py**, no customer route, no automated Telegram
send, no database modification and no production flag activation.

## Prior PR disposition

- #246: relevant automatic evidence, allocation, PDF and receipt contract; this
  draft cherry-picks the standalone engine/test/transport only. The old wizard
  integration is intentionally NOT imported, because it conflicts with the
  deployed fail-closed Builder contract and lacks independently verified data.
- #213: profile/allocator/fictional beta components are already present in main;
  do not duplicate their previous versions.
- #229: optimizer depends on a second incompatible engine interface and
  extra solver dependencies; not blindly transplanted.
- #233: weekly secondary research collector fails rehearsal and cannot be a
  source of approved customer portfolios; keep outside this PR.
- #251 and #206: previous manual-Builder repairs are superseded by the already
  merged #252 and must not be restored.
- #219: separate PostgreSQL portfolio-message idempotency topic, not Builder.

## Unmet release gates

1. Independent issuer-primary source verification for the proposed company
   research bank, including exact NSE identity, dated financial statements,
   corporate actions and completed daily close.
2. Fresh complete research and risk evidence; no invented scores, sources,
   future prices or hand-built "APPROVED" labels.
3. Regulatory/customer suitability and publication approval.
4. Separate 3-input customer UI integration and full Telegram end-to-end
   delivery test with an actual confirmed platform receipt.
5. Regression review that current Portfolio Doctor typed/CSV/XLSX uploads and
   PDF continue to work. No uncertain Telegram resend.
6. All checks green and deliberate human review before any production merge.

**Status: DRAFT, NOT PRODUCTION ENABLED.** JSON fixtures in tests are
explicitly fictitious and prove code paths only, not issuer authenticity.

## Step 1 hardening: reviewed release fence

The customer engine now refuses any JSON artifact unless the repository's
`msk_builder_research_release_registry.json` explicitly pins its SHA-256 digest
AND a second independently reviewed source-evidence audit bundle covers each
issuer and the exact completed-session closing price. The default registry is
`WITHHELD` and empty, so even enabling the environment flag and providing a
synthetic `APPROVED` JSON cannot publish a portfolio. No production flags
were changed.

**Important:** SHA matching and an approval record are proof of provenance
*within the repo*, not proof the source filing bytes or financial calculations
are correct. The `research_audits/<sha256>.json` bundle must be built from
actual authenticated primary-source filings, peer-reviewed, and committed
separately before any approval entry is added. Reconcile each issuer ISIN,
financial-period basis, annual/quarterly figures, official completed close,
corporate actions, valuations and risk assessment. Do not put synthetic QA
fixtures, placeholder hashes, or manually asserted approval text into the
production registry. The sample tests deliberately mock the trusted release
boundary to exercise the mathematical/PDF implementation.

Step 1 remains **BLOCKED on real issuer-primary audits**, not complete.
