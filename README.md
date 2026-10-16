# AAA GitHub Auto AI Advisory — Telegram Stock Bot  (v6.2)

## What changed in v6.2
| Change | Details |
|---|---|
| ❌ Removed | 📰 **News** button and 🤖 **AI** chat button (plus AI topic keyboard, `/clear`, AI chat state). Old cached keyboards get a polite "retired" reply. |
| 🔥 Added | **Stocks Buzz** — one tap scans the top ~1000 NSE stocks for fresh, volume-confirmed breakouts. Also `/buzz`. |
| 📄 Rebuilt | `/report SYMBOL` is now an institutional-style research PDF (see below). |
| 🛠 Fixed | `config.py` was missing 16 constants imported elsewhere (`RSI_PERIOD`, `TIMEOUT_YAHOO`, …) — the project crashed on `import main`. Added with env-overridable defaults. |

## 🔥 Stocks Buzz
1. Universe (`universe.py`): `universe_override.txt` → NSE bhavcopy top-1000 by turnover → Nifty 500 + Microcap 250 lists → built-in fallback. The message tells you which tier was used.
2. Bulk download of 1y candles (`market_data.py`) — minutes, not hours.
3. Score 0-100 (`stocks_buzz.py`): breakout class (52W/55D/20D high) · volume surge · trend & momentum · candle strength · squeeze/tight base · relative strength vs Nifty · delivery % · penalties for extended/overbought.
4. Output: top 10 breakouts with entry / stop / T1 / T2, a near-breakout watchlist, market-regime line, and inline buttons that open Technical / Fundamental / Chart / PDF for each stock.
Liquidity gate: price ≥ ₹20 and ≥ ₹2 Cr average daily turnover. Blow-off moves (RSI>85 or >15% above EMA20) are counted but not recommended.
Scores are rule-based heuristics and have **not** been back-tested.

## 📄 Research report
Executive summary (composite score, stance, 3 horizons) · investment view · technical analysis (candles, EMAs, RSI, MACD, S/R, weekly trend, returns vs Nifty) · risk (volatility, drawdown, VaR, beta) · fundamentals (valuation, growth, margins, cash conversion, 10-yr + quarterly tables) · ownership trend · peers · scenarios & trade plan with position sizing · risk matrix · methodology appendix.
Sections without data are omitted. AI commentary is optional and rejected if it states any figure not in the supplied data.

## Files
`stocks_buzz.py` `universe.py` `market_data.py` — Stocks Buzz  
`report_generator.py` `research_engine.py` `research_data.py` `report_charts.py` — report  

## Env vars (new, all optional)
`BUZZ_UNIVERSE_SIZE` (default 1000) · `BUZZ_UNIVERSE_FILE` (default `universe_override.txt`) plus the timeout/TTL/indicator settings now in `config.py`.
