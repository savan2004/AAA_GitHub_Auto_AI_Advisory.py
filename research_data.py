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
import logging
import threading
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
        })
    return out[-10:]


def parse_shareholding(html: str) -> List[dict]:
    """Quarterly shareholding pattern → [{label, promoters, fii, dii, public}]"""
    tabs = _tables_in(_section(html, "shareholding"))
    if not tabs:
        return []
    hdr, rows = _table_to_rows(tabs[0])
    pro = _pick(rows, "Promoters")
    if not hdr or pro is None:
        return []
    fii = _pick(rows, "FIIs", "FII")
    dii = _pick(rows, "DIIs", "DII")
    pub = _pick(rows, "Public")
    out = []
    for i, lab in enumerate(hdr):
        def g(arr):
            return arr[i] if arr is not None and i < len(arr) else None
        out.append({"label": lab, "promoters": g(pro), "fii": g(fii), "dii": g(dii), "public": g(pub)})
    return out[-8:]


def get_screener_extras(sym: str) -> dict:
    sym = sym.upper().replace(".NS", "")
    cached = _cget(f"scr_{sym}")
    if cached is not None:
        return cached
    html = _screener_html(sym)
    out: dict = {}
    if html:
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
                "roce": None,
            })
        return out

    return {"source": "Yahoo Finance", "unit": "₹ Cr",
            "years": build(fin, bs, cf)[-6:], "quarters": build(qf)[-8:]}


def get_statements(sym: str) -> Optional[dict]:
    """Normalised statements. Screener first (10 yrs), Yahoo as fallback."""
    sym = sym.upper().replace(".NS", "")
    cached = _cget(f"stm_{sym}")
    if cached is not None:
        return cached or None

    ext = get_screener_extras(sym)
    years, quarters = ext.get("years") or [], ext.get("quarters") or []
    result = None
    if years or quarters:
        result = {"source": "Screener.in", "unit": "₹ Cr", "years": years, "quarters": quarters}

    if not result or len(result["years"]) < 3:
        try:
            y = _yahoo_statements(sym)
            if y["years"] or y["quarters"]:
                if result:                             # keep Screener quarters if Yahoo has none
                    y["quarters"] = y["quarters"] or result["quarters"]
                result = y
        except Exception as e:
            logger.info(f"[research_data] yahoo statements {sym}: {str(e)[:100]}")

    _cset(f"stm_{sym}", result or {})
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
