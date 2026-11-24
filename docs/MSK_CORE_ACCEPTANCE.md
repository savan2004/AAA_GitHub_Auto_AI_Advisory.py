# Core data acceptance (P1-P6)
- Quotes retain provider-reported symbol, reject pre-session observations during regular trading hours. An exchange holiday calendar and independent price reconciliation remain necessary.
- A shared Wilder RSI removes fabricated neutral values from incomplete histories. CI-only TA-Lib reference uses a mature deterministic series. Bollinger sample-vs-population convention is reported, not certified.
- Field reconciliation checks ISIN, metric, date range, publication date, unit, scope, allowed NSE URL and Decimal values. It does not authenticate a filing. Existing issuer snapshots reject explicit scope contradictions.
- P3: live NSE discovery, authenticated filing ingestion (e.g. Arelle), issuer taxonomy mapping and independent reconciliation of 20 companies remain outstanding. Never expand approval from synthetic tests.
- P4: offline core and menu tests on Python 3.11/3.12; TA-Lib and NumPy 2 only in CI. No production dependency upgrades.
- P5: Groq GPT-OSS 120B remains a documented model; compare providers with verified fixtures before switching. Production credentials, quota and actual quality are not proven here.
- P6: menu acceptance uses real handler AST and mocks, not a private Telegram delivery.
- Production, paid services, research approval and actual trading are unchanged.

P5 offline harness: `python scripts/msk_model_eval.py` emits identical synthetic prompts; `python scripts/msk_model_eval.py outputs.json` scores externally captured raw model answers, with strict field identity, exact numerals, nulls and units. Compare `openai/gpt-oss-120b`, `gemini-3.5-flash-lite` and `gemini-3.8-flash` only after verifying account quota and provider terms. Five fixtures are a schema smoke test, not an investment-quality benchmark or a substitute for a live evaluation. No model requests are made by CI.

Central RSI phase: six consumer modules (technical_indicators, data_engine, swing_trades, msk_intraday_trade, investment_screener, portfolio_doctor_calculations) delegate to one pure Wilder implementation. Prefix invariance, flat/monotone series, missing observations, and caller delegation tested. ATR/ADX/MACD/EMA centralization, strategy regression and live output acceptance remain separate work; do not infer 100% trading accuracy.

## 24 November 2026 — manual independent public quarterly cross-check

Scope: consolidated quarter 1 July–30 September 2026. Reviewed official NSE iXBRL displayed amounts (INR lakh converted to crore by dividing by 100) against the separately retrieved Screener consolidated quarterly tables. These are dated observations, not an automated provider audit or approval of the Builder research bank. Secondary publication timestamps and live production adapter output were not verified; do not infer that the full reconcile_fact metadata contract has passed.

| NSE symbol / ISIN | Field | NSE | Screener | Result |
|---|---|---:|---:|---|
| DMART / INE192R01011 | Revenue (INR crore) | 19644.01 | 19644 | agrees within rounding |
| DMART | Total consolidated profit (INR crore) | 742.98 | 743 | agrees within rounding |
| DMART | Basic EPS (INR/share) | 11.40 | 11.40 | agrees numerically |
| TCS / INE467B01029 | Revenue (INR crore) | 73188 | 73188 | agrees |
| TCS | Total consolidated profit (INR crore) | 13934 | 13934 | agrees |
| TCS | Basic EPS (INR/share) | 38.37 | 38.37 | agrees numerically |
| INFY / INE009A01021 | Revenue (INR crore) | 49244 | 49244 | agrees |
| INFY | Total consolidated profit (INR crore) | 7838 | 7838 | agrees |
| INFY | Basic EPS (INR/share) vs secondary reported EPS | 19.32 | 19.28 | WITHHELD: EPS basis/value must be reconciled; do not overwrite either |

Primary sources (board approval dates: DMART 10 Oct, TCS 8 Oct, INFY 23 Oct 2026):
- DMART: https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_200410_10102026170830_iXBRL_WEB.html
- TCS: https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_200121_08102026220810_iXBRL_WEB.html
- INFY: https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_203729_23102026202117_iXBRL_WEB.html

Secondary sources:
- https://www.screener.in/company/DMART/consolidated/?order=desc
- https://www.screener.in/company/TCS/consolidated/?order=asc
- https://www.screener.in/company/INFY/consolidated/?order=desc

Eight numeric agreements, one unresolved discrepancy. The total group profit is not interchangeable with profit attributable to owners (e.g. DMART NSE group profit 742.98 crore vs owners' profit 743.573 crore). The differing EPS may reflect denominator/basis differences; neither a cause nor a corrected secondary EPS has been established. Keep publication WITHHELD; no change to the approved registry, no automatic recommendation, no claim of 20-company coverage.
