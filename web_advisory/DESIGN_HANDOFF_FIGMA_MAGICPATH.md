# MSK Research Desk — Figma / MagicPath Design Handoff

This is a code-implemented financial-services dashboard specification that designers can refine in Figma or MagicPath. It is NOT a claim that these plugins generated the current code or that editable Figma frames have already been created.

## Actual working preview
https://msk-web-advisory-preview.onrender.com/?symbol=BEL

## Product promise
An independent Indian-equity **research** workspace. No Telegram account. The default first page retrieves a dated (not intraday/live) BEL EOD price history using TejHQ. A chart based on data-bearing candles is clearly distinguished from a synthetic demonstration or a CSV uploaded by the visitor. Offline education Q&A is rule-based; real generative AI is NOT enabled.

## Desktop frames (Figma)
- 1440×900 and 1366×900 desktop variants; 390×844 mobile, 768×1024 tablet.
- Navy left sidebar: width 252 px. White 74 px utility bar.
- Background #F4F7FB. Text #17263E. Sidebar #101E34.
- White cards on gray canvas, border #E1E8F0, card radius 12–14 px.
- Emerald brand/action #19A991, accent mint #41DEC8, warning warm cream #FFF6E7.
- Font: Inter if locally available; fallback system sans. No separate font downloads required.
- 4-column company metric snapshot; 8 action cards on desktop, 2 columns on mobile.
- Full-width dark OHLCV candlestick canvas in real historical-data mode, with candle wicks/bodies, volume and EMA 20/50; chart minimum 535 px desktop and 445 px mobile.
- Research Detail and Ask Research columns beneath chart, stacking on mobile.
- Persistent source/as-of badge and disclaimer, always visible before recommendations.

## Eight working navigation actions
Dashboard (overview), Technical, Fundamentals, Trading Chart, Screener, Breadth, Research Report (browser print/PDF), and Ask Research (local educational Q&A). Buttons navigate to real content, not inert mockups. Each panel must disclose data dependencies:
- Technical and Chart: real user-supplied or TejHQ historical EOD, source date, experimental indicators; no audited trading signal.
- Fundamentals: user-supplied filing CSV and reporting date, absent by default.
- Screener: actual loaded symbols only, not automatically Nifty 500.
- Breadth: actual multiple dated symbol datasets required.
- Report: browser Print/Save PDF, NOT institutional valuation report.
- Ask: local deterministic research education, NOT connected to real Groq/Gemini without secured server.
- Watchlist: BEL, RELIANCE, TCS, INFY, clicking each requests that exact stock.
- Search: NSE symbol typed, sourced EOD fetched with stale-data / mismatch guard; never display Apple/AAPL when BEL selected.

## Quality bar and security rules
1. An external symbol mismatch must not render any alternative stock.
2. TradingView NSE embeds are restricted; NEVER put an AAPL-default chart under BEL. TejHQ EOD currently renders the site-owned OHLCV.
3. Clearly label 'historical EOD' not 'live market'; data may be unadjusted for splits.
4. Provider/API quote sources, financial report periods, and exchange rights are separate concerns. Do not invent a confidence/accuracy score.
5. No private holdings, API credentials, or financial portfolio are sent to client-side scripts.
6. Strict no-overflow on 390 px viewport, tappable targets ≥ 40 px, keyboard focus visible.
7. Public display and redistribution licence for exchange data must be evaluated before commercial launch.
8. Keep existing Telegram app and its Render service untouched; website is an isolated GitHub branch and separate Render static site.

## Editable prototype workflow with connected design tools
- **Figma:** create editable frames and a reusable design system for app shell, sidebar, KPI cards, stock search, research modules, candles area, report/detail panel, Q&A panel, loading/missing-data/error states. Map components to classes from `dashboard.css`.
- **MagicPath:** prototype desktop/mobile sidebar, quick research cards, watchlist selection, dataset status, and search-to-chart flow. Do not create fictional prices in prototype screenshots that can be mistaken for real prices.
- Review prototype against actual 1440×900 / 390×844 screenshots from CI artifact `msk-web-bel-actual-browser-evidence` in the GitHub Web Advisory workflow.
- Maintain accessible contrast; warn if an interaction is visual-only and not wired to GitHub JavaScript.

## Current deployment
Render static preview: `msk-web-advisory-preview` (free). Database is separate Neon project `floral-cloud-29842433` (not used for public per-user portfolio data yet). Public historical bars are client requested, and API availability/rate limits are external dependencies.

Release criteria: 13+ DOM feature tests, actual Chrome visual screenshot desktop/mobile, source-date/close comparison against real data provider, all eight actions, no mobile horizontal overflow, and no root main merge into Telegram.
