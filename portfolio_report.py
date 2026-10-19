"""
portfolio_report.py — 🧾 Client Portfolio Report
────────────────────────────────────────────────────────────────────────────────
A client sends holdings (Symbol, Qty, Buy Price) as a CSV / Excel file or pasted
text. The bot returns:

  1. Portfolio Health PDF   totals, P&L, weights, concentration, sector mix,
                            correlation, portfolio volatility / beta / drawdown,
                            verdict per holding and an action plan
  2. An 8-page research report for each holding (up to PORTFOLIO_MAX_STOCKS),
     with the client's own position, P&L, weight and verdict built in

Everything is computed from market data — nothing is defaulted or invented. If a
number cannot be computed (no price history, no fundamentals) the report says so.

Privacy: holdings are processed in memory and the generated files are deleted
after sending. Nothing about a client's holdings is logged.

Parsing accepts: broker exports (Zerodha / Groww / Upstox style headers), plain
CSV, .xlsx, or lines such as  RELIANCE 50 1250  /  RELIANCE, 50, 1250.
"""

import io
import os
import re
import csv
import math
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak, KeepTogether

import report_generator as rg
import report_charts as charts
import research_engine as eng
from config import BRAND_NAME as BRAND
from market_data import bulk_history, index_history
from sector_report import Tp, heat, _pct

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MAX_ROWS = 200
MAX_FILE_BYTES = 1_000_000
MAX_ANALYZE = int(os.getenv("PORTFOLIO_MAX_STOCKS", "10"))
MAX_WEIGHT = float(os.getenv("PORTFOLIO_MAX_WEIGHT", "15"))
SECTOR_LIMIT = 35.0

TEMPLATE_CSV = "Symbol,Qty,Buy Price\nRELIANCE,50,1250.00\nSBIN,100,750.00\nBHEL,2000,90.00\n"


# ══════════════════════════════════════════════════════════════════════════
# PARSING
# ══════════════════════════════════════════════════════════════════════════
def _norm_header(h) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(h or "").lower()).strip()


_SYMBOL_H = {"symbol", "scrip", "scrip name", "scripname", "instrument", "stock", "stock name", "stockname",
             "tradingsymbol", "trading symbol", "ticker", "security", "security name", "company", "company name", "name"}
_QTY_H = {"qty", "quantity", "shares", "no of shares", "units", "holding qty", "holdings", "net qty", "balance qty"}
_PRICE_H = {"buy price", "buyprice", "avg price", "average price", "avg cost", "average cost", "avg buy price",
            "average buy price", "buy rate", "buy avg", "avgprice", "purchase price", "cost price", "rate", "price", "cost",
            "avg buy", "buy average"}


def _to_float(x) -> Optional[float]:
    if x is None:
        return None
    t = str(x).strip()
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"(?i)(₹|rs\.?|inr)", "", t).replace(",", "").replace("(", "").replace(")", "").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def clean_symbol(raw, resolver: Optional[Callable[[str], Optional[str]]] = None) -> Optional[str]:
    s = str(raw or "").strip().upper()
    if not s:
        return None
    s = re.sub(r"^(NSE|BSE|NSE_EQ)\s*[:\-]\s*", "", s)
    s = re.sub(r"\.(NS|BO|NSE|BSE)$", "", s)
    s = re.sub(r"-(EQ|BE|SM|ST)$", "", s)
    s = s.strip()
    if re.fullmatch(r"[A-Z0-9&\-]{1,20}", s):
        return s
    if resolver and re.search(r"[A-Za-z]{3,}", s):          # a company name like "Reliance Industries"
        try:
            r = resolver(str(raw).strip())
            return r.upper() if r else None
        except Exception:
            return None
    return None


def _merge(holdings: List[dict]) -> List[dict]:
    """Duplicate symbols (several lots) → one row with the weighted-average buy price."""
    agg: Dict[str, dict] = {}
    for h in holdings:
        a = agg.setdefault(h["symbol"], {"symbol": h["symbol"], "qty": 0.0, "cost": 0.0})
        a["qty"] += h["qty"]
        a["cost"] += h["qty"] * h["buy"]
    return [{"symbol": a["symbol"], "qty": a["qty"], "buy": (a["cost"] / a["qty"]) if a["qty"] else 0.0}
            for a in agg.values()]


def parse_rows(rows: List[List], resolver=None) -> Tuple[List[dict], List[str]]:
    """rows: list of cell lists (strings or numbers). → (holdings, issues)."""
    issues: List[str] = []
    rows = [r for r in rows if r and any(str(c or "").strip() for c in r)][: MAX_ROWS + 10]
    if not rows:
        return [], ["The file is empty."]

    col = {"symbol": None, "qty": None, "price": None}
    header_at = None
    for i, r in enumerate(rows[:6]):
        norm = [_norm_header(c) for c in r]
        found = {"symbol": None, "qty": None, "price": None}
        for j, c in enumerate(norm):
            if c in _SYMBOL_H and found["symbol"] is None:
                found["symbol"] = j
            elif c in _QTY_H and found["qty"] is None:
                found["qty"] = j
            elif c in _PRICE_H and found["price"] is None:
                found["price"] = j
        if sum(v is not None for v in found.values()) >= 2:
            col, header_at = found, i
            break
    if header_at is None:                                     # no header → positional Symbol, Qty, Price
        col = {"symbol": 0, "qty": 1, "price": 2}
    if None in col.values():
        missing = [k for k, v in col.items() if v is None]
        return [], [f"Could not find the {', '.join(missing)} column. Expected headers like: Symbol, Qty, Buy Price."]

    out: List[dict] = []
    start = (header_at + 1) if header_at is not None else 0
    for n, r in enumerate(rows[start:], start=start + 1):
        if len(out) >= MAX_ROWS:
            issues.append(f"Only the first {MAX_ROWS} rows were read.")
            break
        get = lambda k: r[col[k]] if col[k] is not None and col[k] < len(r) else None
        raw_sym = get("symbol")
        if re.match(r"(?i)^\s*(total|grand total|net)\b", str(raw_sym or "")):
            continue
        sym = clean_symbol(raw_sym, resolver)
        qty, buy = _to_float(get("qty")), _to_float(get("price"))
        if sym is None:
            issues.append(f"Row {n}: could not read a stock symbol from “{str(raw_sym)[:25]}”.")
        elif qty is None or qty <= 0:
            issues.append(f"Row {n} ({sym}): invalid quantity.")
        elif buy is None or buy < 0:
            issues.append(f"Row {n} ({sym}): invalid buy price.")
        else:
            out.append({"symbol": sym, "qty": qty, "buy": buy})
    merged = _merge(out)
    if len(merged) < len(out):
        issues.append(f"{len(out) - len(merged)} duplicate symbol row(s) were merged using the weighted-average buy price.")
    return merged, issues


def parse_text(text: str, resolver=None) -> Tuple[List[dict], List[str]]:
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if re.search(r"[,\t;|]", line):
            cells = [c.strip() for c in re.split(r"[,\t;|]", line)]
        else:
            cells = [c for c in re.split(r"\s*[@xX×]\s*|\s+", line) if c]
        rows.append(cells)
    return parse_rows(rows, resolver)


def parse_file(name: str, data: bytes, resolver=None) -> Tuple[List[dict], List[str]]:
    if len(data) > MAX_FILE_BYTES:
        return [], ["File is too large (limit 1 MB)."]
    low = (name or "").lower()
    try:
        if low.endswith((".xlsx", ".xlsm")):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            rows = [[("" if c is None else c) for c in r] for r in ws.iter_rows(values_only=True)]
            return parse_rows(rows, resolver)
        if low.endswith(".xls"):
            return [], ["Old .xls files are not supported — please save as .xlsx or .csv."]
        text = None
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            return [], ["Could not read the file encoding."]
        try:
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t|")
            rows = list(csv.reader(io.StringIO(text), dialect))
        except Exception:
            rows = list(csv.reader(io.StringIO(text)))
        if rows and max(len(r) for r in rows) < 2:           # plain "SYMBOL QTY PRICE" lines
            return parse_text(text, resolver)
        return parse_rows(rows, resolver)
    except Exception as e:
        logger.warning(f"[portfolio] file parse failed: {type(e).__name__}")
        return [], ["Could not read the file. Please send a CSV or .xlsx with columns Symbol, Qty, Buy Price."]


# ══════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════════════════
def _price_all(holdings: List[dict]) -> Tuple[List[dict], Dict[str, pd.DataFrame], Optional[pd.DataFrame]]:
    hist = bulk_history([h["symbol"] for h in holdings], "1y", chunk_size=100)
    nifty = index_history("^NSEI", "1y")
    recs = []
    for h in holdings:
        df = hist.get(h["symbol"])
        ltp = float(df["Close"].iloc[-1]) if df is not None and len(df) else None
        rec = dict(h, name=h["symbol"], ltp=ltp, priced=ltp is not None, R=None, error=None, sector="Not analysed")
        rec["invested"] = h["qty"] * h["buy"]
        rec["value"] = h["qty"] * ltp if ltp is not None else None
        recs.append(rec)
    total = sum(r["value"] for r in recs if r["value"])
    for r in recs:
        r["weight_pct"] = (r["value"] / total * 100) if (r["value"] and total) else 0.0
        if r["priced"]:
            r["pnl"] = r["value"] - r["invested"]
            r["pnl_pct"] = (r["ltp"] / r["buy"] - 1) * 100 if r["buy"] else None
            r["audit_flag"], r["audit_note"] = eng.holding_audit(r["buy"], r["ltp"])
    return recs, hist, nifty


def portfolio_metrics(recs: List[dict], hist: Dict[str, pd.DataFrame], nifty: Optional[pd.DataFrame]) -> dict:
    """Combined value series of TODAY's holdings over the past year → real portfolio vol / drawdown / beta."""
    priced = [r for r in recs if r["priced"]]
    out: dict = {"ok": False}
    if not priced:
        return out
    cols = {}
    for r in priced:
        df = hist.get(r["symbol"])
        if df is not None and len(df) > 60:
            cols[r["symbol"]] = df["Close"] * r["qty"]
    if not cols:
        return out
    vals = pd.concat(cols, axis=1).sort_index().ffill().dropna()
    if len(vals) < 60:
        return out
    total = vals.sum(axis=1)
    rets = total.pct_change().dropna()
    dd = total / total.cummax() - 1
    m = {"ok": True, "series": total, "vol": float(rets.std() * math.sqrt(252) * 100),
         "maxdd": float(dd.min() * 100), "curdd": float(dd.iloc[-1] * 100),
         "var95": float(np.percentile(rets, 5) * 100), "ret_1y": float((total.iloc[-1] / total.iloc[0] - 1) * 100),
         "covered": len(cols), "of": len(priced), "beta": None, "corr": None, "pairs": []}
    try:
        if nifty is not None and not nifty.empty:
            nr = nifty["Close"].pct_change().dropna()
            j = pd.concat([rets, nr], axis=1, join="inner").dropna()
            j.columns = ["p", "n"]
            if len(j) > 60 and j["n"].var() > 0:
                m["beta"] = float(j["p"].cov(j["n"]) / j["n"].var())
            m["nifty_series"] = nifty["Close"].reindex(total.index).ffill().bfill()
    except Exception:
        pass
    # correlation among the biggest holdings
    top = sorted(priced, key=lambda r: -(r["value"] or 0))[:12]
    syms = [r["symbol"] for r in top if r["symbol"] in vals.columns]
    if len(syms) >= 3:
        cm = vals[syms].pct_change().dropna().corr()
        m["corr"] = cm
        for i in range(len(syms)):
            for k in range(i + 1, len(syms)):
                if cm.iloc[i, k] >= 0.80:
                    m["pairs"].append((syms[i], syms[k], float(cm.iloc[i, k])))
    return m


def sector_weights(recs: List[dict]) -> Dict[str, float]:
    tot = sum(r["value"] for r in recs if r["value"]) or 1
    d: Dict[str, float] = {}
    for r in recs:
        if r["value"]:
            d[r["sector"]] = d.get(r["sector"], 0.0) + r["value"] / tot * 100
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def findings(recs, pm, sec) -> List[str]:
    out: List[str] = []
    priced = [r for r in recs if r["priced"]]
    scored = [r for r in priced if r["R"]]
    tot_inv = sum(r["invested"] for r in priced)
    tot_val = sum(r["value"] for r in priced)
    if tot_inv:
        out.append(f"Portfolio value <b>{rg.inr(tot_val, 0)}</b> against <b>{rg.inr(tot_inv, 0)}</b> invested — "
                   f"unrealised P&amp;L <b>{rg.colored(rg.inr(tot_val - tot_inv, 0) + f' ({(tot_val / tot_inv - 1) * 100:+.1f}%)', rg.GREEN_H if tot_val >= tot_inv else rg.RED_H)}</b>.")
    big = [r for r in priced if r["weight_pct"] > MAX_WEIGHT]
    if big:
        out.append("<b>Concentration:</b> " + ", ".join(f"{r['symbol']} ({r['weight_pct']:.1f}%)" for r in big)
                   + f" {'exceeds' if len(big) == 1 else 'exceed'} the {MAX_WEIGHT:.0f}% single-stock guideline.")
    if sec:
        s0, w0 = next(iter(sec.items()))
        if w0 > SECTOR_LIMIT and "Not analysed" not in s0 and "unclassified" not in s0.lower():
            out.append(f"<b>Sector concentration:</b> {rg.strip_emoji(s0)} is {w0:.0f}% of the portfolio (guideline {SECTOR_LIMIT:.0f}%).")
    if scored:
        wsc = sum(r["R"]["sc"]["composite"] * r["value"] for r in scored) / sum(r["value"] for r in scored)
        out.append(f"Value-weighted composite score is <b>{wsc:.0f}/100</b> across {len(scored)} analysed holdings.")
        weak = sorted([r for r in scored if r["R"]["sc"]["composite"] < 48], key=lambda r: -r["value"])
        if weak:
            out.append("<b>Weakest holdings (score &lt; 48):</b> " + ", ".join(f"{r['symbol']} ({r['R']['sc']['composite']})" for r in weak[:5]) + ".")
        strong = sorted([r for r in scored if r["R"]["sc"]["composite"] >= 62], key=lambda r: -r["R"]["sc"]["composite"])
        if strong:
            out.append("<b>Strongest holdings:</b> " + ", ".join(f"{r['symbol']} ({r['R']['sc']['composite']})" for r in strong[:5]) + ".")
    if pm.get("ok"):
        beta = f", beta {pm['beta']:.2f} vs Nifty 50" if pm.get("beta") is not None else ""
        out.append(f"Today's holdings would have shown <b>{pm['vol']:.1f}%</b> annualised volatility and a <b>{pm['maxdd']:.1f}%</b> maximum drawdown over the past year{beta}.")
    if pm.get("pairs"):
        out.append("<b>Highly correlated pairs (≥0.80):</b> " + ", ".join(f"{a}/{b} ({c:.2f})" for a, b, c in pm["pairs"][:4])
                   + " — these move together, so diversification is lower than the holding count suggests.")
    flagged = [r for r in priced if r.get("audit_flag") in ("VERIFY COST BASIS", "ZERO COST")]
    if flagged:
        out.append("<b>Verify cost basis:</b> " + ", ".join(r["symbol"] for r in flagged) + " (possible split/bonus/demerger adjustment).")
    missing = [r["symbol"] for r in recs if not r["priced"]]
    if missing:
        out.append("<b>No price data:</b> " + ", ".join(missing) + " — check the ticker symbol.")
    return out


# ══════════════════════════════════════════════════════════════════════════
# CHARTS
# ══════════════════════════════════════════════════════════════════════════
def chart_allocation(recs, sec) -> Optional[str]:
    try:
        rows = sorted([r for r in recs if r["value"]], key=lambda r: r["weight_pct"])
        if not rows:
            return None
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, max(2.6, 0.27 * len(rows) + 1.2)), gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.55})
        y = np.arange(len(rows))
        a1.barh(y, [r["weight_pct"] for r in rows], color=[charts.RED if r["weight_pct"] > MAX_WEIGHT else charts.NAVY for r in rows], height=0.62)
        for yi, r in zip(y, rows):
            a1.text(r["weight_pct"] + 0.3, yi, f"{r['weight_pct']:.1f}%", va="center", fontsize=7, color=charts.NAVY)
        a1.axvline(MAX_WEIGHT, color=charts.AMBER, lw=0.9, ls="--")
        a1.set_yticks(y)
        a1.set_yticklabels([r["symbol"] for r in rows], fontsize=7.5)
        a1.set_title("Weight by holding", loc="left")
        a1.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        a1.grid(True, axis="x")
        items = list(sec.items())[:8][::-1]
        y2 = np.arange(len(items))
        a2.barh(y2, [w for _, w in items], color=[charts.RED if w > SECTOR_LIMIT else charts.ACCENT for _, w in items], height=0.62)
        for yi, (_, w) in zip(y2, items):
            a2.text(w + 0.5, yi, f"{w:.0f}%", va="center", fontsize=7, color=charts.NAVY)
        a2.set_yticks(y2)
        a2.set_yticklabels([rg.strip_emoji(k)[:22] for k, _ in items], fontsize=7.5)
        a2.set_title("Weight by sector", loc="left")
        a2.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        a2.grid(True, axis="x")
        return charts._save(fig, "pf_alloc.png")
    except Exception as e:
        logger.warning(f"alloc chart failed: {e}")
        plt.close("all")
        return None


def chart_value(pm) -> Optional[str]:
    try:
        s = pm["series"]
        fig, ax = plt.subplots(figsize=(7.4, 2.5))
        x = np.arange(len(s))
        ax.plot(x, (s / s.iloc[0] * 100).values, color=charts.NAVY, lw=1.5, label="Your holdings (today's quantities)")
        if pm.get("nifty_series") is not None:
            n = pm["nifty_series"]
            ax.plot(x, (n / n.iloc[0] * 100).values, color="#9AA5B4", lw=1.2, label="Nifty 50")
        ax.axhline(100, color=charts.GREY, lw=0.5, ls=":")
        ticks = np.linspace(0, len(s) - 1, 5).astype(int)
        ax.set_xticks(ticks)
        ax.set_xticklabels([s.index[i].strftime("%b %y") for i in ticks], fontsize=7)
        ax.grid(True, axis="y")
        ax.legend(frameon=False, fontsize=7.5, loc="upper left")
        ax.set_title("Past year: value of today's holdings vs Nifty 50 (rebased to 100)", loc="left")
        return charts._save(fig, "pf_value.png")
    except Exception as e:
        logger.warning(f"value chart failed: {e}")
        plt.close("all")
        return None


def chart_corr(pm) -> Optional[str]:
    try:
        cm = pm.get("corr")
        if cm is None:
            return None
        n = len(cm)
        fig, ax = plt.subplots(figsize=(5.2, 4.3))
        im = ax.imshow(cm.values, cmap="RdYlGn_r", vmin=-0.2, vmax=1.0)
        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        ax.set_xticklabels(cm.columns, rotation=45, ha="right", fontsize=7.5)
        ax.set_yticklabels(cm.index, fontsize=7.5)
        for i in range(n):
            for j in range(n):
                ax.text(j, i, f"{cm.values[i, j]:.2f}", ha="center", va="center", fontsize=6.5,
                        color="white" if cm.values[i, j] > 0.75 else charts.NAVY)
        ax.set_title("Return correlation — largest holdings", loc="left")
        fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        for sp in ax.spines.values():
            sp.set_visible(False)
        return charts._save(fig, "pf_corr.png")
    except Exception as e:
        logger.warning(f"corr chart failed: {e}")
        plt.close("all")
        return None


# ══════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════
def _sev_hex(sev):
    return {0: rg.RED_H, 1: rg.AMBER_H, 2: rg.AMBER_H, 3: rg.GREEN_H}.get(sev, rg.GREY_H)


def build_summary_pdf(recs, pm, sec, client: str, path: str, notes: List[str]) -> str:
    story: list = []
    W = rg.W
    now = datetime.now(IST).strftime("%d-%b-%Y %H:%M IST")
    priced = [r for r in recs if r["priced"]]
    tot_inv = sum(r["invested"] for r in priced)
    tot_val = sum(r["value"] for r in priced)
    pnl = tot_val - tot_inv
    scored = [r for r in priced if r["R"]]

    banner = Table([[rg.P(rg.esc(BRAND.upper()) + "  |  PORTFOLIO REPORT", "brand"), ""],
                    [rg.P("Portfolio Health Report", "title"), rg.P(rg.inr(tot_val, 0), "px")],
                    [rg.P((f"Prepared for {rg.esc(client)}  ·  " if client else "") + f"{len(recs)} holdings", "sub"),
                     rg.P(("<font color='{}'>{:+.1f}% unrealised</font>").format("#7FE0B0" if pnl >= 0 else "#FF9A9A", (pnl / tot_inv * 100) if tot_inv else 0), "pxs")]],
                   colWidths=[W * 0.66, W * 0.34])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), rg.NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("TOPPADDING", (0, 0), (-1, 0), 11), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                                ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 2), (-1, 2), 12),
                                ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 13)]))
    story += [banner, Spacer(1, 3), rg.P(f"Prepared by: <b>Senior Market Analyst Desk</b>, {BRAND} (automated research engine)  ·  {now}  ·  Prices as of last close", "small"), Spacer(1, 8)]

    wsc = (sum(r["R"]["sc"]["composite"] * r["value"] for r in scored) / sum(r["value"] for r in scored)) if scored else None
    vcount: Dict[str, int] = {}
    for r in scored:
        k = r["R"]["holding"]["verdict"].split("—")[0].split("(")[0].strip()
        vcount[k] = vcount.get(k, 0) + 1
    story.append(rg.kv([
        ["Invested", rg.inr(tot_inv, 0), "Current value", rg.inr(tot_val, 0)],
        ["Unrealised P&amp;L", rg.colored("{} ({:+.1f}%)".format(rg.inr(pnl, 0), (pnl / tot_inv * 100) if tot_inv else 0), rg.GREEN_H if pnl >= 0 else rg.RED_H),
         "Value-weighted score", f"{wsc:.0f}/100" if wsc is not None else "—"],
        ["Portfolio volatility (ann.)", rg.n(pm.get("vol"), 1, "%") if pm.get("ok") else "—", "Max drawdown (1Y)", rg.n(pm.get("maxdd"), 1, "%") if pm.get("ok") else "—"],
        ["Beta vs Nifty 50", rg.n(pm.get("beta"), 2) if pm.get("ok") else "—", "Holdings analysed in full", f"{len(scored)} of {len(recs)}"],
    ]))
    story += [Spacer(1, 6), rg.P("Key findings", "h2")] + rg.bullets(findings(recs, pm, sec), rg.NAVY_H)
    if vcount:
        story += [Spacer(1, 4), rg.P("Verdict mix: " + " · ".join(f"<b>{k}</b> {v}" for k, v in sorted(vcount.items(), key=lambda kv: -kv[1])), "small")]

    # ── holdings table ──
    story += rg.section("Holdings", "Sorted by portfolio weight; P&L uses your buy price against the last close")
    hdr = [Tp("Stock", "h"), Tp("Qty", "hr"), Tp("Avg buy", "hr"), Tp("CMP", "hr"), Tp("Invested", "hr"), Tp("Value", "hr"),
           Tp("P&amp;L %", "hr"), Tp("Weight", "hr"), Tp("Score", "hr"), Tp("Verdict", "h")]
    data, extra = [hdr], []
    for i, r in enumerate(sorted(recs, key=lambda r: -r["weight_pct"]), start=1):
        if not r["priced"]:
            data.append([Tp(rg.esc(r["symbol"]), "cb"), Tp(f"{r['qty']:,.0f}", "r"), Tp(f"{r['buy']:,.2f}", "r")] + [Tp("—", "r")] * 5 + [Tp("no price", "c")])
            continue
        hv = r["R"]["holding"] if r["R"] else None
        data.append([Tp(rg.esc(r["symbol"]), "cb"), Tp(f"{r['qty']:,.0f}", "r"), Tp(f"{r['buy']:,.2f}", "r"), Tp(f"{r['ltp']:,.2f}", "r"),
                     Tp(f"{r['invested']:,.0f}", "r"), Tp(f"{r['value']:,.0f}", "r"), Tp(_pct(r["pnl_pct"]), "r"),
                     Tp(rg.colored(f"{r['weight_pct']:.1f}%", rg.RED_H) if r["weight_pct"] > MAX_WEIGHT else f"{r['weight_pct']:.1f}%", "r"),
                     Tp(str(r["R"]["sc"]["composite"]) if r["R"] else "—", "rb"),
                     Tp(rg.colored(rg.esc(hv["verdict"].split(" (")[0]), _sev_hex(hv["severity"])) if hv else rg.esc("not analysed" if not r["error"] else "analysis failed"), "c")])
        extra.append(("BACKGROUND", (6, i), (6, i), heat(r["pnl_pct"], 25)))
    widths = [21 * mm, 13 * mm, 16 * mm, 16 * mm, 20 * mm, 20 * mm, 14 * mm, 14 * mm, 11 * mm, 33 * mm]
    story.append(rg.table(data, widths, zebra=False, extra=extra + [("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))

    # ── allocation & risk ──
    ac = chart_allocation(recs, sec)
    if ac and rg.img(ac, W):
        story.append(KeepTogether(rg.section("Allocation", f"Red bars exceed the {MAX_WEIGHT:.0f}% single-stock / {SECTOR_LIMIT:.0f}% sector guideline")
                                  + [rg.img(ac, W)]))
    if pm.get("ok"):
        head = rg.section("Portfolio Risk", f"Based on {pm['covered']} of {pm['of']} holdings with a full year of price history")
        vc = chart_value(pm)
        story.append(KeepTogether(head + [x for x in [rg.img(vc, W) if vc else None] if x]))
        story += [Spacer(1, 5), rg.kv([
            ["Annualised volatility", f"{pm['vol']:.1f}%", "Max drawdown (1Y)", f"{pm['maxdd']:.1f}%"],
            ["Current drawdown", f"{pm['curdd']:.1f}%", "1-day VaR (95%, historical)", f"{pm['var95']:.2f}%"],
            ["Beta vs Nifty 50", rg.n(pm.get("beta"), 2), "1-year change in value (today's qty)", f"{pm['ret_1y']:+.1f}%"],
        ]), rg.P("This replays today's quantities over the past year — it measures risk, not your actual historic P&amp;L (purchase dates are unknown).", "tiny")]
        cc = chart_corr(pm)
        if cc:
            story += [Spacer(1, 6), KeepTogether([rg.P("Diversification check", "h2")] + [x for x in [rg.img(cc, W * 0.62)] if x])]

    # ── action plan ──
    story += rg.section("Action Plan", "Rule-based verdicts, most urgent first — a full 8-page report for each stock is attached separately")
    acts = sorted([r for r in scored], key=lambda r: (r["R"]["holding"]["severity"], -r["weight_pct"]))
    if acts:
        data = [[Tp("Stock", "h"), Tp("Verdict", "h"), Tp("What to do", "h"), Tp("Stop", "hr"), Tp("Support", "hr"), Tp("Resistance", "hr")]]
        for r in acts:
            hv, t = r["R"]["holding"], r["R"]["t"]
            sup = t["supports"][0]["price"] if t["supports"] else None
            res = t["resistances"][0]["price"] if t["resistances"] else None
            data.append([Tp(f"<b>{rg.esc(r['symbol'])}</b><br/><font size=6 color='{rg.GREY_H}'>{rg.esc(r['R']['name'][:26])}</font>"),
                         Tp(rg.colored(rg.esc(hv["verdict"]), _sev_hex(hv["severity"]))), Tp(rg.esc(hv["action"])),
                         Tp(rg.inr(r["R"]["plan"]["sl"]), "r"), Tp(rg.inr(sup), "r"), Tp(rg.inr(res), "r")])
        story.append(rg.table(data, [22 * mm, 30 * mm, 70 * mm, 17 * mm, 19 * mm, 20 * mm],
                              extra=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    else:
        story.append(rg.callout("No holding could be analysed in full, so no verdicts are shown. See the notes below.", rg.AMBER_H, "#FFF9E8"))

    # ── notes ──
    notes_all = list(notes)
    notes_all.append("Verdicts combine the six-pillar composite score, a trend gate (no 'accumulate' while the trend pillar is below 45), "
                     "and a concentration cap. They are rule-based outputs on public data and ignore your tax position, time horizon and goals.")
    story += rg.section("Notes, Limits & Disclaimer") + rg.bullets([rg.esc(x) for x in notes_all], rg.AMBER_H)
    story += [Spacer(1, 10), rg.HRFlowable(width="100%", thickness=0.5, color=rg.BORDER, spaceAfter=5),
              rg.P("<b>Disclaimer.</b> This report is generated automatically from public market data and the holdings supplied by the client. It is for educational "
                   "and informational purposes only and does not constitute investment advice, a recommendation, or an offer to buy or sell any security. "
                   "The author is not a SEBI-registered Research Analyst or Investment Adviser. Data may be delayed, incomplete or inaccurate; past performance "
                   "does not guarantee future results. Equity investments are subject to market risk. Consult a SEBI-registered adviser before making investment decisions.", "disc")]

    doc = SimpleDocTemplate(path, pagesize=rg.A4, topMargin=19 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
                            title="Portfolio Health Report", author=BRAND)
    meta = {"sym": "", "name": "", "generated": now, "kind": "Portfolio Report", "right": "Portfolio Health Report"}
    doc.build(story, canvasmaker=rg.make_canvas(meta))
    return path


# ══════════════════════════════════════════════════════════════════════════
# JOB
# ══════════════════════════════════════════════════════════════════════════
def run_job(holdings: List[dict], out_dir: str, client: str = "", progress: Optional[Callable[[str], None]] = None,
            max_analyze: Optional[int] = None, use_ai: bool = True) -> dict:
    """→ {'summary': path, 'stocks': [(sym, path)], 'notes': [...], 'recs': [...]}"""
    say = progress or (lambda m: None)
    os.makedirs(out_dir, exist_ok=True)
    max_analyze = max_analyze or MAX_ANALYZE
    notes: List[str] = []

    say(f"📊 Pricing {len(holdings)} holdings…")
    recs, hist, nifty = _price_all(holdings)
    if not any(r["priced"] for r in recs):
        raise ValueError("None of the symbols returned price data. Please check the NSE ticker symbols (e.g. RELIANCE, HDFCBANK).")

    ranked = sorted([r for r in recs if r["priced"]], key=lambda r: -r["value"])
    to_analyze = ranked[:max_analyze]
    skipped = ranked[max_analyze:]
    if skipped:
        notes.append(f"Full analysis covers the {max_analyze} largest holdings by value; {len(skipped)} smaller holding(s) "
                     f"({', '.join(r['symbol'] for r in skipped[:8])}{'…' if len(skipped) > 8 else ''}) are priced but not scored.")

    stocks: List[Tuple[str, str]] = []
    for i, r in enumerate(to_analyze, start=1):
        say(f"🔬 Analysing {r['symbol']} ({i}/{len(to_analyze)})…")
        try:
            inputs = rg.collect_inputs(r["symbol"])
            R = rg.assemble(inputs, use_ai=use_ai,
                            holding={"qty": r["qty"], "buy": r["buy"], "weight_pct": r["weight_pct"], "max_weight": MAX_WEIGHT})
            r["R"], r["name"], r["sector"] = R, R["name"], (R["sector"] or "Other / unclassified")
            p = os.path.join(out_dir, f"{r['symbol']}_Research_Report.pdf")
            rg.build_pdf(R, p)
            stocks.append((r["symbol"], p))
        except Exception as e:
            r["error"] = str(e)[:120]
            logger.warning(f"[portfolio] analysis failed for {r['symbol']}: {type(e).__name__}")
            notes.append(f"{r['symbol']}: full analysis failed ({r['error']}). It is included in totals but not scored.")

    say("🧮 Building the portfolio summary…")
    pm = portfolio_metrics(recs, hist, nifty)
    sec = sector_weights(recs)
    if not pm.get("ok"):
        notes.append("Portfolio-level risk metrics need at least 60 days of price history; they are omitted.")
    elif pm["covered"] < pm["of"]:
        notes.append(f"Portfolio risk metrics use {pm['covered']} of {pm['of']} priced holdings (the rest lack a full price history).")
    summary = os.path.join(out_dir, f"Portfolio_Health_{datetime.now(IST).strftime('%d%b%Y')}.pdf")
    build_summary_pdf(recs, pm, sec, client, summary, notes)
    return {"summary": summary, "stocks": stocks, "notes": notes, "recs": recs}
