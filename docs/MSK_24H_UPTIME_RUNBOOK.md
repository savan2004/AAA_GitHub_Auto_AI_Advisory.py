# MSK Bot — 24-hour availability acceptance (operator runbook)

## What is verified

- The bot runs on Render `IndianstockAIbot`, with a Free, single-instance service as checked 25 October 2026.
- Existing GET `/` returns `{"status":"ok"}` and is used by Render health checking.
- Render Free web services can sleep after 15 minutes without incoming traffic; a later request can cold-start the process. A successful deployment is not proof of 24-hour uptime.
- Current webhook responds HTTP 200 after in-memory task scheduling, before Telegram business processing finishes. A crash after ACK can lose a message. Restart resets in-memory processed-update IDs.
- A locally stored portfolio JSON file can be lost across restarts or free spin-down. Do not activate user portfolios without durable data and tested migration.

## New health checks (after explicit review/deployment)

- GET `/health/live`: `200` when the Flask process serves requests, with **only this process's** `process_uptime_seconds`.
- GET `/health/ready`: `200` when the local webhook executor is accepting submissions; `503` if it is shut down or a required webhook secret is missing.
- Neither endpoint verifies Telegram API access, completed message processing, upstream NSE/BSE data, database durability or end-to-end delivery.
- Do not publish internal credentials or Telegram chat IDs through health endpoints.

## External monitoring (not enabled by this PR)

1. An operator should use an **independent**, trusted uptime monitor external to Render with probes against `/health/live` after deployment. Until then use GET `/`.
2. Run a local one-shot check with `python scripts/uptime_probe.py`. It exits 0 on an HTTP 200 response with expected status and 1 on failure. This is a *single HTTP observation*, not a 24-hour uptime claim.
3. Record every probe's UTC timestamp, HTTP status, response latency and errors in a durable monitor store. Record missed observations separately; do not treat missing intervals as healthy.
4. For 24-hour assessment, pick a probe frequency (e.g. 5 minutes), require the complete observation window, report numerator/denominator, and **never** claim 100% uptime based on sparse or absent data.
5. Include failure alerts, repeated 5xx/timeouts, p95/p99 latency, and recovery observations, and manually confirm private Telegram smoke-send / receipt when authorized.
6. Test cold starts, actual deployment and rollback, provider outage, duplicate webhook, and portfolio restore before declaring the launch production ready.

## No-code / infrastructure constraints

- Render Free is not designed to guarantee an always-on production bot. More pings can wake or delay spin-down but cannot create a provider SLA, durable local disk or uninterrupted availability.
- Render currently checks `/`; switching Render's health check to `/health/ready` is a **separate deployment configuration change** after route testing and operator approval.
- An always-on compute plan and suitable durable PostgreSQL storage will be required for a meaningful production availability target; choose a budget and validate any free alternative's limits explicitly.
- Do not merge/release draft PRs or activate paid services without the owner's decision.

## CI evidence

- `tests/test_health_endpoints.py` uses AST extraction, isolated from tokens and external requests.
- `tests/test_uptime_probe.py` mocks HTTP success, 500, errors, malformed JSON and misleading degraded status; no network is contacted in CI.
- The existing `test_msk_menu_audit.py` suite imports these classes so the current PR CI gate executes them.
