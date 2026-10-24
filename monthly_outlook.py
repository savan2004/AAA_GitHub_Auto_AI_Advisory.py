"""Read-only, versioned monthly market outlook archive.

An approved report is committed as data/monthly_outlook/YYYY-MM.json.
Never silently generates or updates research on subscriber requests.
"""
from datetime import datetime
from pathlib import Path
import json
import re

ARCHIVE = Path(__file__).resolve().parent / "data" / "monthly_outlook"
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def load_outlook(month=None, root=None):
    if month is None:
        month = datetime.now().strftime("%Y-%m")
    if not isinstance(month, str) or not MONTH_PATTERN.fullmatch(month):
        raise ValueError("Month must be YYYY-MM")
    root = Path(root) if root is not None else ARCHIVE
    path = root / (month + ".json")
    if not path.is_file():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    required = ("month", "published_at", "as_of", "title", "summary", "risk_notice")
    if not isinstance(obj, dict) or any(not isinstance(obj.get(k), str) or not obj[k].strip() for k in required):
        raise ValueError("Monthly outlook document is incomplete")
    if obj["month"] != month:
        raise ValueError("Monthly outlook document month mismatch")
    if len(obj["summary"]) > 3500:
        raise ValueError("Monthly outlook summary too long")
    return obj


def format_outlook(report, month):
    if not report:
        return (f"📅 Monthly Market Outlook ({month})\\n"
                "No reviewed report has been published for this month yet. "
                "MSK Bot will not invent a market outlook.")
    import html
    return ("📅 <b>" + html.escape(report["title"]) + "</b>\\n"
            "Edition: " + html.escape(report["month"]) + "\\n"
            "Data as of: " + html.escape(report["as_of"]) + "\\n"
            "Published: " + html.escape(report["published_at"]) + "\\n\\n"
            + html.escape(report["summary"]) + "\\n\\n"
            "⚠️ " + html.escape(report["risk_notice"]))
