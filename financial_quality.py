"""Conservative, no-dependency financial statement period controls.

A Screener P&L table may end with a TTM value whose end date is not supplied.
Never call that final cell 'latest FY' or use it as a dated annual revenue.
These helpers only parse secondary-provider metadata; they do NOT assert that
numbers were independently verified against an issuer's NSE XBRL filing.
"""
import calendar
import html as html_lib
import math
import re
from datetime import date, datetime

TAG = re.compile(r"<[^>]+>")
PERIOD = re.compile(r"^(Mar)\s+(20\d{2})$", re.I)


def annual_sales_from_table(profit_loss_html, *, today=None):
    """Return dated annual sales in rupees only when header/value align.

    Screener's P&L table reports values in ₹ crore. The final TTM column,
    if present, is deliberately ignored because it lacks a period-end date.
    We only accept explicit March fiscal-year columns (FY ending 31 March).
    """
    if not isinstance(profit_loss_html, str):
        return None
    today = today or date.today()
    if not isinstance(today, date):
        raise ValueError("today must be a date")
    h = re.search(r"<thead\b[^>]*>(.*?)</thead>", profit_loss_html,
                  re.I | re.S)
    row = re.search(r"<tr\b[^>]*>\s*<td\b[^>]*>\s*(?:Sales|Revenue)\b.*?</tr>",
                    profit_loss_html, re.I | re.S)
    if not h or not row:
        return None
    headers = [
        html_lib.unescape(TAG.sub("", val)).strip()
        for val in re.findall(r"<th\b[^>]*>(.*?)</th>", h.group(1), re.I | re.S)
    ]
    vals = [
        html_lib.unescape(TAG.sub("", val)).strip()
        for val in re.findall(r"<td\b[^>]*>(.*?)</td>", row.group(0), re.I | re.S)
    ]
    if not headers or len(vals) < 2 or len(headers) != len(vals):
        # Header and value columns must line up exactly. Do not guess offsets.
        return None
    if not re.match(r"^(Sales|Revenue)\b", vals[0], re.I):
        return None
    for head, number in reversed(list(zip(headers[1:], vals[1:]))):
        m = PERIOD.fullmatch(head)
        if not m:
            continue
        asof = date(int(m.group(2)), 3, calendar.monthrange(int(m.group(2)), 3)[1])
        days_old = (today - asof).days
        if days_old < 0 or days_old > 540:
            return None
        cleaned = number.replace(",", "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
            return None
        crore = float(cleaned)
        if not math.isfinite(crore) or crore <= 0:
            return None
        rupees = crore * 10_000_000
        if not math.isfinite(rupees):
            return None
        return {"rev": rupees, "period_end": asof.isoformat(),
                "period_basis": "FY", "units": "INR"}
    return None


def financial_source_summary(fund):
    """Short Telegram-safe source/period warning, never 'filing verified'."""
    if not isinstance(fund, dict):
        fund = {}
    provenances = fund.get("_field_provenance") or {}
    keys = ("rev", "roe", "eps", "de")
    labels = {"rev": "Revenue", "roe": "ROE", "eps": "EPS", "de": "D/E"}
    details = []
    for key in keys:
        if fund.get(key) is None:
            continue
        source = provenances.get(key) if isinstance(provenances, dict) else None
        source = source if isinstance(source, dict) else {}
        provider = str(source.get("source") or "source unknown")[:30]
        period = str(source.get("period_end") or "period unknown")[:16]
        raw_scope = source.get("scope")
        scope = str(raw_scope if raw_scope in ("standalone", "consolidated")
                    else "scope unknown")[:15]
        details.append(f"{labels[key]}: {provider} ({period}, {scope})")
    if not details:
        return "⚠️ Fundamental periods/scope not verified against issuer filings."
    return ("📚 <b>Source / period</b> (secondary data, NOT NSE filing-audited)\n"
            + "\n".join("• " + line for line in details)
            + "\n⚠️ Cross-period or consolidated/standalone values must not be "
              "treated as directly comparable until issuer filings are checked.")
