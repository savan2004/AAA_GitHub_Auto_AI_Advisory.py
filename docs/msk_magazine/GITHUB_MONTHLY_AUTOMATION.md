# MSK Magazine — independent GitHub monthly automation

## Schedule and stored results
- Workflow: .github/workflows/msk-magazine-monthly.yml
- GitHub cron: Monday 02:30 UTC (08:00 IST); code enforces the **first Monday of each month**. No runs on other Mondays.
- First expected monthly draft after default-branch merge: **7 December 2026**.
- PDF result: MSK_YYYY-MM_EDITORIAL_DRAFT.pdf with exact 16 A4 pages and locked Design 3 index.
- GitHub run artifact includes the PDF, 16 per-page JSON evidence/review files, release_gate.json and original downloaded source files.
- GitHub artifact expiry: 90 days. Copy to a controlled archive for permanent retention.
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
