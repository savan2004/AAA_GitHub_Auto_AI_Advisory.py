# MSK Model Portfolios: financial data release checklist

## As of 26 October 2026
- Draft PR #38 has three working offline model selectors, not deployed.
- Read-only public-feed audit succeeded for five real companies: RELIANCE, INFY, ITC, SUNPHARMA, LT. Audit: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40322107702
- Yahoo historical price charts returned 200, Yahoo quoteSummary fundamentals returned 401, and Screener.in page ratios plus dated annual and balance-sheet values parsed. A successful parser is NOT independently verified issuer filing accuracy.
- No verified ten-stock live portfolio and no end-to-end Telegram production test yet.

## Source permissions
Screener.in's published terms permit personal, non-commercial transitory viewing only; public/commercial bot usage needs operator-confirmed rights. See https://www.screener.in/guides/terms/
Automatic public web-source polling has been disabled in CI. Screener access in the model data reader is disabled by default. Enabling MODEL_SCREENER_USE_AUTHORIZED=true must only happen after explicit rights are granted.

## Authorized data file format
Supply a licensed, sourced, reviewed JSON file as MODEL_APPROVED_DATA_PATH (file on the Render host, not a URL). It must contain schema_version=1, approved_by=<named reviewer>, records keyed by NSE ticker.
Each record requires exact symbol, name, sector (matching the bot's sector classification), provider, https provider_url, coverage definition, fiscal_sales_period, fiscal_sales_previous, balance_sheet_period (YYYY-MM), fetched_utc (UTC ISO), pe (multiple), roe_pct (percent), revenue_growth_pct (YoY percent), debt_equity_ratio (multiple), market_cap (absolute INR).
Snapshot retrieval must be less than 24 hours old; annual and balance periods no older than 460 days, and annual comparison periods must be consecutive. Provider and accounting dates are distinct. Incorrect or incomplete data are rejected, never replaced by guessed numbers.

## Release blockers
1. Obtain genuine financial-data and publication rights, or regularly supply an independently reviewed and authorized file.
2. Cross-check source statements with issuer NSE/BSE filings and symbol/corporate-action updates.
3. Run an actual full-universe dry run with 10 legitimate passing companies in each model and sector/liquidity/quality thresholds.
4. Check Render workload, cache warm-up, rate limits, stability and private Telegram delivery.
5. Approve production merge only after these checks. No return/NAV or actual ₹10 crore investment is claimed.
