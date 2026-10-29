"""MSK Bot usage analytics. Isolated, opt-in PostgreSQL; never limits bot requests."""
import html
import hmac
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("msk_usage")
IST = ZoneInfo("Asia/Kolkata")
DB_ENV = "MSK_ANALYTICS_DATABASE_URL"
MAX_NAME = 80
MENU = {
    "📊 Breadth": "market_breadth",
    "🔥 Stocks Buzz": "stocks_buzz",
    "🏦 Conservative": "conservative",
    "⚖️ Moderate": "moderate",
    "🚀 Aggressive": "aggressive",
    "🎯 Swing (Safe)": "swing_safe",
    "🚀 Swing (Agr)": "swing_aggressive",
    "💼 Portfolio": "portfolio",
    "📈 Chart": "chart",
    "📄 Report": "research_pdf",
    "🏭 Sector Report": "sector_report",
    "🔍 Analysis": "stock_search",
    "📋 Status": "bot_status",
    "🔙 Menu": "menu",
}


def enabled():
    return bool(os.getenv(DB_ENV, "").strip())


def _connect():
    # Import lazily so missing analytics dependencies cannot stop Telegram startup.
    import psycopg
    url = os.environ[DB_ENV]
    return psycopg.connect(url, connect_timeout=4, sslmode="require",
                           options="-c statement_timeout=5000",
                           application_name="msk_bot_usage")


def _label(text, callback=False):
    text = text if isinstance(text, str) else ""
    if callback:
        action = text.split("|", 1)[0].lower()
        return {
            "tech": "technical", "fund": "fundamental",
            "chart": "chart", "pdf": "research_pdf",
            "menu": "stock_search",
        }.get(action, "other_button")
    if text in MENU:
        return MENU[text]
    if text.startswith("/"):
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        return {
            "/start": "start", "/help": "help", "/report": "research_pdf",
            "/chart": "chart", "/portfolio": "portfolio",
            "/buy": "portfolio_buy", "/sell": "portfolio_sell",
            "/buzz": "stocks_buzz", "/sectors": "sector_report",
            "/sector": "sector_report", "/status": "bot_status",
            "/ownerstats": "owner_dashboard", "/ownerusers": "owner_dashboard",
            "/ownerclaim": "owner_setup", "/myid": "owner_setup",
        }.get(command, "other_command")
    return "stock_search"


def extract_event(payload):
    """Keep only identity + feature category; NEVER retain messages/stock queries."""
    if not isinstance(payload, dict):
        return None
    update_id = payload.get("update_id")
    if type(update_id) is not int or update_id < 0:
        return None
    cb = payload.get("callback_query")
    callback = isinstance(cb, dict)
    message = (cb.get("message") if callback else payload.get("message"))
    user = (cb.get("from") if callback else
            message.get("from") if isinstance(message, dict) else None)
    if not isinstance(user, dict) or not isinstance(message, dict):
        return None
    user_id = user.get("id")
    if type(user_id) is not int or user_id <= 0 or user.get("is_bot"):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_type = chat.get("type")
    if chat_type not in ("private", "group", "supergroup"):
        return None
    text = cb.get("data") if callback else message.get("text")
    if not isinstance(text, str) or not text:
        return None
    if not callback and chat_type != "private":
        # Do not count unrelated group chatter or automatic channel forwards.
        if message.get("is_automatic_forward") or not (
            text.startswith("/") or re.match(r"^msk\b", text, re.I)
        ):
            return None
    username = user.get("username") or ""
    if not isinstance(username, str):
        username = ""
    username = username[:MAX_NAME] if re.fullmatch(r"[A-Za-z0-9_]{1,80}", username) else ""
    first = user.get("first_name") if isinstance(user.get("first_name"), str) else ""
    last = user.get("last_name") if isinstance(user.get("last_name"), str) else ""
    name = " ".join((first, last)).strip()[:MAX_NAME]
    return {
        "update_id": update_id, "user_id": user_id, "username": username,
        "display_name": name, "feature": _label(text, callback),
        "chat_type": chat_type,
    }


def record_event(payload):
    """At-most-once counting per Telegram update; fail-open on database outage."""
    event = extract_event(payload)
    if not enabled() or event is None:
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO msk_bot_usage.users
                    (user_id, username, display_name, last_seen, chat_type)
                    VALUES (%s, %s, %s, NOW(), %s)
                    ON CONFLICT (user_id) DO UPDATE
                      SET username=EXCLUDED.username, display_name=EXCLUDED.display_name,
                          chat_type=EXCLUDED.chat_type
                """, (event["user_id"], event["username"],
                      event["display_name"], event["chat_type"]))
                cur.execute("""
                    INSERT INTO msk_bot_usage.events (update_id,user_id,feature,chat_type)
                    VALUES (%s,%s,%s,%s) ON CONFLICT (update_id) DO NOTHING
                    RETURNING update_id
                """, (event["update_id"], event["user_id"],
                      event["feature"], event["chat_type"]))
                if cur.fetchone() is None:
                    return False
                cur.execute("""
                    UPDATE msk_bot_usage.users SET request_count=request_count+1,
                        last_seen=NOW() WHERE user_id=%s
                """, (event["user_id"],))
        return True
    except Exception as exc:
        LOG.warning("Analytics event not stored (%s); bot continues", type(exc).__name__)
        return False


def claim_owner(user_id, code):
    """One-time private-chat owner enrollment; no public registration."""
    key = os.getenv("MSK_OWNER_SETUP_KEY", "")
    if not enabled() or len(key) < 24 or not isinstance(code, str):
        return False
    if not hmac.compare_digest(key, code):
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO msk_bot_usage.owner (slot,telegram_id)
                    VALUES (TRUE,%s) ON CONFLICT (slot) DO NOTHING
                """, (user_id,))
                cur.execute("SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE")
                row = cur.fetchone()
                return bool(row and row[0] == user_id)
    except Exception as exc:
        LOG.warning("Owner enrollment unavailable (%s)", type(exc).__name__)
        return False


def get_owner_id():
    if not enabled():
        return None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE")
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        LOG.warning("Owner lookup unavailable (%s)", type(exc).__name__)
        return None


def is_owner(user_id):
    return type(user_id) is int and get_owner_id() == user_id


def get_summary():
    """Only durable data; no guessed historical users or successful responses."""
    if not enabled():
        return None
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(*) FILTER
                  (WHERE (last_seen AT TIME ZONE 'Asia/Kolkata')::date =
                         (NOW() AT TIME ZONE 'Asia/Kolkata')::date)
                FROM msk_bot_usage.users
                WHERE user_id <> COALESCE(
                  (SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE), -1)
            """)
            users, active = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) FILTER
                  (WHERE (e.created_at AT TIME ZONE 'Asia/Kolkata')::date =
                         (NOW() AT TIME ZONE 'Asia/Kolkata')::date),
                       COUNT(*) FILTER
                  (WHERE e.created_at >= NOW() - INTERVAL '7 days')
                FROM msk_bot_usage.events e
                WHERE e.user_id <> COALESCE(
                  (SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE), -1)
            """)
            today, seven_days = cur.fetchone()
            cur.execute("""
                SELECT feature, COUNT(*) AS n FROM msk_bot_usage.events e
                WHERE e.created_at >= NOW() - INTERVAL '7 days'
                AND e.user_id <> COALESCE(
                  (SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE), -1)
                GROUP BY feature ORDER BY n DESC, feature LIMIT 12
            """)
            features = cur.fetchall()
            cur.execute("""
                SELECT user_id,username,display_name,request_count,
                       last_seen AT TIME ZONE 'Asia/Kolkata'
                FROM msk_bot_usage.users
                WHERE user_id <> COALESCE(
                  (SELECT telegram_id FROM msk_bot_usage.owner WHERE slot=TRUE), -1)
                ORDER BY request_count DESC, last_seen DESC LIMIT 20
            """)
            users_table = cur.fetchall()
    return {
        "users": users, "active_today": active, "requests_today": today or 0,
        "requests_7d": seven_days or 0, "features": features,
        "top_users": users_table,
        "as_of": datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST"),
    }


def summary_text(summary):
    if summary is None:
        return "Usage analytics is not configured."
    lines = [
        "📊 <b>MSK Bot — Owner Analytics</b>",
        "As of: " + html.escape(summary["as_of"]),
        f"👥 Users recorded: <b>{summary['users']}</b>",
        f"🟢 Active today: <b>{summary['active_today']}</b>",
        f"🔢 Requests today: <b>{summary['requests_today']}</b>",
        f"📅 Requests last 7 days: <b>{summary['requests_7d']}</b>",
        "",
        "🔥 <b>Most used (7 days)</b>",
    ]
    lines.extend(f"• {html.escape(str(k))}: {v}" for k, v in summary["features"][:7])
    if not summary["features"]:
        lines.append("No activity recorded yet.")
    lines.append("ℹ️ Requests received; successful delivery is not measured.")
    lines.append("No daily usage quota is applied by this feature.")
    return "\n".join(lines)


def users_text(summary):
    if summary is None:
        return "Analytics unavailable."
    lines = ["👥 <b>Top MSK users (recorded)</b>"]
    for uid, username, name, count, last in summary["top_users"][:15]:
        who = ("@" + username) if username else name or ("ID " + str(uid))
        when = last.strftime("%d %b %H:%M IST") if last else "—"
        lines.append(f"• {html.escape(who)}: {count} requests — {when}")
    if not summary["top_users"]:
        lines.append("No tracked users yet.")
    return "\n".join(lines)


def dashboard_html(summary):
    """HTML only after caller authenticates. All user-controlled fields escaped."""
    if summary is None:
        return "<!doctype html><title>MSK Analytics</title><h1>Analytics unavailable</h1>"
    def esc(x):
        return html.escape(str(x), quote=True)
    cards = [
        ("Recorded users", summary["users"]), ("Active today", summary["active_today"]),
        ("Requests today", summary["requests_today"]),
        ("Requests in 7 days", summary["requests_7d"]),
    ]
    tiles = "".join(f'<section class="tile"><small>{esc(k)}</small><strong>{v}</strong></section>'
                    for k, v in cards)
    features = "".join(f"<tr><td>{esc(n)}</td><td>{count}</td></tr>"
                       for n, count in summary["features"])
    rows = "".join(
        f"<tr><td>{esc('@' + username if username else name or 'Unknown')}</td>"
        f"<td>{uid}</td><td>{count}</td><td>"
        f"{esc(when.strftime('%d %b %Y %H:%M IST') if when else '—')}</td></tr>"
        for uid, username, name, count, when in summary["top_users"]
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120"><title>MSK Bot | Owner Analytics</title>
<style>body{{font:15px system-ui,sans-serif;background:#f5f7fa;color:#14223a;margin:0}}
main{{max-width:1080px;margin:auto;padding:25px}}header{{display:flex;justify-content:space-between;
align-items:center;flex-wrap:wrap}}h1{{font-size:27px}}small,footer{{color:#627189}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px}}
.tile,.panel{{background:#fff;border:1px solid #e1e6ef;border-radius:14px;padding:18px}}
.tile small{{display:block}}strong{{font-size:32px;display:block;margin-top:9px}}
.panel{{margin-top:16px;overflow:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:11px;border-bottom:1px solid #e5eaf2}}
th{{font-size:13px;color:#627189}}a{{color:#2450a4}}footer{{margin:20px 0}}
</style></head><body><main>
<header><div><h1>MSK Bot — Owner Analytics</h1><small>Updated {esc(summary['as_of'])}</small></div>
<small>Private dashboard · automatic refresh 2 min</small></header>
<div class="cards">{tiles}</div>
<div class="panel"><h2>Popular features · 7 days</h2><table><tr><th>Feature</th><th>Requests</th>
</tr>{features or '<tr><td colspan="2">No records yet</td></tr>'}</table></div>
<div class="panel"><h2>Top users · all time</h2><table><tr>
<th>User</th><th>Telegram ID</th><th>Requests</th><th>Last active</th></tr>
{rows or '<tr><td colspan="4">No records yet</td></tr>'}</table></div>
<footer>Tracking starts after deployment. Counts are inbound interactions, not confirmed
successful analysis or delivery. No usage quota imposed by analytics.</footer>
</main></body></html>"""


def claim_daily_digest(day):
    """Claim at most one notice per IST date; no resend after ambiguous transport."""
    if not enabled():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO msk_bot_usage.daily_notices (report_day,status)
                    VALUES (%s,'CLAIMED') ON CONFLICT (report_day) DO NOTHING
                    RETURNING report_day
                """, (day,))
                return cur.fetchone() is not None
    except Exception as exc:
        LOG.warning("Daily digest claim unavailable (%s)", type(exc).__name__)
        return False


def finish_digest(day, status):
    if status not in ("SENT", "UNKNOWN"):
        return
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE msk_bot_usage.daily_notices SET status=%s,
                     finalized_at=NOW() WHERE report_day=%s AND status='CLAIMED'
                """, (status, day))
    except Exception as exc:
        LOG.warning("Daily digest reconciliation unavailable (%s)", type(exc).__name__)


def daily_digest_loop(send_message, stop_event):
    """Best-effort 8pm IST notice, while Render service is awake.
    Callers must not describe it as guaranteed on a sleeping free instance.
    """
    while not stop_event.wait(60):
        now = datetime.now(IST)
        if now.hour < 20:
            continue
        owner_id = get_owner_id()
        if not owner_id or not claim_daily_digest(now.date()):
            continue
        status = "UNKNOWN"
        try:
            stats = get_summary()
            if stats is not None:
                send_message(owner_id, summary_text(stats), parse_mode="HTML")
                status = "SENT"
        except Exception as exc:
            LOG.warning("Daily owner summary delivery unverified (%s)", type(exc).__name__)
        finally:
            finish_digest(now.date(), status)
