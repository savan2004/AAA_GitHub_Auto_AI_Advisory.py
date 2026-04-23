# Bug Fixes & Improvements — v5.0 Production Fixed

## 🔴 Critical Bugs Fixed

### 1. `limits.py.py` — Wrong filename (import crash)
- **File:** `limits.py.py` → **renamed** `limits.py`
- **Impact:** Any code importing `limits` would `ModuleNotFoundError` on Render/Linux
- **Fix:** Renamed file, verified all imports use `limits` not `limits.py`

---

### 2. `ai_engine.py` — OpenAI block inside Gemini `try/except` (indentation bug)
- **Location:** `_call_ai()` function, ~line 200 in original
- **Symptom:** If Gemini raised ANY exception, OpenAI was silently skipped entirely
- **Fix:** Moved OpenAI block to correct indentation level (sibling of Gemini, not child)

---

### 3. `ai_engine.py` — AskFuzz integration was a stub (did nothing)
- **Original:** `_call_askfuzz_ai()` always returned `("", "AskFuzz: API not yet available")`
- **Fix:** Full HTTP integration with:
  - Real `POST https://api.askfuzz.ai/v1/query` call
  - `Authorization: Bearer <ASKFUZZ_API_KEY>` header
  - Proper 401/429/timeout error handling
  - Confidence score displayed in response
  - Activated by setting `ASKFUZZ_API_KEY` env var
  - Gracefully silent when key is absent (no error noise)

---

### 4. `ai_engine.py` — `ai_available()` excluded AskFuzz
- **Original:** Only checked GROQ / Gemini / OpenAI keys
- **Fix:** Now also returns `True` when `ASKFUZZ_API_KEY` is set, so AskFuzz-only deployments work

---

### 5. `main.py` — Webhook dedup `set` never properly capped
- **Original:** `_processed_updates.discard(min(...))` — removes only 1 entry, grows unbounded
- **Fix:** Replaced `set` with `deque(maxlen=1000)` — auto-trims, O(1) append, thread-safe with lock

---

### 6. `main.py` — HTML parse errors silently dropped messages
- **Original:** `bot.send_message(..., parse_mode="HTML")` — unescaped `<`, `>`, `&` in stock data caused Telegram 400 Bad Request; the exception was logged but the message was never re-sent
- **Fix:** New `safe_send()` helper — on HTML error, strips tags and re-sends as plain text

---

### 7. `main.py` — Fundamentals not printed properly
- **Original:** `f" PE (TTM) : {pe or 'N/A'}"` — all fields on same line, hard to read; `pe` could be `0.0` (falsy) and would show as N/A
- **Fix:** `frow()` helper with proper None guards, aligned columns, separate lines per field

---

### 8. `ai_engine.py` — Finnhub news `from` date hardcoded to `2024-01-01`
- **Original:** `"from": "2024-01-01"` — as of 2026 this returns zero results (Finnhub free tier limits history to 1 year)
- **Fix:** Rolling 30-day window: `from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")`

---

### 9. `ai_engine.py` — Chat history trimmed to 12 (caused context overflow on some providers)
- **Original:** `_chat_history[uid][-12:]`
- **Fix:** Reduced to 10 turns — keeps conversation coherent without overflowing GPT-4o-mini context

---

### 10. `main.py` — `get_live_price()` not used in portfolio (used slower `get_hist` instead)
- **Original:** `df = get_hist(sym, "5d")` then `df["Close"].iloc[-1]` — fetches full 5-day OHLCV just for LTP
- **Fix:** `get_live_price(sym)` — single price lookup with 5-min cache, much faster

---

## 🟡 Connector / Output Improvements

### 11. `main.py` — `build_adv()` shows news section
- Added `fetch_news(sym)` call to advisory card — now shows 1-2 recent headlines per stock

### 12. `main.py` — `/status` command + `📈 Status` button
- New `/status` command shows bot + AI health check at a glance

### 13. `main.py` — `build_breadth()` safe None guard
- Added `hit == 0` check — now shows friendly error instead of empty message

### 14. `main.py` — `build_scan()` hit counter
- Tracks how many stocks returned data; shows error only when zero succeeded

### 15. `config.py` — Added `ASKFUZZ_API_KEY` reference
- Centralised so operators know which env var to set

---

## 🟢 No Changes Needed

These files were reviewed and are correct as-is:
- `data_engine.py` — multi-source fetcher, caching, rate limiting all correct
- `fundamentals.py` — data_engine → Finnhub → yfinance priority chain correct
- `swing_trades.py` — 8-condition scoring, ATR-based SL/T1/T2 correct
- `history.py` — rolling 20-item history correct
- `market_news.py` — static fallback + hook correct
- `llm_wrapper.py` — GROQ/Gemini wrapper with model fallback correct
- `health_api.py` / `health_monitor.py` — correct

---

## Environment Variables Reference

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | Bot token from @BotFather |
| `WEBHOOK_URL` | ✅ | Your Render service URL |
| `GROQ_API_KEY` | Recommended | Free LLM (console.groq.com) |
| `GEMINI_API_KEY` | Optional | Google AI (aistudio.google.com) |
| `OPENAI_KEY` | Optional | OpenAI GPT-4o-mini |
| `ASKFUZZ_API_KEY` | Optional | India-focused finance AI |
| `TAVILY_API_KEY` | Optional | Live news search |
| `FINNHUB_API_KEY` | Optional | Fundamentals + news fallback |
| `ALPHA_VANTAGE_KEY` | Optional | News fallback |

At least one of: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_KEY`, or `ASKFUZZ_API_KEY` must be set for AI features.

---

## Round 2 Fixes (from live Telegram output analysis)

### F1. Swing trade conditions truncated — `<` `>` stripped by Telegram HTML
- **Symptom:** `"Trend (price  25, -DI > +DI"` — arrows and comparison operators eaten
- **Fix:** All condition strings in `swing_trades.py` now use HTML entities:
  `>` → `&gt;`, `<` → `&lt;`, `&` → `&amp;`

### F2. Swing trade card improved — Entry Zone, R:R ratio, full conditions
- Added `📥 Entry Zone: ₹X – ₹Y` (±0.5% around LTP)
- Added `⚖️ Risk:Reward: 1:X` calculated from ATR
- All conditions now listed in full, not truncated to 3

### F3. Fundamentals all N/A for BEL (and many stocks without FINNHUB_API_KEY)
- **Root cause:** Yahoo v10 blocked + no Finnhub key = zero fallback
- **Fix:** Added **Screener.in scraper** as Source 2 — requires **NO API key**
  Extracts: PE, ROE, D/E, Market Cap, Revenue, 52W H/L, Book Value, Div Yield, EPS
  Priority: data_engine → **Screener.in** → Finnhub → yfinance

### F4. AI responses generic and vague ("could", "might", "potentially")
- **Fix:** Rewrote `CHAT_SYSTEM` prompt with strict output format enforcement
- Added exact OUTPUT FORMAT templates for all 5 topics (Valuation, Picks, Update, Swing, Options)
- Banned hedge words: "could", "might", "potentially", "I think"
- AI must start with the exact metric asked for

### F5. `ai_insights()` stock card — generic bullets not citing live data
- **Fix:** Prompt now passes `rsi_label` ("OVERBOUGHT", "OVERSOLD", "neutral zone")
- Forces VERDICT line: `BUY / HOLD / AVOID — [one sentence with exact numbers]`
- System prompt: "Never say could/might/potentially. Output ONLY the requested format."

### F6. Screener signals contradictory (SBIN RSI 81.4 = Neutral + OVERBOUGHT)
- **Original logic:** signal computed independently of trend — could conflict
- **Fix:** Smarter signal hierarchy:
  1. RSI < 35 → OVERSOLD
  2. RSI > 72 → OVERBOUGHT
  3. price > EMA20 > EMA50 + RSI > 50 + positive → UPTREND (strong)
  4. price < EMA20 < EMA50 + RSI < 50 → DOWNTREND (avoid)
  5. price > EMA20 + RSI 45–65 + positive → BUY ZONE
  6. else → WAIT

### F7. News showing website names not headlines (MSN, Yahoo Finance page titles)
- **Fix in `build_news()`:**
  - Added `_NEWS_JUNK_PATTERNS` filter — rejects titles containing site names
  - Added `search_depth: advanced` and `include_domains` targeting financial publishers
  - Added **MoneyControl RSS fallback** — free, no key, real stock headlines
- **Fix in `fetch_news()` (stock news):**
  - Same junk filter applied to per-stock Tavily results
  - Added MoneyControl buzzing stocks RSS as Source 4

### F8. Market Breadth — no RSI shown
- **Fix:** Added RSI(5-day) and Bull/Bear label to each index row
  e.g. `🔴 NIFTY IT: 30,314.35 (-4.46%) | RSI:38.2 | Bear`
