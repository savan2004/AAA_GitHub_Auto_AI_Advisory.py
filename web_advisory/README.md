# MSK Web Advisory — independent static MVP (27 Oct 2026)

A separate Render STATIC SITE on an isolated GitHub branch, without Telegram dependency. The original MSK bot main branch and Render service are unchanged. The repo is shared **temporarily only** because the connected GitHub action cannot create a new repository; the production website should eventually move into its own `MSK-Web-Advisory` GitHub repo.

## What actually works
- Mobile / desktop dark design; NSE symbol field
- Daily OHLCV CSV upload (minimum 50 rows), strict header and candle validation
- True candlestick bodies/wicks, volume, EMA20/EMA50, Wilder ADX14, regression channel
- Relative volume uses the **previous** 20 candles, not current candle in denominator
- Technical, Chart, Research PDF (browser print) and strict WAIT or breakout gating
- Info panel signals and clean offline research notes
- Clearly labelled **SYNTHETIC** demonstration, never called actual NSE stock data
- Read-only browser operation; CSV/portfolio entries never transmitted to our server

## Data accuracy / legal scope
- The website does **NOT** fetch NSE quotes, company filings, breadth universes or fundamental ratios from public third-party APIs. No rights to redistribute Yahoo/Screener market data are presumed.
- There is no current price/LTP. Imported data is user-supplied and source-unverified.
- Market Breadth, multi-stock Screener and Fundamentals disclose missing data instead of generating false numbers.
- Direct Ask AI interface exists but **does not call any LLM until a secured backend and explicit provider configuration are deployed**. No fake AI replies, no exposed keys or billing.
- AI later must use separate provider credentials, per-user rate limits, abuse protection, consent, source/date grounding and backend-only secrets. Never put API keys in config.js.

## New cloud project
Neon Free PostgreSQL **MSK-Web-Advisory** (Singapore), project id `floral-cloud-29842433`. Not yet used; there is no point writing client PII before authentication, backups and security controls are available.

## Deployment
- GitHub branch `web/msk-web-advisory-mvp-20261027`
- Render **Static Site** build: `true`; publish directory `web_advisory`
- Static hosting does not consume Render Free Web Service hours, but bandwidth/build allowances still apply.
- Later, serve authenticated Python API separately or use Neon Functions; **do not reuse Telegram bot token, webhook or live bot service**.
- If Render's Free Web Service tier is chosen, it spins down after idle and shares the workspace 750 free-instance-hours across all apps. Existing MSK/AiAutoUpdate services are in the same workspace.
- **Do not upgrade billing without explicit approval**. Replit app creation returned requires_active_subscription, so it was NOT used.

## No-code steps for owner
The main website hosting and Neon Free project can be managed here via connected Render/Neon apps. Once needed, the owner must explicitly authorize AI provider secrets and any paid products, and separately consent to any personal portfolio storage.

## Acceptance and limits
Run built-in static JS syntax check via GitHub Actions. Manually verify Chrome/Android layout, demo candlestick hover, CSV import with real sourced data, Print PDF, all buttons and unavailable-AI message. No historical ROI, live market accuracy, SEBI regulatory approval or production-grade uptime is claimed.
