# MSK Research Buttons — AI Ask and Draft

## What users see
All original deterministic cards, chart images, report PDFs, layout and reply buttons stay present. After a completed research output, an optional row offers:
- **🤖 Ask AI** — user types a short natural-language question about the report.
- **✍️ Draft View** — generates a structured outlook / evidence / risks / missing-data note using the last report.

Root **🤖 Ask AI** supports general educational questions without requiring market data.

## Covered research actions
Stock lookup and Analysis; Technical; Fundamental; Chart (symbol-specific and auto-scan); Stock PDF (separate public-metrics recap, NOT PDF extraction); Conservative, Moderate and Aggressive screens; Breadth; Swing Safe and Aggressive; Stocks Buzz; Sector Report; Portfolio (GENERAL EDUCATION ONLY).

Status, buy/sell portfolio mutations, group discussion posts and health reports intentionally do not dispatch private per-user AI requests.

## Evidence and privacy
1. The raw deterministic report is sent unchanged to Telegram first.
2. Public-market excerpts are retained in per-chat/per-button memory for a maximum of 15 minutes and 3100 chars. No durable AI transcript is written to disk. Telegram group chats do not show these private AI controls.
3. User explicitly requests the AI action; only then does the existing common Groq/Gemini/OpenAI/optional AskFuzz gateway run. This avoids routine token costs on scanner execution.
4. Private portfolio holdings, amounts, share quantity, average cost and P&L are NEVER passed as AI context. Personal portfolio performance questions are declined by the AI helper; general education only.
5. Missing/stale evidence is disclosed. No model-created numerical claims are accepted unless every number appears in the selected report or question. Canned news is excluded.
6. AI output is HTML-escaped and sent in a separate clearly marked message, not injected into a report, chart or PDF. When AI is unavailable or creates unsupported values, return a non-AI error/deterministic research check instead of presenting speculation.
7. Chart metadata is NOT the image itself. PDF quick-metrics context is NOT PDF extraction. Do not describe these as if the AI had read the chart pixels or the full PDF.

## Verification
At head commit `dd80d09b57a62fbf0d89bd7f8fdf374b9e26d9ba`:
- [AI Research Accuracy workflow](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40361635117) successful.
- [Group Natural Language](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40361635384) successful.
- [PDF Technical Setup](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40361635774) successful.
- [Full Research Bridge](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40361635179) successful.
- [Startup & Portfolio Regression](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/40361635303) successful.

The tests mock model calls. They do NOT prove live Groq/Gemini/OpenAI quota, rates, model access, bot-to-user Telegram delivery or data licensing; a staged private smoke check is required before merge/release.

## Release procedure
Stage the PR on an isolated staging bot using configured valid model keys and a private Telegram test chat. Run all buttons, intentionally ask for an invented target, inspect source/date limitations, verify credentials absent from logs, check private portfolio is excluded, then approve merge and Render deploy. Keep test data and messages out of public groups.
