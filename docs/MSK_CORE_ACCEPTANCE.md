# Core data acceptance (P1-P6)
- Quotes retain provider-reported symbol, reject pre-session observations during regular trading hours. An exchange holiday calendar and independent price reconciliation remain necessary.
- A shared Wilder RSI removes fabricated neutral values from incomplete histories. CI-only TA-Lib reference uses a mature deterministic series. Bollinger sample-vs-population convention is reported, not certified.
- Field reconciliation checks ISIN, metric, date range, publication date, unit, scope, allowed NSE URL and Decimal values. It does not authenticate a filing. Existing issuer snapshots reject explicit scope contradictions.
- P3: live NSE discovery, authenticated filing ingestion (e.g. Arelle), issuer taxonomy mapping and independent reconciliation of 20 companies remain outstanding. Never expand approval from synthetic tests.
- P4: offline core and menu tests on Python 3.11/3.12; TA-Lib and NumPy 2 only in CI. No production dependency upgrades.
- P5: Groq GPT-OSS 120B remains a documented model; compare providers with verified fixtures before switching. Production credentials, quota and actual quality are not proven here.
- P6: menu acceptance uses real handler AST and mocks, not a private Telegram delivery.
- Production, paid services, research approval and actual trading are unchanged.
