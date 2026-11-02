# MSK Web Advisory — Phase 1 standalone backend (draft, NOT deployed)

Scope: **only** `web_advisory/backend/`. No imports from `main.py`,
Telegram handlers, the MSK bot's Render process, or AiAutoUpdate.

## Existing system inspected

- The preview is a **Render static site** publishing `web_advisory/` from
  `web/msk-web-advisory-mvp-20261027`.
- Frontend load order is `config.js`, `app.js`, `features.js`,
  `market_widget.js`, `dashboard.js` and `import_center.js`.
- `config.js` currently references a Neon Function URL. That project's
  `neon_function/index.mjs` is explicitly **not** a generative AI backend.
- `market_widget.js` independently calls TejHQ for unadjusted EOD OHLCV;
  its uptime, licensing and cross-origin accessibility are not guaranteed.
- `app.js` uses local research Q&A and browser print, not secure generative
  AI or an eight-page independently validated PDF.
- This Phase 1 introduces **no new live frontend connection**. It cannot
  silently replace data sources used by the preview.

## Provider contract

`GET /api/health`: informational health, not proof of upstream availability.
`GET /api/v1/equities/{SYMBOL}/history`: read-only historical bars.

Example response schema:

```json
{
  "symbol": "BEL", "exchange": "NSE", "interval": "1d",
  "source": "operator_provisioned_csv",
  "verified": false, "live": false, "adjusted": false,
  "asof": "2026-10-30", "count": 55,
  "bars": [{"timestamp":"2026-10-30","open":100,"high":102,"low":99,"close":101,"volume":1234}]
}
```

The example is for schema illustration, **not** a quote or a runnable
55-bar dataset. No automatic company-fundamental values are claimed.

Provider errors use `{"error":"code"}` with 400 invalid symbols,
404 absent symbols, 422 invalid input dataset, 503 unconfigured provider.
Never invent fallback prices, forward targets or account statements.

**Default is disabled.** No market-data license is presumed; an operator must
supply local CSV files with audited rights and provenance. Filenames are
strictly SYMBOL.csv. Files must contain 50–400 ascending dated OHLCV bars.
The adapter rejects nonfinite, inconsistent, future and duplicate values.
The output explicitly marks source data unverified and not live.

## Local-only development

From repository root:

```bash
python -m unittest discover -s web_advisory/backend/tests -v
MSK_WEB_DATA_DIR=/path/to/operator-approved-csv python web_advisory/backend/server.py
```

By default it listens on `127.0.0.1:8765`; the optional `PORT` environment
variable selects another local port. Do not set `MSK_WEB_HOST=0.0.0.0`
until external network controls and security review are complete.

No pip packages are required. No API keys should be placed in browser JavaScript
or committed files. The API is anonymous read-only and is **not production
authorization** for a public hosted backend, even with restrictive CORS.
Before public deployment add gateway rate limits, abuse protection, TLS,
access logs with redaction, request size enforcement, monitoring and source
license approval. A separate authentication/authorization review is required
before AI, portfolios, user uploads, DB writes, or personalized queries.

## Test and rollout gates (not yet passed)

1. Run the offline Python unittest suite and independently inspect results.
2. Verify no files outside `web_advisory/` changed; confirm no Telegram or
   AiAutoUpdate dependency.
3. Use a local fixture with exactly 50 / 400 candles; test 49 / 401,
   stale/future dates, duplicate timestamps, NaN/Inf, impossible OHLC,
   missing columns, oversized file and malformed UTF-8.
4. Verify allowlisted CORS and no `Access-Control-Allow-Credentials`;
   POST/OPTIONS do not enable writes.
5. Review file path/symlink safety and operator CSV provenance.
6. Test gateway security for public hosting: TLS, request quotas, abuse
   prevention and safe error logging. Do not host publicly before acceptance.
7. Independently verify actual TejHQ response schema and data rights before
   designing a future adapter; never proxy third-party providers without
   authorization.
8. Once secure backend is deployed *as a separate service*, develop a
   website-only frontend client on another reviewable PR. Require explicit
   source/date badges, failure UX and browser integration tests.
9. Later phases: fundamental-data interface, cache with provenance and
   freshness ledger, eight-page PDF acceptance, screened universe and
   grounded AI with server-only credentials.
10. Confirm a separate Render deployment and external smoke checks before
    declaring the API live. A draft PR is **not** a deployment.

## Rollback

No changes to existing Render static site, build command, GitHub main,
bot service, Telegram config or AiAutoUpdate. Reject/close this draft PR
without affecting the live website.
