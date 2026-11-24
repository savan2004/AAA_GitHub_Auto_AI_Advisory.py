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
