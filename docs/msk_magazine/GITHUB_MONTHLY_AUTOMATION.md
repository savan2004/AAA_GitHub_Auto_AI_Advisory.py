# MSK Magazine — independent GitHub monthly automation

## One-magazine-per-month production policy

- **Exactly one public edition per calendar issue** (YYYY-MM), and only after all release gates pass. Generating private review drafts does not create an additional magazine.
- **Days 1–15:** collect dated official finance filings, index, macro and company data; store checksums, source URLs and dates. This is the editorial target, **not** a claim that continuous source collection is already implemented.
- **Days 16–23:** write current analysis and create charts from verified values; no synthetic numbers or old-month recommendations.
- **Days 24–27:** assemble the locked 16-page Design 3 PDF; complete title, page count, chart and logo checks.
- **Days 28–month-end:** independent source comparison, page-by-page visual review, corrections and final candidate.
- **Following month:** the existing first-Monday workflow may create an **unapproved private draft**, not a public edition. Release is separate and requires complete financial/chart QA, Telegram proof and explicit owner approval. If these fail, remain unpublished rather than issuing a filler magazine.
- A missing or delayed month **must not** be replaced with an invented edition. Revisions must retain the same issue identity; never send more than one public release for the same issue.
- Do not claim daily/weekly autonomous collection, public-delivery deduplication or publication readiness until their connected jobs and ledger evidence exist.

## Schedule and stored results
- Workflow: .github/workflows/msk-magazine-monthly.yml
- GitHub cron: Monday 02:30 UTC (08:00 IST); code enforces the **first Monday of each month**. No runs on other Mondays.
- First expected monthly draft after default-branch merge: **7 December 2026**.
- PDF result: MSK_YYYY-MM_EDITORIAL_DRAFT.pdf with exact 16 A4 pages and locked Design 3 index.
- GitHub run artifact includes the PDF, 16 per-page JSON evidence/review files, release_gate.json and original downloaded source files.
- GitHub artifact expiry: 90 days. Additionally each actual scheduled/manual run creates a uniquely named **UNPUBLISHED GitHub draft release** containing the PDF, release gate and source/page evidence archive. Draft release assets remain available in GitHub until explicitly deleted or repository policy changes. These are NOT public magazines or approved releases.
- Workflow schedules do NOT run from a draft branch. Do not call this live until merged to default branch.

## Source verification
The monthly runner automatically tries the NSE official daily index CSV around the previous month-end:
https://archives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv

It keeps original bytes, source URL, SHA-256, source date and exact data rows. It accepts all four valid indices (Nifty 50, Nifty Bank, Nifty Midcap 150, Nifty Smallcap 250), valid closing values and correctly matching archive dates. It explicitly blocks 403/404, timeouts, wrong dates, duplicate or missing indices. Missing input means withholding all figures, never inserting zero.

The PDF generator rechecks the numerical value in its matching official-source text excerpt, original source bytes, checksum, URL and observation date. Conflicting same-day values are withheld. All checks are structural byte consistency, not independent authentication or proof of financial correctness.

Other financial content (CPI, RBI policy, sector breadth, FIIs, company fundamentals, Top 5 stock rankings) must stay withheld until separately sourced and reviewed. No investment suggestions are invented.

## Editorial QA
Automated tests: 16-page A4, unchanged titles/order, required unapproved watermark, PDF checksum and valid document structure. Visual clipping, logo fidelity, business accuracy and owner sign-off require separate checks. Design is approved but content/publication is not.

The renderer accepts --approved-logo path/to/asset.png. Until original owner-approved logo bytes are installed, it visibly says ORIGINAL LOGO ASSET PENDING instead of misrepresenting replacement artwork.

## Reproduce locally
1. Install open-source packages: reportlab==4.2.5, pypdf>=5,<7, requests==2.32.3.
2. Run: python -m unittest discover -s tests -p test_msk_magazine_monthly.py -v
3. Run: python -m unittest discover -s tests -p test_msk_magazine_nse.py -v
4. Run: python scripts/msk_magazine_nse.py --issue 2026-12 --output-dir magazine_artifacts/2026-12/sources
5. Render: python scripts/msk_magazine_monthly.py --issue 2026-12 --output-dir magazine_artifacts --source-pack magazine_artifacts/2026-12/sources/source_pack.json --source-root magazine_artifacts/2026-12/sources
6. If no valid source_pack.json was created, omit --source-pack and --source-root to generate clearly withheld research draft.

There is **no Telegram call, no public post, no live bot integration, and no automatic release** in this workflow. It ALWAYS sets publication_allowed=false and editorial_approved=false.

Tracked by MSK GitHub issue #96 and PR #159. Never edit AiAutoUpdate or another project.

## Durable archive safety

The workflow uses contents:write only in the monthly draft job. A GitHub Actions run with a unique run ID creates a private/unpublished draft release; it never publishes the release, never overwrites a previous draft, and never sends Telegram messages. A draft release is **not** owner editorial approval. Workflow publication still has no code path. If GitHub policy or permission blocks draft creation, the job fails visibly after producing the Actions artifact; never report archive success without a draft release record.
