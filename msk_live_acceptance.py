"""Single-run, owner-only live integration acceptance for the deployed MSK Bot.

Disabled unless MSK_LIVE_ACCEPTANCE_RUN_ID is explicitly configured.
Never runs against customer chats or changes portfolio holdings.  The separate
MSK portfolio PostgreSQL database is used only for a one-time run claim.
"""
import contextvars
import hashlib
import logging
import os
import re
import time
import threading

LOG = logging.getLogger("msk_acceptance")
_PROBE = contextvars.ContextVar("msk_acceptance_probe", default=None)


def capture_telegram_ack(kind, message_id, file_id=None, message_text=None):
    """Capture only Telegram API returns, never an assumed user delivery."""
    probe = _PROBE.get()
    if (probe is None or probe.get("closed") or
            type(message_id) is not int or message_id <= 0):
        return
    if kind not in ("text", "photo", "document"):
        return
    # Stocks Buzz sends a preliminary "scanning" notice before doing any work.
    # Never accept that notice (or a progress update) as the finished result.
    if probe["action"] == "StocksBuzz" and kind == "text":
        if (not isinstance(message_text, str) or
                "STOCKS BUZZ — Breakout Radar" not in message_text or
                "Scanned" not in message_text or
                "confirmed breakouts" not in message_text):
            LOG.info("MSK_ACCEPTANCE_BUZZ_NONFINAL_RECEIPT message_id=%d", message_id)
            return
    probe["receipts"].append((kind, message_id))
    if (kind in ("photo", "document") and
            isinstance(file_id, str) and file_id.strip()):
        probe.setdefault("files", {})[message_id] = file_id
    LOG.info("MSK_ACCEPTANCE_RECEIPT action=%s kind=%s message_id=%d",
             probe["action"], kind, message_id)
    # Owner-only quality evidence: this is PUBLIC market data returned by
    # Telegram itself; never log arbitrary conversations or other buttons.
    if (probe["action"] == "Breadth" and kind == "text" and
            isinstance(message_text, str) and
            0 < len(message_text) <= 3500 and
            "MARKET PARTICIPATION" in message_text):
        LOG.info("MSK_ACCEPTANCE_BREADTH_RETURNED_TEXT message_id=%d text=%r",
                 message_id, message_text)
    # Public issuer research is logged only for the *verified owner* single
    # Large Cap acceptance probe, not for ordinary customer messages.
    if (probe["action"] == "LargeCapTop5" and kind == "text" and
            isinstance(message_text, str) and
            0 < len(message_text) <= 5000 and
            "LARGE CAP" in message_text and "Nifty 100" in message_text):
        LOG.info("MSK_ACCEPTANCE_LARGECAP_RETURNED_TEXT message_id=%d text=%r",
                 message_id, message_text)
    if (probe["action"] == "MidCapTop5" and kind == "text" and
            isinstance(message_text, str) and
            0 < len(message_text) <= 5000 and
            "MID CAP" in message_text and "Nifty Midcap 150" in message_text):
        LOG.info("MSK_ACCEPTANCE_MIDCAP_RETURNED_TEXT message_id=%d text=%r",
                 message_id, message_text)
    if (probe["action"] == "SmallCapTop5" and kind == "text" and
            isinstance(message_text, str) and
            0 < len(message_text) <= 5000 and
            "SMALL CAP" in message_text and "Nifty Smallcap 250" in message_text):
        LOG.info("MSK_ACCEPTANCE_SMALLCAP_RETURNED_TEXT message_id=%d text=%r",
                 message_id, message_text)


def _claim_once(run_id):
    import psycopg
    dsn = os.getenv("PORTFOLIO_DATABASE_URL", "")
    if not dsn or "sslmode=require" not in dsn:
        raise RuntimeError("Owner acceptance requires its own TLS database")
    with psycopg.connect(dsn, connect_timeout=12) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS msk_bot_acceptance_runs("
            "run_id TEXT PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public.msk_bot_acceptance_actions (
              run_id TEXT NOT NULL REFERENCES public.msk_bot_acceptance_runs(run_id),
              feature_name TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'UNVERIFIED'
                  CHECK (status IN ('UNVERIFIED','PASS','FAIL')),
              message_id BIGINT, file_id TEXT, data_timestamp TIMESTAMPTZ,
              provider TEXT, error TEXT,
              retry_count INTEGER NOT NULL DEFAULT 0
                  CHECK (retry_count BETWEEN 0 AND 1),
              validation_status TEXT NOT NULL DEFAULT 'unverified'
                  CHECK (validation_status IN ('unverified','passed','failed')),
              final_score NUMERIC(3,1),
              pdf_generated BOOLEAN NOT NULL DEFAULT FALSE,
              pdf_delivered BOOLEAN NOT NULL DEFAULT FALSE,
              checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (run_id,feature_name),
              CHECK (final_score IS NULL OR
                     (status='PASS' AND final_score BETWEEN 8.5 AND 10)),
              CHECK (status <> 'PASS' OR
                     (message_id > 0 AND data_timestamp IS NOT NULL AND
                      provider IS NOT NULL AND provider <> '' AND
                      validation_status = 'passed')),
              CHECK (pdf_delivered = FALSE OR
                     (pdf_generated AND file_id IS NOT NULL AND message_id > 0))
            )
        """)
        # Durable per-button claims preserve progress if Render restarts.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public.msk_bot_acceptance_steps (
                run_id TEXT NOT NULL REFERENCES public.msk_bot_acceptance_runs(run_id),
                feature_name TEXT NOT NULL,
                phase TEXT NOT NULL CHECK
                    (phase IN ('STARTED','DONE','FAILED','INTERRUPTED')),
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                error TEXT,
                PRIMARY KEY(run_id,feature_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public.msk_bot_acceptance_events (
                event_id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES public.msk_bot_acceptance_runs(run_id),
                feature_name TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT,
                message_id BIGINT,
                file_id TEXT,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        row = conn.execute(
            "INSERT INTO msk_bot_acceptance_runs(run_id) VALUES(%s) "
            "ON CONFLICT DO NOTHING RETURNING run_id", (run_id,)
        ).fetchone()
    # Preserve one-time claims used by the standalone PDF probe.
    # run_once resumes regardless of this return value; per-action claims
    # decide which Telegram sends are safe.
    return row is not None


def _claim_action(run_id, label):
    """Claim once before ANY Telegram send. Interrupted actions never resend.

    Telegram Bot API has no idempotency key. A process can die after Telegram
    accepted a message but before its receipt was saved. The only safe
    automatic choice is to mark that action INTERRUPTED, not send it again.
    Subsequent actions continue on startup.
    """
    import psycopg
    dsn = os.getenv("PORTFOLIO_DATABASE_URL", "")
    if not dsn or "sslmode=require" not in dsn:
        raise RuntimeError("Acceptance action claims require TLS PostgreSQL")
    with psycopg.connect(dsn, connect_timeout=12) as conn:
        created = conn.execute(
            """INSERT INTO public.msk_bot_acceptance_steps
               (run_id,feature_name,phase) VALUES(%s,%s,'STARTED')
               ON CONFLICT DO NOTHING RETURNING feature_name""",
            (run_id, label)
        ).fetchone()
        if created:
            # Old runs might have acceptance results, but no step journal.
            # Backfill them without sending any duplicate messages.
            prior = conn.execute(
                """SELECT status,message_id FROM
                   public.msk_bot_acceptance_actions
                   WHERE run_id=%s AND feature_name=%s""",
                (run_id, label)
            ).fetchone()
            if prior:
                conn.execute(
                    """UPDATE public.msk_bot_acceptance_steps
                       SET phase=%s,finished_at=NOW(),error='legacy_result_retained'
                       WHERE run_id=%s AND feature_name=%s""",
                    ("FAILED" if prior[0] == "FAIL" else "DONE", run_id, label)
                )
                conn.execute(
                    """INSERT INTO public.msk_bot_acceptance_events
                       (run_id,feature_name,event,detail,message_id)
                       VALUES (%s,%s,'SKIP_EXISTING_RESULT',
                               'Existing Neon result preserved; no resend',%s)""",
                    (run_id, label, prior[1])
                )
                LOG.info("MSK_ACCEPTANCE_SKIP_EXISTING action=%s", label)
                return False
            conn.execute(
                """INSERT INTO public.msk_bot_acceptance_events
                   (run_id,feature_name,event,detail)
                   VALUES (%s,%s,'STARTED','Durably claimed before Telegram send')""",
                (run_id, label)
            )
            return True

        existing = conn.execute(
            """SELECT phase FROM public.msk_bot_acceptance_steps
               WHERE run_id=%s AND feature_name=%s FOR UPDATE""",
            (run_id, label)
        ).fetchone()
        if existing and existing[0] == "STARTED":
            conn.execute(
                """UPDATE public.msk_bot_acceptance_steps
                   SET phase='INTERRUPTED',finished_at=NOW(),
                       error='Process restarted; Telegram delivery uncertain; no resend'
                   WHERE run_id=%s AND feature_name=%s AND phase='STARTED'""",
                (run_id, label)
            )
            conn.execute(
                """INSERT INTO public.msk_bot_acceptance_actions
                   (run_id,feature_name,status,validation_status,error)
                   VALUES(%s,%s,'FAIL','failed',
                   'Interrupted before result stored; delivery uncertain; no resend')
                   ON CONFLICT (run_id,feature_name) DO NOTHING""",
                (run_id, label)
            )
            conn.execute(
                """INSERT INTO public.msk_bot_acceptance_events
                   (run_id,feature_name,event,detail)
                   VALUES(%s,%s,'INTERRUPTED',
                          'Delivery uncertain: automatic resend prohibited')""",
                (run_id, label)
            )
            LOG.warning("MSK_ACCEPTANCE_INTERRUPTED action=%s no_resend=true", label)
        else:
            LOG.info("MSK_ACCEPTANCE_SKIP_COMPLETED action=%s phase=%s",
                     label, existing[0] if existing else "unknown")
        return False


def _finish_action(run_id, label, *, result, message_id=None, file_id=None,
                   error=None):
    """Append event and complete only our claimed action in one transaction."""
    import psycopg
    dsn = os.getenv("PORTFOLIO_DATABASE_URL", "")
    if not dsn or "sslmode=require" not in dsn:
        raise RuntimeError("Acceptance event commit requires TLS PostgreSQL")
    phase = "DONE" if result == "api_ack" else "FAILED"
    with psycopg.connect(dsn, connect_timeout=12) as conn:
        row = conn.execute(
            """UPDATE public.msk_bot_acceptance_steps
               SET phase=%s,finished_at=NOW(),error=%s
               WHERE run_id=%s AND feature_name=%s AND phase='STARTED'
               RETURNING feature_name""",
            (phase, str(error)[:160] if error else None, run_id, label)
        ).fetchone()
        if row is None:
            LOG.warning("MSK_ACCEPTANCE_LATE_OUTCOME action=%s ignored=true", label)
            return False
        conn.execute(
            """INSERT INTO public.msk_bot_acceptance_events
               (run_id,feature_name,event,detail,message_id,file_id)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (run_id,label,result,str(error)[:160] if error else None,
             message_id,file_id)
        )
        return True


def _save_action(run_id, label, *, status, message_id=None, file_id=None,
                 error=None, validation_status="unverified",
                 data_timestamp=None, provider=None, retry_count=0,
                 pdf_generated=False, pdf_delivered=False, final_score=None):
    """Persist real action evidence; failure to write never upgrades a rating.

    Do not treat a Telegram API ACK as proof of content, data freshness,
    database commit, or user receipt. Missing proof => unverified.
    """
    if status not in ("PASS", "FAIL", "UNVERIFIED"):
        raise ValueError("Invalid acceptance status")
    if validation_status not in ("passed", "failed", "unverified"):
        raise ValueError("Invalid validation status")
    if (type(retry_count) is not int or retry_count not in (0, 1)):
        raise ValueError("Invalid retry count")
    if type(message_id) is not int or message_id <= 0:
        message_id = None
    if not isinstance(file_id, str) or not file_id:
        file_id = None
    if status == "PASS" and not (
            message_id and provider and data_timestamp and
            validation_status == "passed" and
            (not pdf_generated or (pdf_delivered and file_id))):
        raise ValueError("Cannot certify feature without full live proof")
    if final_score is not None and (status != "PASS" or
                                    not 8.5 <= final_score <= 10):
        raise ValueError("Scores require passed evidence gate")
    dsn = os.getenv("PORTFOLIO_DATABASE_URL", "")
    if not dsn or "sslmode=require" not in dsn:
        raise RuntimeError("Owner acceptance evidence requires TLS PostgreSQL")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=12) as conn:
        conn.execute(
            """INSERT INTO public.msk_bot_acceptance_actions(
               run_id,feature_name,status,message_id,file_id,data_timestamp,
               provider,error,retry_count,validation_status,final_score,
               pdf_generated,pdf_delivered,checked_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
               ON CONFLICT(run_id,feature_name) DO UPDATE SET
               status=EXCLUDED.status,message_id=EXCLUDED.message_id,
               file_id=EXCLUDED.file_id,data_timestamp=EXCLUDED.data_timestamp,
               provider=EXCLUDED.provider,error=EXCLUDED.error,
               retry_count=EXCLUDED.retry_count,
               validation_status=EXCLUDED.validation_status,
               final_score=EXCLUDED.final_score,
               pdf_generated=EXCLUDED.pdf_generated,
               pdf_delivered=EXCLUDED.pdf_delivered,checked_at=NOW()""",
            (run_id, label, status, message_id, file_id, data_timestamp,
             provider, (str(error)[:160] if error else None), retry_count,
             validation_status, final_score, bool(pdf_generated),
             bool(pdf_delivered))
        )


def run_once(ctx):
    """Exercise all 12 production features and four inline paths as owner."""
    run_id = os.getenv("MSK_LIVE_ACCEPTANCE_RUN_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{6,100}", run_id):
        LOG.error("MSK_ACCEPTANCE_NOT_STARTED reason=invalid_run_id")
        return
    time.sleep(8)
    uid = ctx["usage_analytics"].get_owner_id()
    if type(uid) is not int or uid <= 0:
        LOG.error("MSK_ACCEPTANCE_NOT_STARTED reason=no_verified_private_owner")
        return
    try:
        _claim_once(run_id)
    except Exception as exc:
        LOG.error("MSK_ACCEPTANCE_NOT_STARTED reason=durable_claim_failed type=%s",
                  type(exc).__name__)
        return

    # Only one live process may walk a run at once. The session lock releases
    # automatically if Render kills the process. Step claims prevent resends
    # if Telegram accepted a message just before that crash.
    import psycopg
    dsn = os.getenv("PORTFOLIO_DATABASE_URL", "")
    lock_key = int(hashlib.sha256(run_id.encode("ascii")).hexdigest()[:15], 16)
    try:
        with psycopg.connect(dsn, connect_timeout=12) as guard:
            locked = guard.execute(
                "SELECT pg_try_advisory_lock(%s)", (lock_key,)
            ).fetchone()[0]
            if not locked:
                LOG.info("MSK_ACCEPTANCE_NOT_STARTED reason=another_runner_active")
                return
            _run_actions(ctx, uid, run_id)
    except Exception as exc:
        LOG.exception("MSK_ACCEPTANCE_RUN_FAILED run_id=%s type=%s",
                      run_id, type(exc).__name__)


def _owner_report_symbol():
    """Only vetted public NSE company symbols for owner-only Report tests."""
    requested = os.getenv("MSK_LIVE_ACCEPTANCE_REPORT_SYMBOL", "SBIN").strip().upper()
    if requested not in ("SBIN", "DLF"):
        raise ValueError("Owner PDF acceptance symbol must be SBIN or DLF")
    return requested


def _run_actions(ctx, uid, run_id):
    bot, send = ctx["bot"], ctx["safe_send"]
    LOG.info("MSK_ACCEPTANCE_RESUME run_id=%s actions=16 owner_verified=true", run_id)

    def emit(message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Feature returned empty result")
        active = _PROBE.get()
        if active is not None and message.lstrip().startswith(
                ("❌", "🛑", "⚠️", "⏳")):
            active["blocked"] = True
        return send(uid, message)

    def index(profile):
        response = ctx["request_index_top5"](profile, uid, send)
        if not isinstance(response, str) or not response.strip():
            raise ValueError("Prepared index report empty")
        if any(word in response.lower() for word in (
                "unavailable", "withheld", "not ready", "insufficient coverage")):
            LOG.warning("MSK_ACCEPTANCE_DATA_WITHHELD profile=%s", profile)
            active = _PROBE.get()
            if active is not None:
                active["blocked"] = True
        emit(response)

    def swing(mode):
        result = ctx["get_swing_trades"](mode=mode)
        if not isinstance(result, str) or not result.strip():
            raise ValueError("Swing result empty")
        for start in range(0, len(result), 3400):
            emit(result[start:start + 3400])

    class BotProxy:
        def __init__(self, original):
            self._original = original

        def __getattr__(self, name):
            return getattr(self._original, name)

        def send_photo(self, *args, **kwargs):
            sent = self._original.send_photo(*args, **kwargs)
            ctx["_record_telegram_api_ack"](uid, sent, "photo")
            return sent

        def send_message(self, *args, **kwargs):
            sent = self._original.send_message(*args, **kwargs)
            ctx["_record_telegram_api_ack"](uid, sent, "text")
            return sent

        def send_document(self, *args, **kwargs):
            sent = self._original.send_document(*args, **kwargs)
            ctx["_record_telegram_api_ack"](uid, sent, "document")
            return sent

    def auto_chart():
        ctx["get_chart_generator"]().send_to_telegram(BotProxy(bot), uid)

    def inline_chart():
        import os as _os
        generator = ctx["get_chart_generator"]()
        ok, details, path = generator.generate("SBIN.NS", "SBIN")
        if not ok or not path or not _os.path.isfile(path):
            raise RuntimeError("Inline stock chart generation failed")
        if _os.path.getsize(path) < 1024:
            raise ValueError("Inline chart is empty")
        with open(path, "rb") as stream:
            sent = bot.send_photo(uid, stream, caption="MSK live acceptance: SBIN chart")
        ctx["_record_telegram_api_ack"](uid, sent, "photo")

    def stock_search():
        ctx["send_symbol_menu"](uid, "SBIN")

    def portfolio():
        try:
            emit(ctx["build_portfolio_card"](uid))
        except RuntimeError:
            LOG.warning("MSK_ACCEPTANCE_DATA_WITHHELD profile=portfolio_storage")
            active = _PROBE.get()
            if active is not None:
                active["blocked"] = True
            emit("⚠️ Portfolio storage unavailable: existing positions require safe recovery.")

    actions = [
        ("Analysis", stock_search),
        ("Breadth", lambda: emit(ctx["build_breadth"]())),
        ("StocksBuzz", lambda: ctx["run_buzz"](uid)),
        ("LargeCapTop5", lambda: index("conservative")),
        ("MidCapTop5", lambda: index("moderate")),
        ("SmallCapTop5", lambda: index("aggressive")),
        ("SwingSafe", lambda: swing("conservative")),
        ("SwingAggressive", lambda: swing("aggressive")),
        ("Portfolio", portfolio),
        ("Chart", auto_chart),
        ("Report", lambda: ctx["run_report"](uid, _owner_report_symbol())),
        ("SectorReport", lambda: ctx["run_sector_report"](uid)),
        ("InlineTechnical", lambda: emit(ctx["build_technical_text"]("SBIN", "SBIN"))),
        ("InlineFundamental", lambda: emit(ctx["build_fundamental_text"]("SBIN", "SBIN"))),
        ("InlineChart", inline_chart),
        ("InlineResearchPDF", lambda: ctx["run_report"](uid, "SBIN")),
    ]
    completed = 0
    timeouts = {
        "Analysis": 90, "Breadth": 170, "StocksBuzz": 360,
        "LargeCapTop5": 90, "MidCapTop5": 90, "SmallCapTop5": 90,
        "SwingSafe": 180, "SwingAggressive": 180, "Portfolio": 45,
        "Chart": 210, "Report": 300, "SectorReport": 330,
        "InlineTechnical": 90, "InlineFundamental": 120,
        "InlineChart": 180, "InlineResearchPDF": 300,
    }
    required = {
        "Chart": "photo", "InlineChart": "photo",
        "Report": "document", "SectorReport": "document",
        "InlineResearchPDF": "document",
    }
    # Owner-only targeted retests use a *new run ID* but do not resend
    # unrelated buttons. No selection means the original full 16-action run.
    names = {name for name, _ in actions}
    only = os.getenv("MSK_LIVE_ACCEPTANCE_ONLY_FEATURES", "").strip()
    selected = {name.strip() for name in only.split(",") if name.strip()} if only else names
    if not selected or not selected.issubset(names):
        LOG.error("MSK_ACCEPTANCE_NOT_STARTED reason=invalid_feature_filter")
        return
    LOG.info("MSK_ACCEPTANCE_SELECTED run_id=%s count=%d features=%s",
             run_id, len(selected), ",".join(sorted(selected)))
    for label, func in actions:
        if label not in selected:
            continue
        try:
            if not _claim_action(run_id, label):
                continue
        except Exception as exc:
            # A missing Neon claim must NEVER fall back to sending Telegram.
            LOG.error("MSK_ACCEPTANCE_CLAIM_FAILED action=%s type=%s",
                      label,type(exc).__name__)
            return
        probe = {"action": label, "receipts": [], "files": {},
                 "blocked": False, "error": None, "closed": False}
        LOG.info("MSK_ACCEPTANCE_ACTION_START action=%s", label)

        def _execute(_probe=probe, _func=func, _label=label):
            token = _PROBE.set(_probe)
            try:
                _func()
            except Exception as exc:
                _probe["error"] = type(exc).__name__
                LOG.error("MSK_ACCEPTANCE_ACTION_ERROR action=%s type=%s",
                          _label, type(exc).__name__, exc_info=True)
            finally:
                _PROBE.reset(token)

        worker = threading.Thread(target=_execute,
                                  name=f"msk-test-{label}", daemon=True)
        worker.start()
        worker.join(timeouts[label])
        receipts = probe["receipts"]
        required_kind = required.get(label, "text")
        if worker.is_alive():
            result = "timeout"
        elif probe["error"]:
            result = "error"
        elif probe["blocked"]:
            result = "data_withheld"
        elif not any(kind == required_kind for kind, _ in receipts):
            result = "missing_" + required_kind + "_ack"
        else:
            result = "api_ack"
            completed += 1
        # Freeze the action before any timed-out worker can emit late evidence.
        probe["closed"] = True
        # Persist facts, not a numerical prediction. An API acknowledgment alone
        # lacks independent provider freshness/content checks, so it is never PASS.
        matches = [mid for kind, mid in receipts if kind == required_kind]
        message_id = matches[-1] if matches else None
        file_id = probe["files"].get(message_id)
        is_pdf = required_kind == "document"
        pdf_ack = bool(is_pdf and message_id and file_id)
        fail = result in ("timeout", "error", "data_withheld") or result.startswith("missing_")
        record_status = "FAIL" if fail else "UNVERIFIED"
        validation_status = "failed" if fail else "unverified"
        issue = (probe["error"] or ("timeout_after_%ss" % timeouts[label] if result == "timeout" else result)) if fail else "pending_independent_data_validation"
        try:
            _save_action(
                run_id, label, status=record_status, message_id=message_id,
                file_id=file_id, error=issue,
                validation_status=validation_status,
                pdf_generated=pdf_ack, pdf_delivered=pdf_ack
            )
            LOG.info("MSK_ACCEPTANCE_NEON_SAVED action=%s status=%s message_id=%s",
                     label, record_status, message_id or "NONE")
        except Exception as exc:
            LOG.error("MSK_ACCEPTANCE_NEON_FAILED action=%s type=%s",
                      label, type(exc).__name__)
            # Do not mark the claim DONE if its evidence is missing.
            return
        try:
            if not _finish_action(run_id, label, result=result,
                                  message_id=message_id, file_id=file_id,
                                  error=issue if fail else None):
                LOG.error("MSK_ACCEPTANCE_FINISH_REJECTED action=%s", label)
                return
        except Exception as exc:
            LOG.error("MSK_ACCEPTANCE_FINISH_FAILED action=%s type=%s",
                      label,type(exc).__name__)
            return
        LOG.info("MSK_ACCEPTANCE_ACTION_END action=%s result=%s receipts=%d required=%s",
                 label, result, len(receipts), required_kind)
    LOG.info("MSK_ACCEPTANCE_FINAL run_id=%s api_ack_actions=%d total=%d",
             run_id, completed, len(selected))
