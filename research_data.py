"""
research_data.py — Deep fundamental data for the institutional research report
────────────────────────────────────────────────────────────────────────────────
Everything here is BEST-EFFORT and returns None / {} on failure. The report
omits any section whose data is missing, instead of printing pages of "N/A".

Sources
  Screener.in (HTML, no key)  → 10-yr P&L / balance sheet / cash flow, last 8
                                quarters, compounded growth rates, shareholding
                                pattern (promoter / FII / DII / public).
  Yahoo Finance (yfinance)    → annual + quarterly statements as a fallback,
                                and analyst consensus price targets.

Normalised output of get_statements(sym):
  {
    "source": "Screener.in" | "Yahoo Finance",
    "unit":   "₹ Cr",
    "years":    [ {label, revenue, op_profit, opm, net_profit, eps,
                   ocf, borrowings, networth, roce}, ... ]   oldest → newest
    "quarters": [ {label, revenue, op_profit, opm, net_profit, eps}, ... ]
  }
Any field can be None.
"""

import re
import time
import calendar
import logging
import threading
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_TTL = 6 * 3600
_cache: Dict[str, dict] = {}
_lock = threading.Lock()

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/",
}


def _cget(key):
    with _lock:
        e = _cache.get(key)
    if e and time.time() - e["ts"] < _TTL:
        return e["val"]
    return None


def _cset(key, val):
    with _lock:
        _cache[key] = {"val": val, "ts": time.time()}


# ══════════════════════════════════════════════════════════════════════════
# Tiny HTML table parser (stdlib only — no bs4/lxml dependency)
# ══════════════════════════════════════════════════════════════════════════
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._table = None
        self._row = None
        self._cell = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            txt = re.sub(r"\s+", " ", "".join(self._cell).replace("\xa0", " ")).strip()
            self._row.append(txt)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._depth = max(0, self._depth - 1)


def _tables_in(html_chunk: str) -> List[List[List[str]]]:
    p = _TableParser()
    try:
        p.feed(html_chunk)
    except Exception as e:
        logger.debug(f"[research_data] table parse error: {e}")
    return p.tables


def _section(html: str, sec_id: str) -> str:
    m = re.search(r'<section[^>]*\bid="%s"[^>]*>(.*?)</section>' % re.escape(sec_id),
                  html, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""


def _num(txt) -> Optional[float]:
    if txt is None:
        return None
    t = str(txt).replace(",", "").replace("%", "").replace("₹", "").strip()
    if t in ("", "-", "--", "—", "NA", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _clean_label(txt: str) -> str:
    return re.sub(r"\s*\+\s*$", "", txt or "").strip()


def _table_to_rows(table: List[List[str]]) -> tuple:
    """(period_labels, {row_label: [floats]}) from a Screener data-table."""
    if not table or len(table) < 2:
        return [], {}
    header = table[0][1:]
    rows = {}
    for r in table[1:]:
        if len(r) < 2:
            continue
        rows[_clean_label(r[0])] = [_num(x) for x in r[1:]]
    return header, rows


def _pick(rows: dict, *names) -> Optional[List[Optional[float]]]:
    low = {k.lower(): v for k, v in rows.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    for n in names:                                   # prefix match ("Net Profit" → "Net Profit +")
        for k, v in low.items():
            if k.startswith(n.lower()):
                return v
    return None


# ══════════════════════════════════════════════════════════════════════════
# Screener.in
# ══════════════════════════════════════════════════════════════════════════
def _screener_html(sym: str) -> Optional[str]:
    cached = _cget(f"scr_html_{sym}")
    if cached:
        return cached
    try:
        from config import TIMEOUT_SCREENER as TO
    except Exception:
        TO = 8
    for url in (f"https://www.screener.in/company/{sym}/consolidated/",
                f"https://www.screener.in/company/{sym}/"):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=max(TO, 10))
            if r.ok and "data-table" in r.text:
                _cset(f"scr_html_{sym}", r.text)
                return r.text
        except Exception as e:
            logger.info(f"[research_data] screener {sym} failed: {str(e)[:100]}")
    return None


def parse_growth(html: str) -> dict:
    """Compounded growth tables → {"Compounded Sales Growth": {"5 Years": 14.0, ...}, ...}"""
    out = {}
    for t in _tables_in(_section(html, "profit-loss") or html):
        if not t or len(t[0]) != 1:
            continue
        title = t[0][0]
        if not re.search(r"compounded|stock price cagr|return on equity", title, re.I):
            continue
        d = {}
        for r in t[1:]:
            if len(r) == 2:
                d[r[0].rstrip(":").strip()] = _num(r[1])
        if d:
            out[title.strip()] = d
    return out


def parse_quarters(html: str) -> List[dict]:
    tabs = _tables_in(_section(html, "quarters"))
    if not tabs:
        return []
    hdr, rows = _table_to_rows(tabs[0])
    sales = _pick(rows, "Sales", "Revenue")
    if not hdr or sales is None:
        return []
    op = _pick(rows, "Operating Profit", "Financing Profit")
    opm = _pick(rows, "OPM %", "Financing Margin %")
    npf = _pick(rows, "Net Profit")
    eps = _pick(rows, "EPS in Rs", "EPS")
    out = []
    for i, lab in enumerate(hdr):
        def g(arr):
            return arr[i] if arr is not None and i < len(arr) else None
        out.append({"label": lab, "revenue": g(sales), "op_profit": g(op), "opm": g(opm),
                    "net_profit": g(npf), "eps": g(eps)})
    return out[-8:]


def parse_years(html: str) -> List[dict]:
    pl = _tables_in(_section(html, "profit-loss"))
    pl = [t for t in pl if t and len(t[0]) > 2]            # drop ranges tables
    if not pl:
        return []
    hdr, rows = _table_to_rows(pl[0])
    sales = _pick(rows, "Sales", "Revenue")
    if not hdr or sales is None:
        return []
    op = _pick(rows, "Operating Profit", "Financing Profit")
    opm = _pick(rows, "OPM %", "Financing Margin %")
    npf = _pick(rows, "Net Profit")
    eps = _pick(rows, "EPS in Rs", "EPS")

    bs_tabs = _tables_in(_section(html, "balance-sheet"))
    bs_hdr, bs = _table_to_rows(bs_tabs[0]) if bs_tabs else ([], {})
    cf_tabs = _tables_in(_section(html, "cash-flow"))
    cf_hdr, cf = _table_to_rows(cf_tabs[0]) if cf_tabs else ([], {})
    ra_tabs = _tables_in(_section(html, "ratios"))
    ra_hdr, ra = _table_to_rows(ra_tabs[0]) if ra_tabs else ([], {})

    borrow = _pick(bs, "Borrowings")
    t_assets = _pick(bs, "Total Assets")
    deposits = _pick(bs, "Deposits")
    eq_cap = _pick(bs, "Equity Capital")
    reserves = _pick(bs, "Reserves")
    ocf = _pick(cf, "Cash from Operating Activity")
    roce = _pick(ra, "ROCE %")

    def at(arr, hdr_list, label):
        if arr is None or label not in hdr_list:
            return None
        j = hdr_list.index(label)
        return arr[j] if j < len(arr) else None

    out = []
    for i, lab in enumerate(hdr):
        if lab.strip().upper() == "TTM":
            continue
        def g(arr):
            return arr[i] if arr is not None and i < len(arr) else None
        e, r = at(eq_cap, bs_hdr, lab), at(reserves, bs_hdr, lab)
        nw = (e or 0) + r if r is not None else None
        out.append({
            "label": lab, "revenue": g(sales), "op_profit": g(op), "opm": g(opm),
            "net_profit": g(npf), "eps": g(eps),
            "ocf": at(ocf, cf_hdr, lab), "borrowings": at(borrow, bs_hdr, lab),
            "networth": nw, "roce": at(roce, ra_hdr, lab),
            "total_assets": at(t_assets, bs_hdr, lab), "deposits": at(deposits, bs_hdr, lab),
        })
    return out[-10:]


# ── Shareholder label sanitiser ──────────────────────────────────────────
# In sans-serif fonts a capital I and a lowercase l look identical, so scraped or OCR'd
# text often turns "FIIs"/"DIIs" into "Fils"/"Dlls" (or "Fll"/"D11"). Everything that
# touches a holder label goes through here, and only canonical names are ever displayed.
_FII_RE = re.compile(r"^f[il1|!]{2}s?$")
_DII_RE = re.compile(r"^d[il1|!]{2}s?$")
HOLDER_DISPLAY = {"promoters": "Promoters", "fii": "FIIs", "dii": "DIIs", "public": "Public", "gov": "Government"}


def normalize_holder_label(raw: str) -> Optional[str]:
    """Raw row label → canonical key ('promoters'|'fii'|'dii'|'public'|'gov') or None."""
    t = re.sub(r"[^a-z0-9|!]", "", (raw or "").lower().replace("\xa0", " "))
    if not t:
        return None
    if t.startswith("promoter"):
        return "promoters"
    if _FII_RE.match(t) or t.startswith(("foreign", "fpi")):
        return "fii"
    if _DII_RE.match(t) or t.startswith("domestic"):
        return "dii"
    if t.startswith("public"):
        return "public"
    if t.startswith("government") or t == "govt":
        return "gov"
    return None


def holder_display(key: str) -> str:
    """Canonical display text — always 'FIIs' / 'DIIs' with capital I's."""
    return HOLDER_DISPLAY.get(key, key.title())


def sanitize_holder_text(text: str) -> str:
    """
    Repair corrupted holder names inside free text. Only the exact 4-letter plural forms are
    touched (F/D + two look-alikes of I/l + 's'), so real words — Fill, Dill, Filing, Dills —
    are left alone.
    """
    text = re.sub(r"\bF[IiLl1|!]{2}s\b", "FIIs", text)
    return re.sub(r"\bD[IiLl1|!]{2}s\b", "DIIs", text)


def parse_shareholding(html: str) -> List[dict]:
    """Quarterly shareholding pattern → [{label, promoters, fii, dii, public}]"""
    tabs = _tables_in(_section(html, "shareholding"))
    if not tabs:
        return []
    hdr, rows = _table_to_rows(tabs[0])
    by_key: Dict[str, list] = {}
    for lab, vals in rows.items():
        k = normalize_holder_label(lab)
        if k and k not in by_key:
            by_key[k] = vals
    pro = by_key.get("promoters")
    if not hdr or pro is None:
        return []
    fii, dii, pub = by_key.get("fii"), by_key.get("dii"), by_key.get("public")
    out = []
    for i, lab in enumerate(hdr):
        def g(arr):
            return arr[i] if arr is not None and i < len(arr) else None
        out.append({"label": lab, "promoters": g(pro), "fii": g(fii), "dii": g(dii), "public": g(pub)})
    return out[-8:]


def detect_layout(html: str) -> str:
    """'lender' if Screener shows Financing Profit/Margin rows (NBFCs, banks), else 'corporate'."""
    for sec in ("quarters", "profit-loss"):
        for t in _tables_in(_section(html, sec)):
            for r in t:
                if r and re.match(r"^\s*(financing profit|financing margin)", r[0].replace("\xa0", " "), re.I):
                    return "lender"
    return "corporate"


def get_screener_extras(sym: str) -> dict:
    sym = sym.upper().replace(".NS", "")
    cached = _cget(f"scr_{sym}")
    if cached is not None:
        return cached
    html = _screener_html(sym)
    out: dict = {}
    if html:
        out["layout"] = detect_layout(html)
        for key, fn in (("growth", parse_growth), ("quarters", parse_quarters),
                        ("years", parse_years), ("shareholding", parse_shareholding)):
            try:
                out[key] = fn(html)
            except Exception as e:
                logger.warning(f"[research_data] parse {key} {sym}: {e}")
                out[key] = {} if key == "growth" else []
    _cset(f"scr_{sym}", out)
    return out


# ══════════════════════════════════════════════════════════════════════════
# Yahoo Finance fallback (statements) + analyst consensus
# ══════════════════════════════════════════════════════════════════════════
def _yrow(df, *names):
    if df is None or getattr(df, "empty", True):
        return None
    idx = {str(i).strip().lower(): i for i in df.index}
    for n in names:
        if n.lower() in idx:
            return df.loc[idx[n.lower()]]
    return None


def _cr(v):
    try:
        f = float(v)
        if f != f:                                   # NaN
            return None
        return round(f / 1e7, 2)
    except (TypeError, ValueError):
        return None


def _yahoo_statements(sym: str) -> dict:
    import yfinance as yf
    try:
        from fundamentals import _rate_limit_yf
        _rate_limit_yf()
    except Exception:
        pass
    t = yf.Ticker(f"{sym}.NS")
    fin, bs, cf, qf = t.financials, t.balance_sheet, t.cashflow, t.quarterly_financials

    def build(fin_df, bs_df=None, cf_df=None):
        if fin_df is None or getattr(fin_df, "empty", True):
            return []
        cols = sorted(fin_df.columns)                # oldest → newest
        rev = _yrow(fin_df, "Total Revenue", "Operating Revenue")
        ebit = _yrow(fin_df, "EBIT", "Operating Income")
        ebitda = _yrow(fin_df, "EBITDA", "Normalized EBITDA")
        ni = _yrow(fin_df, "Net Income", "Net Income Common Stockholders")
        eps = _yrow(fin_df, "Diluted EPS", "Basic EPS")
        debt = _yrow(bs_df, "Total Debt") if bs_df is not None else None
        tassets = _yrow(bs_df, "Total Assets") if bs_df is not None else None
        eq = _yrow(bs_df, "Stockholders Equity", "Common Stock Equity") if bs_df is not None else None
        ocf = _yrow(cf_df, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities") \
            if cf_df is not None else None
        out = []
        for c in cols:
            def g(s):
                try:
                    return s[c] if s is not None and c in s.index else None
                except Exception:
                    return None
            r = _cr(g(rev))
            op = _cr(g(ebitda)) if ebitda is not None else _cr(g(ebit))
            out.append({
                "label": c.strftime("%b-%y") if hasattr(c, "strftime") else str(c),
                "revenue": r, "op_profit": op,
                "opm": round(op / r * 100, 1) if (op is not None and r) else None,
                "net_profit": _cr(g(ni)),
                "eps": (round(float(g(eps)), 2) if g(eps) is not None and g(eps) == g(eps) else None),
                "ocf": _cr(g(ocf)), "borrowings": _cr(g(debt)), "networth": _cr(g(eq)),
                "roce": None, "total_assets": _cr(g(tassets)), "deposits": None,
            })
        return out

    return {"source": "Yahoo Finance", "unit": "₹ Cr",
            "years": build(fin, bs, cf)[-6:], "quarters": build(qf)[-8:]}


# ══════════════════════════════════════════════════════════════════════════
# Period parsing & data-freshness policy
# ══════════════════════════════════════════════════════════════════════════
_FMTS = ("%b %Y", "%b-%y", "%b-%Y", "%B %Y", "%b'%y", "%b %y")


def period_end(label: str) -> Optional[date]:
    """'Jun 2026' / 'Jun-26' → 30-Jun-2026 (month end). None if unparseable."""
    lab = (label or "").strip()
    for f in _FMTS:
        try:
            d = datetime.strptime(lab, f)
            return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        except ValueError:
            continue
    return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def expected_latest_quarter(today: date, grace_days: int = 10) -> date:
    """
    Latest quarter whose results should already be public on `today`.
    SEBI LODR: 45 days after quarter-end (60 days for the March quarter), plus a few
    days of grace for aggregators to catch up.
    """
    y = today.year
    ends = []
    for yy in (y - 1, y):
        for m in (3, 6, 9, 12):
            ends.append(date(yy, m, calendar.monthrange(yy, m)[1]))
    for qe in sorted(ends, reverse=True):
        due = qe.toordinal() + (60 if qe.month == 3 else 45) + grace_days
        if due <= today.toordinal():
            return qe
    return ends[0]


def freshness(quarters: List[dict], today: Optional[date] = None, max_age_days: Optional[int] = None) -> dict:
    """
    {status: FRESH|LAGGING|STALE|MISSING, latest, expected, lag_quarters, age_days, label}
      FRESH    latest quarter ≥ the quarter that should already be reported
      LAGGING  exactly one reporting cycle behind (may be a late filer / slow aggregator)
      STALE    two or more cycles behind, or older than max_age_days from its quarter-end
    """
    if max_age_days is None:
        try:
            from config import MAX_QUARTER_AGE_DAYS as max_age_days
        except Exception:
            max_age_days = 135
    today = today or date.today()
    exp = expected_latest_quarter(today)
    latest = period_end(quarters[-1]["label"]) if quarters else None
    if latest is None:
        return {"status": "MISSING", "latest": None, "expected": exp, "lag_quarters": None,
                "age_days": None, "label": quarters[-1]["label"] if quarters else None}
    lag_q = max(0, _months_between(latest, exp) // 3)
    age = (today - latest).days
    status = "FRESH" if lag_q == 0 else "LAGGING" if lag_q == 1 else "STALE"
    if age > max_age_days and status == "FRESH":      # defensive: clock/label oddities
        status = "LAGGING"
    if age > max_age_days and lag_q >= 1:
        status = "STALE" if lag_q >= 2 else status
    return {"status": status, "latest": latest, "expected": exp, "lag_quarters": lag_q,
            "age_days": age, "label": quarters[-1]["label"]}


def _fresher(a: List[dict], b: List[dict]) -> tuple:
    """Pick the list whose last period is most recent; tie → longer, then `a`. Returns (list, 'a'|'b')."""
    ea = period_end(a[-1]["label"]) if a else None
    eb = period_end(b[-1]["label"]) if b else None
    if ea is None and eb is None:
        return (a, "a") if len(a) >= len(b) else (b, "b")
    if eb is None or (ea is not None and ea > eb):
        return a, "a"
    if ea is None or eb > ea:
        return b, "b"
    return (a, "a") if len(a) >= len(b) else (b, "b")


def get_statements(sym: str, today: Optional[date] = None) -> Optional[dict]:
    """
    Normalised statements. Screener first (10 yrs); Yahoo is consulted when Screener is thin
    or its quarters are not current — and the FRESHER source wins per block (annual / quarterly).
    The old logic replaced Screener wholesale with Yahoo whenever Screener had < 3 annual
    periods (typical for recent IPOs), which let Yahoo's stale quarters overwrite fresh ones.
    """
    sym = sym.upper().replace(".NS", "")
    cached = _cget(f"stm_{sym}")
    if cached is not None and today is None:
        return cached or None

    ext = get_screener_extras(sym)
    s_years, s_quarters = ext.get("years") or [], ext.get("quarters") or []
    s_fresh = freshness(s_quarters, today) if s_quarters else {"status": "MISSING"}

    y_years, y_quarters = [], []
    if len(s_years) < 4 or s_fresh["status"] != "FRESH":
        try:
            y = _yahoo_statements(sym)
            y_years, y_quarters = y["years"], y["quarters"]
        except Exception as e:
            logger.info(f"[research_data] yahoo statements {sym}: {str(e)[:100]}")

    years, ysrc = _fresher(s_years, y_years)
    quarters, qsrc = _fresher(s_quarters, y_quarters)
    if not years and not quarters:
        _cset(f"stm_{sym}", {})
        return None
    names = {"a": "Screener.in", "b": "Yahoo Finance"}
    # a Yahoo annual block can't carry lender detail Screener would; keep Screener years if equally fresh
    result = {
        "source": names[ysrc] if years else names[qsrc],
        "sources": {"years": names[ysrc] if years else None, "quarters": names[qsrc] if quarters else None},
        "unit": "₹ Cr", "years": years, "quarters": quarters, "layout": ext.get("layout", "corporate"),
    }
    result["freshness"] = freshness(quarters, today) if quarters else {"status": "MISSING"}
    if today is None:
        _cset(f"stm_{sym}", result)
    return result


def get_consensus(sym: str, ltp: float) -> Optional[dict]:
    """Analyst consensus target from Yahoo (often absent for Indian small caps)."""
    sym = sym.upper().replace(".NS", "")
    cached = _cget(f"cons_{sym}")
    if cached is not None:
        return cached or None
    out = None
    try:
        import yfinance as yf
        t = yf.Ticker(f"{sym}.NS")
        apt = getattr(t, "analyst_price_targets", None)
        if apt and isinstance(apt, dict):
            mean = apt.get("mean") or apt.get("median")
            if mean and ltp and 0.3 < float(mean) / ltp < 3.0:        # sanity band
                out = {"mean": float(mean), "high": apt.get("high"), "low": apt.get("low"),
                       "upside": (float(mean) / ltp - 1) * 100}
    except Exception as e:
        logger.info(f"[research_data] consensus {sym}: {str(e)[:100]}")
    _cset(f"cons_{sym}", out or {})
    return out


# ══════════════════════════════════════════════════════════════════════════
# Derived metrics from statements (used by the research engine)
# ══════════════════════════════════════════════════════════════════════════
def cagr(first: Optional[float], last: Optional[float], years: float) -> Optional[float]:
    try:
        if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
            return None
        return ((last / first) ** (1.0 / years) - 1) * 100
    except Exception:
        return None


def series_cagr(rows: List[dict], key: str, years: int) -> Optional[float]:
    vals = [r.get(key) for r in rows]
    if len(vals) <= years:
        return None
    return cagr(vals[-(years + 1)], vals[-1], years)
