"""Opt-in MSK Bot-only durable Telegram intake and subscriber ledger.

No paid entitlement is enabled automatically. Telegram updates are claimed
durably *before* asynchronous dispatch: duplicate claims survive restarts.
A crash after claiming can leave an update unresolved; never automatically
replay a potentially mutating /buy or /sell (manual reconciliation required).
'dispatched' means handler returned, NOT delivered to Telegram.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from postgres_portfolio import _connect_postgres, _validate_dsn

_USER_LIMIT = 9223372036854775807
_FEATURE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
_STATUSES = frozenset(("inactive", "trial", "active", "expired", "blocked"))

_SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS msk_bot_state",
    """CREATE TABLE IF NOT EXISTS msk_bot_state.subscribers (
        user_id BIGINT PRIMARY KEY CHECK (user_id > 0),
        status TEXT NOT NULL DEFAULT 'inactive'
            CHECK (status IN ('inactive','trial','active','expired','blocked')),
        expires_at TIMESTAMPTZ,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        version BIGINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS msk_bot_state.entitlements (
        user_id BIGINT NOT NULL REFERENCES msk_bot_state.subscribers(user_id),
        feature TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(user_id, feature)
    )""",
    """CREATE TABLE IF NOT EXISTS msk_bot_state.review_events (
        event_key TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES msk_bot_state.subscribers(user_id),
        payload_sha256 CHAR(64) NOT NULL,
        reviewer_ref TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS msk_bot_state.telegram_updates (
        update_id BIGINT PRIMARY KEY CHECK(update_id >= 0),
        user_id BIGINT REFERENCES msk_bot_state.subscribers(user_id),
        stage TEXT NOT NULL DEFAULT 'claimed'
            CHECK (stage IN ('claimed','dispatched','failed')),
        claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS msk_bot_state.telegram_api_receipts (
        chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL CHECK(message_id > 0),
        kind TEXT NOT NULL CHECK(kind IN ('text', 'document', 'photo')),
        acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(chat_id, message_id)
    )""",
    """CREATE INDEX IF NOT EXISTS msk_updates_unresolved
       ON msk_bot_state.telegram_updates(claimed_at)
       WHERE stage IN ('claimed','failed')""",
)


class DurableStateError(RuntimeError):
    """Reject unsafe data or unavailable storage without logging credentials."""


def _id(value, *, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= _USER_LIMIT:
        raise ValueError("Invalid Telegram identifier")
    return value


def _utc_aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Expiry needs an aware datetime")
    return value.astimezone(timezone.utc)


class DurableBotState:
    def __init__(self, dsn, *, connector=None):
        self._dsn = _validate_dsn(dsn)
        self._connector = connector or _connect_postgres
        # If enabled but unavailable, fail startup; never silently turn off
        # durable deduplication or substitute the in-memory queue.
        with self._connector(self._dsn) as conn:
            # Multiple webhook workers can boot simultaneously. Serialize
            # CREATE SCHEMA/TABLE/INDEX to avoid PostgreSQL catalog races.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (628705164101,))
            for sql in _SCHEMA:
                conn.execute(sql)

    def ping(self):
        """Read-only readiness check; never treats a network outage as healthy."""
        with self._connector(self._dsn) as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)

    def claim(self, update_id, user_id=None):
        """Atomic first-claim wins across workers and restarts.

        Subscriber identity is recorded as inactive only; no rights granted.
        Never stores Telegram message content, names or API credentials.
        """
        update_id = _id(update_id, allow_zero=True)
        if user_id is not None:
            user_id = _id(user_id)
        with self._connector(self._dsn) as conn:
            if user_id is not None:
                conn.execute(
                    """INSERT INTO msk_bot_state.subscribers(user_id)
                       VALUES (%s) ON CONFLICT(user_id)
                       DO UPDATE SET last_seen=NOW()""", (user_id,)
                )
            row = conn.execute(
                """INSERT INTO msk_bot_state.telegram_updates(update_id,user_id)
                   VALUES (%s,%s) ON CONFLICT(update_id) DO NOTHING
                   RETURNING update_id""", (update_id, user_id)
            ).fetchone()
            return row is not None

    def finish(self, update_id, *, dispatched):
        """Record dispatch outcome, not Telegram delivery or financial execution."""
        update_id = _id(update_id, allow_zero=True)
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """UPDATE msk_bot_state.telegram_updates
                   SET stage=%s, resolved_at=NOW()
                   WHERE update_id=%s AND stage='claimed'
                   RETURNING update_id""",
                ("dispatched" if dispatched else "failed", update_id)
            ).fetchone()
            if row is None:
                raise DurableStateError("Update claim missing or already resolved")

    def abandon_unscheduled(self, update_id):
        """Only for an executor submission that certainly did not occur."""
        update_id = _id(update_id, allow_zero=True)
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """DELETE FROM msk_bot_state.telegram_updates
                   WHERE update_id=%s AND stage='claimed'
                   RETURNING update_id""", (update_id,)
            ).fetchone()
            if row is None:
                raise DurableStateError("Cannot abandon update already resolved")

    def record_delivery_ack(self, chat_id, message_id, kind):
        """Persist a Telegram Bot API send acknowledgment, not a user-read receipt.

        This is intentionally separate from webhook stage='dispatched'.
        No message text, PDFs, credentials or user names are retained.
        """
        if (type(chat_id) is not int or chat_id == 0 or
                not -_USER_LIMIT <= chat_id <= _USER_LIMIT):
            raise ValueError("Invalid Telegram chat ID")
        message_id = _id(message_id)
        if kind not in ("text", "document", "photo"):
            raise ValueError("Invalid Telegram acknowledgment kind")
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """INSERT INTO msk_bot_state.telegram_api_receipts
                   (chat_id,message_id,kind) VALUES (%s,%s,%s)
                   ON CONFLICT(chat_id,message_id) DO NOTHING
                   RETURNING message_id""",
                (chat_id, message_id, kind)
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    """SELECT kind FROM msk_bot_state.telegram_api_receipts
                       WHERE chat_id=%s AND message_id=%s""",
                    (chat_id, message_id)
                ).fetchone()
                if existing != (kind,):
                    raise DurableStateError("Conflicting Telegram API send receipt")
                return False
            return True

    def get_delivery_ack(self, chat_id, message_id):
        """Read-only presence check for private staging and restore evidence."""
        if type(chat_id) is not int or chat_id == 0:
            raise ValueError("Invalid chat")
        message_id = _id(message_id)
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """SELECT kind FROM msk_bot_state.telegram_api_receipts
                   WHERE chat_id=%s AND message_id=%s""",
                (chat_id, message_id)
            ).fetchone()
            return row[0] if row else None

    def get_update_stage(self, update_id):
        update_id = _id(update_id, allow_zero=True)
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                "SELECT stage FROM msk_bot_state.telegram_updates WHERE update_id=%s",
                (update_id,)
            ).fetchone()
            return row[0] if row else None

    def get_subscriber(self, user_id):
        user_id = _id(user_id)
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """SELECT status, expires_at, version
                   FROM msk_bot_state.subscribers WHERE user_id=%s""",
                (user_id,)
            ).fetchone()
        if not row:
            return {"status": "unknown", "expires_at": None, "version": None}
        return {"status": row[0], "expires_at": row[1], "version": row[2]}

    def is_entitled(self, user_id, feature, *, now=None):
        """Default-deny, requires BOTH valid reviewed membership and feature."""
        user_id = _id(user_id)
        if not isinstance(feature, str) or not _FEATURE.fullmatch(feature):
            return False
        now = _utc_aware(now or datetime.now(timezone.utc))
        with self._connector(self._dsn) as conn:
            row = conn.execute(
                """SELECT 1 FROM msk_bot_state.subscribers s
                   JOIN msk_bot_state.entitlements e ON e.user_id=s.user_id
                   WHERE s.user_id=%s AND s.status IN ('active','trial')
                     AND s.expires_at>%s AND e.feature=%s
                     AND e.expires_at>%s""",
                (user_id, now, feature, now)
            ).fetchone()
        return row is not None

    def apply_reviewed_membership(self, user_id, status, expires_at, features,
                                  *, event_key, reviewer_ref, expected_version,
                                  now=None):
        """Operator-only API, deliberately not exposed as any Telegram handler.

        Idempotent event-key journal, atomic membership+feature replacement.
        No payment confirmation or automatic upgrade is implemented here.
        """
        user_id = _id(user_id)
        if status not in _STATUSES:
            raise ValueError("Invalid membership status")
        if not isinstance(features, (tuple, list, set)):
            raise ValueError("Features must be a collection")
        if len(features) > 32 or any(
                not isinstance(f, str) or not _FEATURE.fullmatch(f)
                for f in features):
            raise ValueError("Invalid entitlement name or feature count")
        cleaned = sorted(set(features))
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("Expected subscriber version must be nonnegative")
        if status not in ("active", "trial") and cleaned:
            raise ValueError("Inactive status cannot grant entitlements")
        now = _utc_aware(now or datetime.now(timezone.utc))
        expiry = _utc_aware(expires_at) if expires_at is not None else None
        if status in ("active", "trial") and (expiry is None or expiry <= now):
            raise ValueError("Active membership requires future expiry")
        if not isinstance(event_key, str) or not _KEY.fullmatch(event_key):
            raise ValueError("Invalid unique review event key")
        if not isinstance(reviewer_ref, str) or not _KEY.fullmatch(reviewer_ref):
            raise ValueError("Invalid reviewer reference")
        content = json.dumps({
            "user_id": user_id, "status": status, "expiry": expiry.isoformat()
            if expiry else None, "features": cleaned, "reviewer_ref": reviewer_ref
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        with self._connector(self._dsn) as conn:
            conn.execute(
                """INSERT INTO msk_bot_state.subscribers(user_id)
                   VALUES (%s) ON CONFLICT(user_id) DO NOTHING""",
                (user_id,)
            )
            # Serialize all changes for this subscriber, including distinct
            # event keys, so approval events are fully atomic.
            locked = conn.execute(
                "SELECT version FROM msk_bot_state.subscribers "
                "WHERE user_id=%s FOR UPDATE", (user_id,)
            ).fetchone()
            if locked is None:
                raise DurableStateError("Subscriber lock missing")
            claimed = conn.execute(
                """INSERT INTO msk_bot_state.review_events
                   (event_key,user_id,payload_sha256,reviewer_ref)
                   VALUES (%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING
                   RETURNING event_key""",
                (event_key, user_id, digest, reviewer_ref)
            ).fetchone()
            if claimed is None:
                existing = conn.execute(
                    """SELECT user_id,payload_sha256,reviewer_ref FROM
                       msk_bot_state.review_events WHERE event_key=%s""",
                    (event_key,)
                ).fetchone()
                if existing != (user_id, digest, reviewer_ref):
                    raise DurableStateError("Review event key reused with conflicting data")
                return False
            if locked[0] != expected_version:
                raise DurableStateError("Stale subscriber version; reload before review")
            conn.execute(
                """UPDATE msk_bot_state.subscribers
                   SET status=%s, expires_at=%s,
                       last_seen=NOW(), version=version+1
                   WHERE user_id=%s""", (status, expiry, user_id)
            )
            conn.execute(
                "DELETE FROM msk_bot_state.entitlements WHERE user_id=%s",
                (user_id,)
            )
            for feature in cleaned:
                conn.execute(
                    """INSERT INTO msk_bot_state.entitlements
                       (user_id,feature,expires_at) VALUES (%s,%s,%s)""",
                    (user_id, feature, expiry)
                )
        return True


def build_durable_bot_state(*, environ=None, connector=None):
    env = os.environ if environ is None else environ
    enabled = env.get("MSK_DURABLE_UPDATES_ENABLED", "").strip().lower()
    if enabled not in ("", "false", "true"):
        raise DurableStateError("Unknown MSK_DURABLE_UPDATES_ENABLED value")
    if enabled != "true":
        return None
    dsn = env.get("MSK_STATE_DATABASE_URL")
    if not dsn:
        raise DurableStateError("Durable updates enabled without MSK_STATE_DATABASE_URL")
    return DurableBotState(dsn, connector=connector)
