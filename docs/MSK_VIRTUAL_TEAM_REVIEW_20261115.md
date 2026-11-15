# MSK Bot — Virtual Expert Team Review (15 Nov 2026)

**Type:** Simulated cross-functional product meeting. These are role-based recommendations, **not a meeting with outside human experts**.  
**Scope:** MSK Bot `savan2004/AAA_GitHub_Auto_AI_Advisory.py` only. AiAutoUpdate Channel remains separate.

## Attendees and decisions

| Simulated role | Main finding | Decision |
|---|---|---|
| Quant Research | Rule/quality scores are not measured trading accuracy. | Track every issued setup through a predetermined holding window and outcome. |
| Indian Market Data | NSE/Yahoo intraday bars can lag or lack spot-index volume. | Nifty/Bank Nifty 5m candles must be from the current session; use ETF volume/VWAP explicitly labelled as a proxy; no data → NO TRADE. |
| Risk Manager | High win-rate claims hide stop size, gap risk and trading costs. | Hard stop, stop-percent cap, at least 1:2 T1 R:R and no automatic orders; avoid issuing trades on holidays/stale feeds. |
| Product/UX | A broken Portfolio button undermines confidence. | Hide it from the new menu; keep portfolio data and recovery code untouched. Replace with Intraday Trade. |
| QA/DevOps | A button sending a Telegram message is not proof the advice is correct. | CI first, production deploy, owner-initiated message ID, independent verification and timestamped outcome records. |

## Readiness review from actual Neon owner acceptance records

Scores below are **provisional engineering/readiness estimates, not stock-picking or trading-accuracy measurements**. A PASS is a recorded QA state; an API message ID alone does not certify the research data. Evidence read 15 Nov 2026 from `msk_bot_acceptance_actions`.

| Button | Provisional readiness / 10 | Last recorded evidence | Main gap |
|---|---:|---|---|
| 🔍 Analysis | 6 | 14 Nov: Telegram message 14237; UNVERIFIED | Fundamental/price source independent checks |
| 🩺 Market Health | 6.5 | 15 Nov: message 14292; UNVERIFIED | FII OI, PCR, Brent, rate source freshness |
| 🔥 Stocks Buzz | 3.5 | 14 Nov: FAIL, no message ID | Universe completeness / verified breakouts |
| 🏦 Large Cap Top 5 | 6.5 | 14 Nov: message 14275; UNVERIFIED | Full data/signal certification |
| ⚖️ Mid Cap Top 5 | 8 | 13 Nov: message 14225; PASS | Ongoing fresh-session rechecks |
| 🚀 Small Cap Top 5 | 4 | 13 Nov: last ledger FAIL, older data; user reports button now works | Re-certify latest actual content |
| 🎯 Swing Safe | 6.5 | 15 Nov: message 14293; UNVERIFIED | Outcome-based confirmation and risk checks |
| 🚀 Swing Aggressive | 6.5 | 15 Nov: message 14303; UNVERIFIED | Outcome-based confirmation and risk checks |
| ⚡ Intraday Trade | Not rated until LIVE | New code/CI in this PR | Live-session market-feed and Telegram verification |
| 📈 Chart | 8 | 13 Nov: message 14221; PASS | Ongoing accuracy/image check |
| 📄 Research PDF | 6 | 13 Nov: message 14222; UNVERIFIED | Non-SBIN data + PDF financial validation |
| 🏭 Sector Report | 3 | 13 Nov: FAIL | Live PDF delivery, correct constituent data |
| 💼 Portfolio | **Hidden** | 13 Nov: FAIL | Safe recovery before re-enable |

**Do not interpret 8/10 as 80% win-rate or a probability of market success.** These are qualitative engineering estimates only; individual dates differ. Latest user tap tests may be newer than the last ledger row.

## Proposed accuracy measurement (no invented 70% claims)

**Primary KPI 1 — Outcome-based trade expectancy (R):** For every genuinely posted triggered strategy signal, record symbol/index, LONG/SHORT, entry, SL, T1/T2, IST timestamp, source bar, Telegram message ID, and an expiry window. Evaluate actual subsequent candles without look-ahead. If stop and target are hit within one bar, assume stop first. Include realistic costs, spreads and slippage. Report average net R and number of trades, separated by strategy.

**Primary KPI 2 — Verified-signal proportion:** % of *published actionable* signals with a same-session verified candle, source timestamp, valid instrument, correct BUY/SELL geometry, risk cap and actual Telegram ACK. Target **100%**; any stale trade is a failure. A withheld/no-trade response does not count as an actionable signal.

**Primary KPI 3 — Data/Telegram coverage:** % of user requests that return either a complete verified report or a clear NO TRADE/unavailable outcome within 30 seconds, with API receipt. Monitor unverified and failed data sources separately; delivery ≠ recommendation correctness.

**Outcome guardrails:** Report trade count, net expectancy, hit rate plus uncertainty, max drawdown, no-trade frequency and risk-adjusted return. Do not claim a ≥70% win rate without enough *forward-recorded* completed trades (suggest minimum 30 per strategy and more for publication-level confidence). Never optimize a hit rate by dropping losing or unfilled calls.

## Approved future additions (ranked)

1. **Signal outcome journal:** Daily actual trades vs WAIT vs NO TRADE; entry/SL/T1/T2 result with broker costs and Telegram message ID; neutral, no exaggerated accuracy.
2. **Data-quality traffic lights:** per-feature green/amber/red for source, latest timestamp, coverage and verification; show directly in output.
3. **Personal risk calculator:** user enters capital and maximum risk; calculate educational maximum quantity without storing broker credentials or placing orders.
4. **Nifty/Bank Nifty option OI context:** verified expiry-specific OI PCR, max pain and OI buildup, labelled indicative; never derive premium from index spot.
5. **Weekly transparent performance report:** triggered calls, missed data, winners, losers, net R and no-trades, including error bounds; no cherry-picking.
6. **Event alerts:** earnings, FOMC/RBI decisions, holidays, expiry and crude/USD-INR moves, using a dated primary source.

## Intraday acceptance contract

- New button must be visible exactly once and work only on explicit private user click or `/intraday`. No startup, cron, channel auto-post or background check.
- Portfolio button must be absent from new keyboards; old Telegram cached labels get a maintenance reply. Portfolio holdings and slash recovery code must not be deleted.
- Outside NSE trading window 09:40–15:15 IST / weekends / exchange holidays → NO TRADE, no invented live price.
- Need aligned current-session 5-minute spot and ETF candles, 15-minute opening range, EMA20/50, Wilder RSI/ATR/ADX, ETF VWAP and actual observed volume. Explicitly state that ETF volume is a *proxy*, not index volume.
- No setup until all hard gates: ADX ≥20, RSI/EMA trend, confirmed ORB beyond buffer, ETF volume >1.2× recent average, 0.08%–0.90% max stop risk, planned T1/T2 = 2R/3R.
- A technical quality score /10 is never marketed as measured trading accuracy.
- NO orders are placed automatically. Option premiums, option liquidity, lots and slippage must be checked separately.
- CI/regression success does not prove a real live market BUY. Confirm Render LIVE and actual user-initiated Telegram ID on next trading session before certifying end-to-end.

**Next meeting decision:** Prioritize outcome journal, real-data certification and sector-PDF recovery over adding more prediction buttons.
