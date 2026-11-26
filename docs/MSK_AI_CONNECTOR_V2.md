# MSK AI connector v2 — new main provider module

## Code decision
- `msk_ai_connector_v2.py`: all HTTP calls, provider settings, fallback, numeric-grounding and error handling; **no dependency added**.
- `ai_engine.py`: keeps all existing public analysis, chat and legacy status function names and signatures, delegates to connector v2.
- Use present source collectors for real prices, financial ratios, completion-session checks, stock scoring and risk rules. The language model **never creates raw stock data**.

## What changed
1. Groq now uses `openai/gpt-oss-120b` (rather than the retired `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`).
2. Gemini uses `gemini-3.5-flash-lite` over the documented HTTPS API, without relying on the old `google-generativeai` `gemini-1.5-flash` client.
3. **Zero additional spending by default:** Groq and Gemini configured credentials are eligible; OpenAI API is permitted only with `MSK_AI_ALLOW_PAID=1`; AskFuzz only with `MSK_AI_ALLOW_ASKFUZZ=1`. Existing API keys alone are not permission to bill. Usage within any third-party's own plan is still subject to their limits and pricing.
4. Each configured provider receives **at most one HTTP request per user action**. No blind retries, no token-heavy multi-model cycling. Once a valid answer returns, stop.
5. For `ai_insights`, `structured_investment_outlook`, `long_term_view`, reject numeric tokens absent from the supplied prompt. A safe deterministic fallback is displayed rather than publishing a made-up target.
6. Never expose vendor exception bodies, secrets, private chain-of-thought tags, redirects or malformed responses to end users. No chat topic state leaked across users using artificial user ID 0.

## Important boundaries
- A grounded numeral can still accompany a *wrong explanation*. **100% accuracy or 100/100 profitable stocks is not a credible guarantee.** Numeric grounding is one guard, not an audit.
- No market prices, filings, financials, recommendations or SEBI suitability are verified by this connector. The research-bank independent release gate remains `WITHHELD`.
- `GROQ_API_KEY`, `GEMINI_API_KEY`/ `GOOGLE_API_KEY` are optional existing secrets. Real provider access, model permissions and free quota must be checked before production enablement.
- This PR adds no new autonomous trading, Telegram sends, database operations or customer approval.
- A provider can change models or quotas again: use `MSK_GROQ_MODEL` / `MSK_GEMINI_MODEL` only after verifying the model in current official documentation. Explicitly retired models are blocked.

## Proof
`python -m unittest discover -s tests -p 'test_msk_ai_connector_v2.py' -v`

Mocks are offline; no token usage and no billable HTTP calls. Live private integration and Render deployment are separate release requirements.
