# MSK Bot: private Owner Usage Analytics (no new usage quota)

Scope: MSK Telegram bot only. No AiAutoUpdate changes or shared channel data.
Tracking starts only after a database and Render environment variables are configured.
Recorded users/requests are not Telegram delivery acknowledgements.

## Owner commands (in private chat with @IndianstockAIbot)
- `/ownerclaim <setup key>`: one-time owner binding, secret verified against a strong Render-only key. DELETE the Telegram setup message afterward.
- `/ownerstats`: total users, daily active users, requests today/seven days, popular buttons. The owner account is excluded from the usage counts.
- `/ownerusers`: top 15 recorded users by number of interactions.
- `/myid`: shows a private user's own Telegram ID.

Private web dashboard: `https://aaa-github-auto-ai-advisory-py.onrender.com/owner/usage`.
The browser will request Basic authentication: username `owner`, password `MSK_OWNER_DASHBOARD_KEY`.
Access requires HTTPS. Password must be long/random, stored only in Render secrets.
Cache-Control no-store, iframe blocking, CSP, HTML escaping. Never place the key in a URL.
Do not give public access to personal user lists.

## Database
Existing MSK Neon project `MSK-Web-Advisory`, database `neondb`,
isolated schema `msk_bot_usage`; NOT AiAutoUpdate ledger.
Connection held only in Render variable `MSK_ANALYTICS_DATABASE_URL`.
No raw chat messages, analysis prompts, stocks searched or bot tokens are stored.
Stored fields: Telegram numeric user ID, optional public username and display name,
last seen, chat type, de-duplicated incoming update id, feature category, timestamp.
Add a suitable privacy notice and retention policy before broad public promotion.

### Idempotent schema setup
```sql
CREATE SCHEMA IF NOT EXISTS msk_bot_usage;
CREATE TABLE IF NOT EXISTS msk_bot_usage.users (
  user_id BIGINT PRIMARY KEY,
  username VARCHAR(80) NOT NULL DEFAULT '',
  display_name VARCHAR(80) NOT NULL DEFAULT '',
  chat_type VARCHAR(12) NOT NULL,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  request_count BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS msk_bot_usage.events (
  event_id BIGSERIAL PRIMARY KEY,
  update_id BIGINT NOT NULL UNIQUE,
  user_id BIGINT NOT NULL REFERENCES msk_bot_usage.users(user_id),
  feature VARCHAR(64) NOT NULL,
  chat_type VARCHAR(12) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS msk_usage_events_day_idx
 ON msk_bot_usage.events(created_at DESC);
CREATE TABLE IF NOT EXISTS msk_bot_usage.owner (
  slot BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (slot),
  telegram_id BIGINT NOT NULL UNIQUE,
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS msk_bot_usage.daily_notices (
  report_day DATE PRIMARY KEY,
  status VARCHAR(12) NOT NULL CHECK (status IN ('CLAIMED','SENT','UNKNOWN')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finalized_at TIMESTAMPTZ
);
```

## Render variables
- `MSK_ANALYTICS_DATABASE_URL`: private Neon PostgreSQL connection string.
- `MSK_OWNER_SETUP_KEY`: random 32+ character one-time owner claim code.
- `MSK_OWNER_DASHBOARD_KEY`: independent random 32+ character browser password.
- `MSK_UNLIMITED_USAGE=1`: turns off the existing daily `call_llm_with_limits` quota.
  Provider rate limits, infrastructure anti-abuse throttling and costs remain independent.

## Automatic notification
Best-effort owner message once each IST day after 20:00 while the Render
process is running. The DB claim prevents duplicate sends. If the Telegram
result is unknown, the notice is recorded `UNKNOWN`, not retried blindly.
**Render Free can sleep; 20:00 delivery is not guaranteed.** Use `/ownerstats`
for an on-demand report.

## What the data does NOT mean
- Total recorded users is not Telegram's full historical bot user count.
- Requests count accepted inbound interactions, not successful analysis jobs.
- No accurate request history before this deployment.
- Process restart while an async analytics write is queued may lose that event.
- This feature does not impose quotas. Existing anti-abuse per-minute throttles
  and API provider restrictions can still apply.
