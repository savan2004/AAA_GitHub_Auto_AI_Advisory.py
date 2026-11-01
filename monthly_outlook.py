"""Read only, human-approved monthly market outlook editions.

One JSON file per YYYY-MM month. Never generate advice or prices on request.
"""
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ARCHIVE = Path(__file__).resolve().parent / "data" / "monthly_outlook"
MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
REQUIRED = ("month", "published_at", "as_of", "title", "summary", "risk_notice")
MAX_SUMMARY_LENGTH = 3000


def current_month():
    """Use the intended market timezone even when Render runs outside India."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m")


def load_outlook(month=None, root=None):
    """Return an explicitly approved edition or None; reject corrupt editions."""
    month = current_month() if month is None else month
    if not isinstance(month, str) or not MONTH_RE.fullmatch(month):
        raise ValueError("Outlook month must be YYYY-MM")
    path = (Path(root) if root is not None else ARCHIVE) / (month + ".json")
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Outlook edition must be a JSON object")
    if report.get("approved") is not True:
        return None
    for key in REQUIRED:
        if not isinstance(report.get(key), str) or not report[key].strip():
            raise ValueError("Approved outlook missing required field: " + key)
    if report["month"] != month:
        raise ValueError("Outlook edition month mismatch")
    date.fromisoformat(report["as_of"])
    date.fromisoformat(report["published_at"])
    if len(report["summary"]) > MAX_SUMMARY_LENGTH:
        raise ValueError("Outlook summary exceeds Telegram-safe size")
    if len(report["title"]) > 160 or len(report["risk_notice"]) > 450:
        raise ValueError("Outlook title or disclaimer is too long")
    return report


def format_outlook(report, month):
    """HTML-escape externally edited content; keep Telegram message bounded."""
    if report is None:
        return (f"📅 <b>Monthly Market Outlook — {html.escape(month)}</b>\n"
                "No reviewed edition has been published for this month.\n"
                "MSK Bot does not invent market forecasts or trade levels.")
    fields = [
        "📅 <b>" + html.escape(report["title"]) + "</b>",
        "Edition: " + html.escape(report["month"]),
        "Data as of: " + html.escape(report["as_of"]),
        "Published: " + html.escape(report["published_at"]),
        "",
        html.escape(report["summary"]),
        "",
        "⚠️ " + html.escape(report["risk_notice"]),
    ]
    output = "\n".join(fields)
    if len(output) > 4000:
        raise ValueError("Outlook is too large for Telegram")
    return output
