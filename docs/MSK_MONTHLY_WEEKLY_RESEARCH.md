# MSK: monthly AI suggestions + weekly technical / monthly fundamental refresh

## What we already had (reused without copying)

- `index_constituents.py`: official NSE constituent sets and industry names
- `top5_company_ratings.fetch_company_evidence`: existing monthly financial/fundamental review, explicit validation and missing-data handling
- `market_data.bulk_history`: existing bounded multi-company completed-candle reader
- `top5_technical_ratings.technical_candidate`: existing risk-aware technical ratings
- `msk_ai_builder_automatic.py` / `msk_ai_builder_research_release.py`: unchanged customer input, independent release approval, allocation and report gates

The previous `ai_engine.py` models `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` were **retired on Groq free/developer tiers in August 2026**; this isolated monthly job instead uses the vendor-documented `openai/gpt-oss-120b` model, if the existing `GROQ_API_KEY` secret is available and permitted by account limits. It never falls back to a paid model.

## Simple blueprint

1. **Once monthly** (GitHub Actions day 1 at 07:47 IST): load three official NSE lists (Nifty 100 / Midcap 100 / Smallcap 100); submit their symbols and sectors as **ONE combined AI prompt**; accept only 5–12 valid in-index symbols per group (reject hallucinations, duplicates, stale indexes). AI chooses research ideas only; AI **does not invent stock prices, fundamentals or trade performance**.
2. **Once monthly**: run the existing `fetch_company_evidence` only for up to 36 AI ideas (no full Nifty 500 research), reject missing/weak/stale data, save a dated internal `msk-monthly-research.json` GitHub Actions artifact.
3. **Every Monday** (09:17 IST): reuse the monthly artifact; one bounded bulk OHLCV request for the financial shortlist; run the existing completed-candle technical ratings; save five best *watchlist ideas* per index as `msk-weekly-research.json`. **Zero weekly AI calls and zero weekly fundamental fetches.**
4. **Every customer click**: existing Builder reads only independently reviewed/approved release artifacts, never the research-only weekly JSON. The present 3 inputs, PDF, Telegram protections and full Stock Research section are untouched. No data without regulatory/source permission enters a commercial report.

## Operational boundaries

- Neither script sends Telegram, changes Render, reads private user holdings, adds live API expenses, writes to Neon or modifies a production-approved bank.
- Scheduled jobs run only on GitHub's default branch after merge; pull requests run network-free tests.
- Provider failures, missing AI credentials, stale monthly records and wrong index membership produce a **withheld/failed research job**, not fabricated equities.
- The monthly workflow requires the existing GitHub Actions repository secret `GROQ_API_KEY`. No assumption is made that this secret exists or that Groq's plan permits extra usage. A missing key requires owner authorization rather than silently using a paid OpenAI account.
- **This is a research prototype, not a proven 15-company investment-grade dataset or live customer publication.** Financial sources include secondary websites and commercial data rights still need confirmation; independent official issuer financials and release approval are required.

## Product distinction

The user-facing Portfolio Builder flow stays: **investment amount → risk → investment timeframe → qualified stock allocation → compact PDF**. Detailed fundamentals remain available separately under **Stock Research**. A relative watchlist and its ratings do not guarantee an investment return or establish legal suitability.

## Local tests

```bash
python -m unittest discover -s tests -p 'test_msk_monthly_weekly_research.py' -v
```

No API key or external HTTP connection is needed for offline tests.
