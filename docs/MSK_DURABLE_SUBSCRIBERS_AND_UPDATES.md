# MSK Bot P0 — durable Telegram update and subscriber ledger

**Scope:** only MSK Bot, not AiAutoUpdate, MSK Web or Algo. Draft PR #94.
This is a release *candidate*, not evidence that production subscribers or
customer portfolios have been migrated.

## Why this is needed

The old webhook has a `deque(maxlen=1000)` and set of update IDs in process
memory. A restart loses them, so Telegram can replay the same update. For
`/buy` or `/sell`, replay can mutate a portfolio twice. The optional
`msk_usage` service counts requests but **cannot** deduplicate bot actions:
it is best-effort analytics and has a different purpose.

`msk_durable_state.py` adds a separate opt-in PostgreSQL schema:

| Table | Data and purpose |
|---|---|
| `msk_bot_state.telegram_updates` | Unique Telegram update ID, optional user ID, claimed/dispatched/failed stage, timestamps |
| `msk_bot_state.subscribers` | Telegram user ID, inactive-by-default membership, expiry, version, seen-at timestamps |
| `msk_bot_state.entitlements` | Feature code and explicit expiry; no feature is automatically granted |
| `msk_bot_state.review_events` | Unique operator event key, payload digest and reviewer reference; no raw payment information |

Only the minimal Telegram numeric ID is stored; incoming messages, names,
phone numbers, credentials and other message content are not kept in this
ledger. Membership and entitlements cannot be activated by an incoming
Telegram update. **There is no payment enrollment/charge handler.**

## Release flags (default OFF)

To prepare **private staging only**, use a dedicated, access-restricted,
backed-up MSK PostgreSQL database with TLS:

```text
MSK_DURABLE_UPDATES_ENABLED=true
MSK_STATE_DATABASE_URL=<secret Postgres URI ending ?sslmode=require or stronger>
```

The URI belongs in an environment-secret manager, **never GitHub, a Telegram
message, an issue comment or a log**. No secret is installed by this PR.
The `msk_bot_state` schema is not shared with AiAutoUpdate's Neon ledger.
The default `MSK_DURABLE_UPDATES_ENABLED` is OFF and the old production
service is unchanged until an approved, proven cutover.

When explicitly enabled, failure to connect/initialize the ledger **fails
startup**. Mid-run DB outage causes the webhook to return **HTTP 503** before
processing; `/health/ready` also reports degraded if the ledger fails a
read-only DB probe. Duplicate `update_id` values are accepted with HTTP 200
but **never dispatched again**. Claims are unique across workers/restarts.

## Dispatch lifecycle and limits

1. Authenticate Telegram's webhook-secret header; reject malformed IDs and
   oversized bodies. Do not record incoming text.
2. Under a bounded webhook slot, atomically insert the update claim into
   PostgreSQL and record a new sender as **inactive/unentitled**.
3. If already claimed, dispatched or failed, do not invoke any Telegram
   handler again. If the database is unavailable, return **503**, not 200.
4. After successful queue submission, return HTTP 200. The dispatch worker
   records `dispatched` if the handler returned, or `failed` if it raised.
   **Dispatched does not prove delivery of Telegram messages**, final PDF
   generation, or success of subsequently spawned background work.
5. If executor submission *definitely* fails before a worker is scheduled,
   remove that unstarted claim and return 503, permitting Telegram retry.
6. If the service crashes after claim, the row stays `claimed`; if a
   handler raises, it stays `failed`. **Neither is ever automatically
   replayed**, because portfolio side effects may already have occurred.
   These require a human reconciliation against user holdings and Telegram
   evidence before any manually designed recovery procedure.

This is **durable at-most-once handler dispatch**, not exactly-once end-to-end
delivery. At-most-once intentionally prioritizes prevention of duplicate
portfolio mutation over blind automatic recovery of uncertain outcomes.
A future transaction-scoped portfolio command idempotency key and a verified
Telegram delivery outbox would be needed to promise exactly-once effects.

Do not delete update IDs, drop the table, turn off the flag during active
traffic, or assume a fresh process-memory queue is a safe rollback. Use a
maintenance window and reconcile claimed/failed records first.

## Subscriber review and expiry

`apply_reviewed_membership()` is an internal **operator-only** method;
**no Telegram command or payment callback can call it**. It requires an
opaque unique review event key, reviewer reference, current subscriber
version, legal status, and an aware UTC-compatible expiry for active/trial.
Membership+entitlements+journal are one SQL transaction. Identical approval
replays are no-ops; conflicting event-key reuse is rejected. Concurrent or
stale review events cannot overwrite a newer subscriber version.
`is_entitled()` requires both active/trial unexpired membership and a
matching, unexpired feature entitlement. Inactive, blocked, expired, missing
or unreviewed subscribers get no premium features. Existing free bot menu
behavior is not changed or restricted by this opt-in ledger.

## Operational checks (aggregate-only, no client holdings in logs)

After a validated staging deployment and schema creation, run read-only
aggregate SQL with authorized operator credentials:

```sql
SELECT stage, COUNT(*) FROM msk_bot_state.telegram_updates GROUP BY stage;
SELECT status, COUNT(*) FROM msk_bot_state.subscribers GROUP BY status;
SELECT COUNT(*) AS unresolved
FROM msk_bot_state.telegram_updates
WHERE stage IN ('claimed','failed');
```

Test the same synthetic Telegram update twice and across a fresh bot process;
verify **one handler dispatch**, not two. Verify DB outage returns 503 and
marks readiness degraded. Check that users remain inactive until operator
review, expired rights stop immediately, stale review events are blocked,
and a fresh `pg_dump` restores *both* subscriber and update ledgers into
a separately named staging database.

## Non-negotiable production cutover gates

- Recover the **actual** live `portfolio_data.json` through an approved
  operator process **without restarting, rebuilding or redeploying** the
  current Render bot; independently verify a dated, encrypted, off-host
  backup, SHA-256 and full holdings readback.
- Verify production database backup retention and isolated restore for
  portfolio positions, subscribers, review audit, Telegram update claims.
- Test concurrent duplicate claims, crash-after-claim refusal, DB outage,
  `/start`, `/buy`, `/sell`, `/portfolio`, callback actions, entitlement
  expiry and provider outages in **private staging**. Record exact CI SHA,
  staging Telegram message IDs, masked log times, rollback owner.
- Confirm token rotation/security and membership approval policy; no
  paid subscriptions or payment processing until separate privacy,
  entitlement, expiry, webhook-signature and payment idempotency reviews.
- Use one reviewed release candidate, staged maintenance/freeze window and
  rollback plan. Do not merge PR #15/#94/#104 wholesale without reconciling
  overlapping code. No live Render deployment is authorized by tests alone.

## Synthetic CI evidence scope

`tests/test_msk_durable_state.py` covers actual webhook code extracted
without live Telegram imports: duplicate after mocked restart, DB outage,
unstarted queue rejection, failed dispatch and claimed rows, malicious IDs,
sender identity, flags and slot cleanup. The existing menu audit and health
endpoints remain in regression suites.

`tests/pr94_postgres_integration.py` exercises **real isolated PostgreSQL
16** with synthetic IDs: concurrent claims (1/8 winners), restart,
claimed/failed suppression, active subscription expiry, audit replay and
stale-version rejection, revocation, `pg_dump/pg_restore` independent DB
verification. This proves a control path on **synthetic data only**, not a
migration or a paid subscriber activation in production.
