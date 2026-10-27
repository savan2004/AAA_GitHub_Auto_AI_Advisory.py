# MSK RESEARCH — CLAUDE / EXTERNAL ENGINEER HANDOFF
Date: 2026-10-27
Repository: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py
Source branch: `trial/company-journey-20261027`
Website preview: https://msk-web-advisory-preview.onrender.com/
Existing bot: IndianstockAIbot (Render service on `main`; do not modify/deploy bot)
Related draft PRs: #54 (market first), #55 (company journey)
Primary recovery backlog: #53

## Product requested by owner
Build a usable Indian stock research terminal:
1. Dashboard starts with indices (NIFTY 50, BANK NIFTY, SENSEX, INDIA VIX), breadth, sector context and dated market-moving news.
2. A clearly visible company/NSE-symbol search.
3. Selecting a symbol displays real identity, business overview, latest dated validated EOD price and basic financial/technical facts.
4. All tabs apply to the SAME selected stock: full fundamentals, technical, clean chart, stock screener, peer comparison, portfolio, research report and AI analyst.
5. A true downloadable institutional MSK PDF (existing Python generator should be reused if securely feasible), not just browser print.
6. Responsive mobile/desktop; no fake or stale data, no invented signal confidence.

## Current implementation
- `web_advisory/` is a static frontend hosted by separate Render static site on branch `web/msk-web-advisory-mvp-20261027`, auto-deploy disabled.
- `market_first.js`, `market_first.css` provide trial overview and links. Index levels deliberately unavailable without approved source.
- `company_journey.js`, `company_journey.css` provide source-aware stock panel and contextual buttons.
- `app.js`, `features.js`, `dashboard.js`, `market_widget.js`, `import_center.js` are earlier modules.
- Existing code queries a free external TejHQ OHLCV endpoint from the browser; network/cors/freshness and redistribution authorization NOT proven for all symbols.
- Financial ratios currently require a user-supplied CSV; no audited automatic issuer filing pipeline.
- Generative AI intentionally not connected; offline responses are deterministic.
- Website PDF is not connected to complete MSK Python research-report generation.
- `main` is a SEPARATE Telegram service; share modules only through safe, isolated APIs.
- `web_advisory/tests/market_first_trial.test.cjs` was added; dedicated CI workflow `.github/workflows/msk-web-market-trial.yml` was added, but references **missing** `web_advisory/tests/trial-browser.cjs`. Correct the workflow/test mismatch first; CI pass is NOT confirmed.
- Latest trial changes were **not deployed** and should not be described as live.

## Tasks — order and acceptance
P0: Make branch CI green. Add a genuine headless browser test for the market-first and stock-search flows, capture screenshot and JS console failures.
P0: Fix any core data consistency error (symbol mismatch, stale breadth, stale stock price) and test failure paths.
P0: Implement a real company lookup and source-dated financial research view with licensing/coverage disclosure. Never invent data.
P0: Design and implement a secure, independently deployed PDF generation API with authenticated access and a downloadable research report; reuse audited MSK report code without exposing Telegram secrets. Reject incomplete reports rather than pretending they are complete.
P1: Build accessible mobile layout, show loading and error states, use no false live-feed badges.
P1: Conduct cross-browser, large/small screen and CSV import negative tests.
P1: Define credible high-quality screener based on licensed, date-aligned NSE stock universe; keep index membership separate.
P1: Verify production service source SHA, then test the ACTUAL deployed version after user review/approval.

## Important engineering and safety constraints
- Never merge trial branch into `main` (Telegram bot deploys from main).
- Do not alter Render's bot service, Telegram tokens, database secrets, billing or paid APIs.
- Work using small isolated commits and draft PRs; provide exact tests and outputs.
- Don't claim a button works just because it changes the selected tab.
- Distinguish synthetic DEMO data, user-imported CSV, historical EOD, and independently verified financial filings.
- Perform independent data licensing, CORS, timeliness, and financial source verification before public use.
- No unsupported BUY/SELL probabilities or tailored advice; evaluate applicable SEBI requirements.
- Never paste or include live tokens/credentials in a shared zip or a third-party AI chat.

## Specific request for Claude
1. Inspect the **whole** codebase and especially `web_advisory/`.
2. Give a ranked P0/P1 defect list with file+line references.
3. Fix one user-visible P0 defect at a time, including test and evidence.
4. Provide a safe plan for full end-to-end RELIANCE stock research PDF.
5. Return code patches or a reviewed PR plan, plus honest runnable test results.
