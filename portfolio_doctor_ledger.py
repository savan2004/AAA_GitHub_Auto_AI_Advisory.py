"""Durable, metadata-only private Portfolio Doctor delivery ledger.

Separate, opt-in PostgreSQL connection; never touches legacy portfolio tables.
If the ledger cannot safely claim an upload, report delivery is disabled.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


class LedgerUnavailable(RuntimeError):
    pass


class PortfolioDoctorLedger:
    def __init__(self, dsn, secret):
        if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
            raise LedgerUnavailable("Independent doctor ledger database unavailable")
        if not secret or len(secret) < 32:
            raise LedgerUnavailable("Dedicated doctor ledger secret unavailable")
        self.dsn, self.secret = dsn, secret.encode("utf-8")

    @classmethod
    def from_env(cls):
        return cls(os.getenv("MSK_PORTFOLIO_DOCTOR_LEDGER_DATABASE_URL", ""),
                   os.getenv("MSK_PORTFOLIO_DOCTOR_LEDGER_SECRET", ""))

    def _digest(self, kind, value):
        raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
        return hmac.new(self.secret, kind.encode("ascii") + b":" + raw,
                        hashlib.sha256).hexdigest()

    def _connection(self):
        try:
            import psycopg
            return psycopg.connect(self.dsn, sslmode="require",
                                   connect_timeout=5, prepare_threshold=None)
        except Exception as exc:
            raise LedgerUnavailable("Private delivery ledger is unavailable") from exc

    def reserve(self, report_id, user_id, payload):
        user_hash = self._digest("user", user_id)
        upload_hash = self._digest("upload", payload)
        today = datetime.now(IST).date()
        try:
            with self._connection() as conn:
                conn.execute("SET LOCAL statement_timeout = '6000ms'")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS msk_portfolio_doctor_delivery_v1 (
                        report_id TEXT PRIMARY KEY,
                        user_hash TEXT NOT NULL,
                        upload_hash TEXT NOT NULL,
                        request_day DATE NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('RESERVED', 'READY', 'SENDING', 'SENT', 'FAILED', 'UNCERTAIN')),
                        telegram_message_id BIGINT,
                        telegram_file_id TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (user_hash, upload_hash, request_day)
                    )
                """)
                row = conn.execute("""
                    INSERT INTO msk_portfolio_doctor_delivery_v1
                        (report_id, user_hash, upload_hash, request_day, state)
                    VALUES (%s, %s, %s, %s, 'RESERVED')
                    ON CONFLICT (user_hash, upload_hash, request_day) DO NOTHING
                    RETURNING report_id
                """, (report_id, user_hash, upload_hash, today)).fetchone()
                return row is not None
        except Exception as exc:
            raise LedgerUnavailable("Cannot reserve durable private report") from exc

    def _transition(self, report_id, old_state, new_state, message_id=None, file_id=None):
        try:
            with self._connection() as conn:
                conn.execute("SET LOCAL statement_timeout = '6000ms'")
                cursor = conn.execute("""
                    UPDATE msk_portfolio_doctor_delivery_v1
                    SET state = %s, telegram_message_id = COALESCE(%s, telegram_message_id),
                        telegram_file_id = COALESCE(%s, telegram_file_id), updated_at = now()
                    WHERE report_id = %s AND state = %s
                """, (new_state, message_id, file_id, report_id, old_state))
                if cursor.rowcount != 1:
                    raise LedgerUnavailable("Private report state conflict")
        except LedgerUnavailable:
            raise
        except Exception as exc:
            raise LedgerUnavailable("Cannot persist private delivery state") from exc

    def mark_ready(self, report_id):
        self._transition(report_id, "RESERVED", "READY")

    def mark_sending(self, report_id):
        self._transition(report_id, "READY", "SENDING")

    def mark_sent(self, report_id, message_id, file_id):
        if not isinstance(message_id, int) or message_id <= 0 or not file_id:
            raise LedgerUnavailable("Telegram delivery receipt incomplete")
        self._transition(report_id, "SENDING", "SENT", message_id, str(file_id))

    def mark_failed(self, report_id):
        """No retry: a transport failure may already have sent the document."""
        try:
            with self._connection() as conn:
                conn.execute("SET LOCAL statement_timeout = '6000ms'")
                conn.execute("""
                    UPDATE msk_portfolio_doctor_delivery_v1
                    SET state = CASE WHEN state = 'SENDING' THEN 'UNCERTAIN' ELSE 'FAILED' END, updated_at = now()
                    WHERE report_id = %s AND state IN ('RESERVED', 'READY', 'SENDING')
                """, (report_id,))
        except Exception as exc:
            raise LedgerUnavailable("Cannot persist private failure") from exc
