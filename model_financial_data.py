"""Evidence-first, explicit-period fundamentals for MSK's *model* screener only.

Fetches a limited number of public company pages from Screener.in. No private
API, no assertion that a fresh HTTP fetch is a fresh financial filing.
Absence/format drift is a hard rejection. The data source must permit such use;
operators should review its current terms before production rollout.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from urllib.parse import quote
import math
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MSKResearchAudit/1.0; public-financial-data-check)",
    "Accept": "text/html",
}
REQUIRED = ("pe", "roe_pct", "revenue_growth_pct", "debt_equity_ratio", "market_cap")


def _number(text):
    if text is None or isinstance(text, bool):
        return None
    match = re.fullmatch(r"\s*(?:₹\s*)?(-?\d[\d,]*(?:\.\d+)?)\s*(?:%|Cr\.?)?\s*", str(text))
    if not match:
        return None
    try:
        result = float(match.group(1).replace(",", ""))
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def _table(section):
    """Return [(periods, row map)] only when the table header and cells align."""
    table = section.select_one("table") if section else None
    if table is None:
        return [], {}
    header = table.select_one("thead tr")
    if header is None:
        return [], {}
    periods = [th.get_text(" ", strip=True) for th in header.find_all(["th", "td"])]
    if len(periods) < 3:
        return [], {}
    periods = periods[1:]
    rows = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) < len(periods) + 1:
            continue
        label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip(" +").lower()
        values = [_number(c.get_text(" ", strip=True)) for c in cells[1:len(periods)+1]]
        if label and len(values) == len(periods):
            rows[label] = values
    return periods, rows


def _fy_columns(periods, today):
    answer = []
    for index, label in enumerate(periods):
        parsed = None
        try:
            parsed = datetime.strptime(label, "%b %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if parsed.timestamp() <= today:
            answer.append((index, parsed))
    return answer


def _get_row(rows, names):
    for name in names:
        for key, vals in rows.items():
            if key == name:
                return vals
    return None


def parse_screener_model(html_text, symbol, fetched_at=None, sector=None):
    """Normalize annual YoY revenue and statement-date-matched debt/equity.

    All rupee values are absolute except underlying BS and sales figures,
    which Screener displays as rupees *crore*.
    """
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol):
        return None, "invalid ticker"
    if not isinstance(html_text, str) or len(html_text) > 2_000_000:
        return None, "invalid/oversized response"
    fetched_at = time.time() if fetched_at is None else float(fetched_at)
    if not math.isfinite(fetched_at):
        return None, "invalid fetch timestamp"
    soup = BeautifulSoup(html_text, "html.parser")
    # An actual matching NSE company page must be present, not a HTTP-200 bot
    # challenge, generic profile or redirects to a different company.
    text_head = soup.get_text(" ", strip=True)[:5000]
    if not re.search(r"\bNSE\s*:\s*" + re.escape(symbol) + r"(?![A-Z0-9&-])", text_head, re.I):
        return None, "NSE symbol not verified on provider page"
    ratios = {}
    for li in soup.select("#top-ratios li"):
        name = li.select_one(".name")
        if name is None:
            continue
        key = re.sub(r"\s+", " ", name.get_text(" ", strip=True)).strip().lower()
        rest = li.get_text(" ", strip=True)
        rest = rest[len(name.get_text(" ", strip=True)):].strip()
        val = _number(rest)
        if val is not None:
            ratios[key] = val
    market_cr = ratios.get("market cap")
    pe = ratios.get("stock p/e")
    roe = ratios.get("roe")
    if any(x is None for x in (market_cr, pe, roe)):
        return None, "missing market cap / P-E / ROE in company ratios"

    annual_periods, annual_rows = _table(soup.select_one("section#profit-loss"))
    sales = _get_row(annual_rows, ("sales", "revenue"))
    if sales is None:
        return None, "annual sales row unavailable"
    annuals = [(i, dt) for i, dt in _fy_columns(annual_periods, fetched_at) if dt.month == 3]
    if len(annuals) < 2:
        return None, "two annual fiscal periods required"
    (old_i, old_date), (new_i, new_date) = annuals[-2:]
    if new_date.year - old_date.year != 1 or (fetched_at - new_date.timestamp()) > 460 * 86400:
        return None, "annual financial statements stale or non-consecutive"
    if len(sales) <= new_i or len(sales) <= old_i:
        return None, "annual sales column misaligned"
    prev_sales, latest_sales = sales[old_i], sales[new_i]
    if prev_sales is None or latest_sales is None or prev_sales <= 0 or latest_sales < 0:
        return None, "annual revenue unavailable or invalid"
    growth = (latest_sales / prev_sales - 1) * 100

    balance_periods, balance_rows = _table(soup.select_one("section#balance-sheet"))
    borrowings = _get_row(balance_rows, ("borrowings",))
    equity = _get_row(balance_rows, ("equity capital",))
    reserves = _get_row(balance_rows, ("reserves",))
    dates = _fy_columns(balance_periods, fetched_at)
    # Prefer the latest balance-sheet column with all three line items.
    if not dates or any(v is None for v in (borrowings, equity, reserves)):
        return None, "balance sheet debt/equity unavailable"
    debt_value = None
    balance_asof = None
    for col, dated in reversed(dates):
        if max(col, 0) >= min(len(borrowings), len(equity), len(reserves)):
            continue
        debt, capital, reserve = borrowings[col], equity[col], reserves[col]
        if any(v is None for v in (debt, capital, reserve)):
            continue
        if debt < 0 or capital + reserve <= 0:
            continue
        debt_value = debt / (capital + reserve)
        balance_asof = dated
        break
    if debt_value is None or fetched_at - balance_asof.timestamp() > 460 * 86400:
        return None, "balance-sheet debt/equity is missing or stale"

    if not (0 < pe < 500 and market_cr > 0 and -100 <= growth <= 3000 and
            -200 <= roe <= 300 and 0 <= debt_value <= 100):
        return None, "out-of-range valuation / financial metrics"
    name = soup.select_one("h1")
    company_name = name.get_text(" ", strip=True)[:80] if name else symbol
    if not isinstance(sector, str) or not sector.strip():
        return None, "sector classification missing"
    return {
        "name": company_name,
        "sector": sector,
        "fetched_at": fetched_at,
        "pe": round(pe, 4),
        "roe_pct": round(roe, 4),
        "revenue_growth_pct": round(growth, 4),
        "debt_equity_ratio": round(debt_value, 6),
        "market_cap": round(market_cr * 1e7),
        "provider": "Screener.in public company page",
        "provider_url": f"https://www.screener.in/company/{quote(symbol)}/consolidated/",
        "fiscal_sales_period": new_date.strftime("%Y-%m"),
        "fiscal_sales_previous": old_date.strftime("%Y-%m"),
        "balance_sheet_period": balance_asof.strftime("%Y-%m"),
        "fetched_utc": datetime.fromtimestamp(fetched_at, timezone.utc).isoformat(),
        "coverage": "ROE provider headline; growth annual revenue YoY; D/E borrowings/(equity capital+reserves)",
    }, None


def _approved_snapshot(symbol, sector):
    """Read a separately authorized, curated/licensed, dated financial record.

    Refuse blank or self-contradictory provenance, unapproved imports and stale
    snapshots; fetching a local file does NOT reset the actual data timestamp.
    """
    path = os.getenv("MODEL_APPROVED_DATA_PATH", "").strip()
    if not path:
        return None, "approved financial snapshot path not configured"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, "approved snapshot unavailable/invalid JSON"
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None, "unsupported approved financial snapshot schema"
    if not isinstance(payload.get("approved_by"), str) or not payload["approved_by"].strip():
        return None, "financial snapshot lacks explicit operator approval"
    records = payload.get("records")
    record = records.get(symbol) if isinstance(records, dict) else None
    if not isinstance(record, dict) or record.get("symbol") != symbol:
        return None, "approved snapshot has no exact NSE symbol"
    if record.get("sector") != sector:
        return None, "approved sector classification mismatch"
    if not isinstance(record.get("provider"), str) or not record["provider"].strip():
        return None, "approved financial statement provider missing"
    source = record.get("provider_url")
    if not isinstance(source, str) or not re.fullmatch(r"https://[a-zA-Z0-9./_?&=%#-]{8,400}", source):
        return None, "approved financial source URL invalid"
    if not isinstance(record.get("coverage"), str) or not record["coverage"]:
        return None, "statement accounting basis not documented"
    for key in ("fiscal_sales_period", "fiscal_sales_previous", "balance_sheet_period"):
        try:
            datetime.strptime(record[key], "%Y-%m")
        except (ValueError, KeyError, TypeError):
            return None, f"financial statement date missing/invalid: {key}"
    try:
        newer = datetime.strptime(record["fiscal_sales_period"], "%Y-%m")
        older = datetime.strptime(record["fiscal_sales_previous"], "%Y-%m")
        balance = datetime.strptime(record["balance_sheet_period"], "%Y-%m")
    except (ValueError, TypeError, KeyError):
        return None, "invalid annual/balance statement dates"
    if newer.year - older.year != 1 or newer.month != older.month:
        return None, "sales comparison must use consecutive fiscal years"
    now = time.time()
    if newer.replace(tzinfo=timezone.utc).timestamp() > now or balance.replace(tzinfo=timezone.utc).timestamp() > now:
        return None, "financial statement periods are future-dated"
    if now - newer.replace(tzinfo=timezone.utc).timestamp() > 460 * 86400:
        return None, "approved financial statement outdated"
    try:
        fetched = datetime.fromisoformat(record["fetched_utc"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, "approved snapshot retrieval timestamp missing"
    if not 0 <= now - fetched <= 86400:
        return None, "approved data snapshot must be less than 24 hours old"
    values = {key: record.get(key) for key in REQUIRED}
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values.values()):
        return None, "approved financial values missing or nonfinite"
    if not (0 < values["pe"] < 500 and values["market_cap"] > 0 and
            -200 <= values["roe_pct"] <= 300 and -100 <= values["revenue_growth_pct"] <= 3000 and
            0 <= values["debt_equity_ratio"] <= 100):
        return None, "approved financial values out of range"
    return dict(record, fetched_at=fetched), None


def fetch_model_fundamentals(symbol, sector, timeout=9):
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol):
        return None, "invalid ticker"
    if os.getenv("MODEL_APPROVED_DATA_PATH"):
        return _approved_snapshot(symbol, sector)
    # Screener.in grants personal non-commercial viewing in its public terms.
    # Do NOT automatically scrape for a public/commercial Telegram service.
    # Operators must explicitly obtain the necessary data-use rights first.
    if os.getenv("MODEL_SCREENER_USE_AUTHORIZED", "").lower() != "true":
        return None, "licensed/approved financial source not configured; public Screener use disabled"
    url = f"https://www.screener.in/company/{quote(symbol)}/consolidated/"
    try:
        response = requests.get(url, headers=HEADERS, timeout=(4, timeout), allow_redirects=False)
    except requests.Timeout:
        return None, "Screener.in timeout"
    except requests.RequestException:
        return None, "Screener.in unavailable"
    if response.status_code != 200:
        return None, f"Screener.in HTTP {response.status_code}"
    if len(response.content) > 2_000_000:
        return None, "provider HTML too large"
    return parse_screener_model(response.text, symbol, fetched_at=time.time(), sector=sector)
