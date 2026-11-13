"""Single-run, owner-only live integration acceptance for the deployed MSK Bot.

Disabled unless MSK_LIVE_ACCEPTANCE_RUN_ID is explicitly configured.
Never runs against customer chats or changes portfolio holdings.  The separate
MSK portfolio PostgreSQL database is used only for a one-time run claim.
"""
import contextvars
import logging
import os
import re
import time
import threading

LOG = logging.getLogger("msk_acceptance")
_PROBE = contextvars.ContextVar("msk_acceptance_probe", default=None)


def capture_telegram_ack(kind, message_id):
    """Called only for a genuine successful Telegram Bot API response."""
    probe = _PROBE.get()
    if probe is None or type(message_id) is not int or message_id <= 0:
        return
    probe["receipts"].append((kind, message_id))
    LOG.info("MSK_ACCEPTANCE_RECEIPT action=%s kind=%s message_id=%d",
             probe["action"], kind, message_id)


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
        row = conn.execute(
            "INSERT INTO msk_bot_acceptance_runs(run_id) VALUES(%s) "
            "ON CONFLICT DO NOTHING RETURNING run_id", (run_id,)
        ).fetchone()
    return row is not None


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
        if not _claim_once(run_id):
            LOG.info("MSK_ACCEPTANCE_NOT_STARTED reason=run_id_already_claimed")
            return
    except Exception as exc:
        LOG.error("MSK_ACCEPTANCE_NOT_STARTED reason=durable_claim_failed type=%s",
                  type(exc).__name__)
        return

    bot, send = ctx["bot"], ctx["safe_send"]
    LOG.info("MSK_ACCEPTANCE_BEGIN run_id=%s actions=16 owner_verified=true", run_id)

    def emit(message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Feature returned empty result")
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
        ("Report", lambda: ctx["run_report"](uid, "SBIN")),
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
    for label, func in actions:
        probe = {"action": label, "receipts": [], "blocked": False, "error": None}
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
        LOG.info("MSK_ACCEPTANCE_ACTION_END action=%s result=%s receipts=%d required=%s",
                 label, result, len(receipts), required_kind)
    LOG.info("MSK_ACCEPTANCE_FINAL run_id=%s api_ack_actions=%d total=16",
             run_id, completed)
