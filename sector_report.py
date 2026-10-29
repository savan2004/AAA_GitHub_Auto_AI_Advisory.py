"""
sector_report.py — 🏭 Index & Sectoral Update Report (PDF)
────────────────────────────────────────────────────────────────────────────────
Modelled on a sell-side sector note: a summary page, numbered exhibits, a
scorecard table, a technical section and an outlook — but built entirely from
live market data, so every statement is traceable to a number in the report.

Layers
  1. NSE index layer      broad indices + sector indices  → performance,
                          relative strength vs Nifty 50, RRG-style rotation
  2. Constituent layer    the project's sector map (nifty500_collector.SECTOR_STOCKS)
                          → breadth (% above 50/200 EMA), median returns,
                          near-52W-high share, Stocks Buzz breakout counts,
                          leaders / laggards

Honest limits (also printed in the PDF)
  • Breadth is measured on the constituent sample in SECTOR_STOCKS, not the
    entire market.
  • Index tickers are Yahoo Finance symbols; any that fail to download are
    simply omitted from the report.
  • "Stance" is a rule-based rotation read, not an analyst rating or target.

Public entrypoint:
    generate_sector_report_pdf() -> (ok, path_or_error, summary_lines)
"""

import os
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether,
)

import report_generator as rg
from config import BRAND_NAME as BRAND
import report_charts as charts
import research_engine as eng
import stocks_buzz
from market_data import bulk_history
from nifty500_collector import SECTOR_STOCKS
from technical_indicators import calc_rsi

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
OUTPUT_DIR = rg.OUTPUT_DIR
CACHE_TTL = 30 * 60
_cache: Dict = {"ts": 0.0, "path": None, "summary": None}
_gen_lock = threading.Lock()


def _grp(sub: str) -> Optional[str]:
    for k in SECTOR_STOCKS:
        if sub.lower() in k.lower():
            return k
    return None


# (display name, Yahoo ticker, constituent-group key or None, kind)
INDICES = [
    ("Nifty 50", "^NSEI", None, "broad"),
    ("Nifty Bank", "^NSEBANK", _grp("Banking"), "broad"),
    ("Nifty Midcap 100", "^CNXMIDCAP", None, "broad"),
    ("Nifty Smallcap 100", "^CNXSC", None, "broad"),
    ("Nifty IT", "^CNXIT", _grp("IT & Software"), "sector"),
    ("Nifty Pharma", "^CNXPHARMA", _grp("Pharma"), "sector"),
    ("Nifty FMCG", "^CNXFMCG", _grp("Consumer & FMCG"), "sector"),
    ("Nifty Auto", "^CNXAUTO", _grp("Automobiles"), "sector"),
    ("Nifty Energy", "^CNXENERGY", _grp("Power & Energy"), "sector"),
    ("Nifty Metal", "^CNXMETAL", None, "sector"),
    ("Nifty Realty", "^CNXREALTY", _grp("Real Estate"), "sector"),
    ("Nifty Infra", "^CNXINFRA", _grp("Industrials"), "sector"),
    ("Nifty Financial Services", "^CNXFIN", None, "sector"),
    ("Nifty PSE", "^CNXPSE", None, "sector"),
    ("Nifty Media", "^CNXMEDIA", None, "sector"),
]
BENCH = "^NSEI"


# ══════════════════════════════════════════════════════════════════════════
# Analytics
# ══════════════════════════════════════════════════════════════════════════
def _ret(c: pd.Series, k: int) -> Optional[float]:
    if len(c) <= k:
        return None
    last, old = float(c.iloc[-1]), float(c.iloc[-1-k])
    if not np.isfinite(last) or not np.isfinite(old) or old <= 0:
        return None
    return round((last / old - 1) * 100, 1)


def _last_completed_session(now=None):
    """Exclude unfinished NSE daily bars; no guessed holiday calendar."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    today = pd.Timestamp(now.date())
    if now.weekday() < 5 and (now.hour, now.minute) >= (16, 0):
        return today.date()
    return (today - pd.offsets.BDay(1)).date()


def _completed_bars(history, now=None):
    """Return only completed candles; never let an intraday Yahoo bar skew PDF."""
    cutoff = _last_completed_session(now)
    out = {}
    for ticker, frame in (history or {}).items():
        if frame is None or getattr(frame, "empty", True):
            continue
        try:
            keep = [pd.Timestamp(i).date() <= cutoff for i in frame.index]
            clean = frame.loc[keep].sort_index()
            if not clean.empty and clean.index.is_unique:
                out[ticker] = clean
        except (ValueError, TypeError, KeyError):
            logger.warning("Sector report: rejected invalid timestamps in %s", ticker)
    return out


def _sector_risk(index, breadth):
    """Transparent market-trend risk, NOT estimated investment risk or probability."""
    p50 = breadth.get("p50") if breadth else None
    vol = index.get("vol20")
    if index.get("trend") == "Strong down" or (p50 is not None and p50 < 20) or (vol is not None and vol >= 40):
        return "HIGH"
    if index.get("trend") == "Downtrend" or (p50 is not None and p50 < 40) or (vol is not None and vol >= 28):
        return "ELEVATED"
    return "MODERATE"


def index_metrics(df: pd.DataFrame, nifty_c: pd.Series) -> dict:
    c = df["Close"].dropna()
    ltp = float(c.iloc[-1])
    ema = lambda sp: float(c.ewm(span=sp, adjust=False).mean().iloc[-1])
    e20, e50 = ema(20), ema(50)
    e200 = ema(200) if len(c) >= 200 else None
    if e200 and ltp > e20 > e50 > e200:
        trend = "Strong up"
    elif ltp > e50 and e20 > e50:
        trend = "Uptrend"
    elif e200 and ltp < e20 < e50 < e200:
        trend = "Strong down"
    elif ltp < e20 < e50:
        trend = "Downtrend"
    else:
        trend = "Sideways"
    # 52-week HIGH means the highest traded High, not the highest daily close.
    highs = df["High"].dropna().iloc[-252:] if "High" in df.columns else pd.Series(dtype=float)
    hi = float(highs.max()) if not highs.empty else None
    daily_returns = c.pct_change(fill_method=None).dropna()
    vol20 = round(float(daily_returns.tail(20).std() * np.sqrt(252) * 100), 1) if len(daily_returns) >= 20 else None
    m = {"ltp": ltp, "chg": _ret(c, 1), "r1w": _ret(c, 5), "r1m": _ret(c, 21), "r3m": _ret(c, 63),
         "r6m": _ret(c, 126), "r1y": _ret(c, min(250, len(c) - 1)) if len(c) > 200 else None,
         "vol20": vol20,
         "from_high": round((ltp / hi - 1) * 100, 1) if hi and hi > 0 else None,
         "rsi": calc_rsi(c) if len(c) > 30 else None,
         "trend": trend, "above50": ltp > e50, "above200": (ltp > e200) if e200 else None,
         "ema50": e50, "ema200": e200}
    pts = (40 if ltp > e50 else 0) + ((30 if e50 > e200 else 0) + (30 if ltp > e200 else 0) if e200 else 30)
    m["trend_pts"] = pts

    # simplified relative-rotation read vs Nifty 50
    m["rs_ratio"] = m["rs_mom"] = None
    m["quad"] = "n/a"
    try:
        j = pd.concat([c, nifty_c], axis=1, join="inner").dropna()
        j.columns = ["i", "n"]
        if len(j) > 70:
            rs = j["i"] / j["n"]
            ratio = 100 * rs / rs.rolling(50).mean()
            current_ratio, prior_ratio = float(ratio.iloc[-1]), float(ratio.iloc[-11])
            if not np.isfinite(current_ratio) or not np.isfinite(prior_ratio):
                raise ValueError("invalid relative-rotation series")
            m["rs_ratio"] = round(current_ratio, 2)
            m["rs_mom"] = round(100 + current_ratio - prior_ratio, 2)
            # Exact benchmark parity is NOT sector leadership.
            if abs(m["rs_ratio"] - 100) <= 0.15 and abs(m["rs_mom"] - 100) <= 0.15:
                m["quad"] = "Neutral"
            else:
                m["quad"] = ("Leading" if m["rs_ratio"] > 100 and m["rs_mom"] > 100 else
                             "Weakening" if m["rs_ratio"] > 100 else
                             "Improving" if m["rs_mom"] > 100 else "Lagging")
            # Benchmark and sector MUST share both endpoints of the same 3M
            # window; sparse holidays and provider gaps can shift raw offsets.
            ir, nr = _ret(j["i"], 63), _ret(j["n"], 63)
            m["rel3m"] = round(ir - nr, 1) if ir is not None and nr is not None else None
    except Exception as e:
        logger.debug(f"rs calc failed: {e}")
    m.setdefault("rel3m", None)
    return m


def stock_metrics(df: pd.DataFrame) -> Optional[dict]:
    c = df["Close"].dropna()
    if len(c) < 60:
        return None
    ltp = float(c.iloc[-1])
    e50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    e200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1]) if len(c) >= 200 else None
    hi = float(df["High"].iloc[-252:].max())
    return {"ltp": ltp, "r1m": _ret(c, 21), "r3m": _ret(c, 63), "above50": ltp > e50,
            "above200": (ltp > e200) if e200 else None, "near_high": ltp >= hi * 0.95}


def _pctile(vals: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Tied metrics MUST receive equal percentiles (average rank)."""
    items = sorted(((k, float(v)) for k, v in vals.items()
                    if v is not None and np.isfinite(float(v))), key=lambda kv: kv[1])
    n = len(items)
    if n <= 1:
        return {k: 50.0 for k, _ in items}
    ranked = {}
    i = 0
    while i < n:
        j = i + 1
        while j < n and items[j][1] == items[i][1]:
            j += 1
        score = (i + j - 1) / (2 * (n - 1)) * 100
        for k, _ in items[i:j]:
            ranked[k] = score
        i = j
    return ranked


def analyze(hist: Dict[str, pd.DataFrame], now=None) -> dict:
    prepared = now or datetime.now(IST)
    hist = _completed_bars(hist, prepared)
    nifty = hist.get(BENCH)
    if nifty is None or len(nifty) < 70:
        raise ValueError("Nifty 50 history unavailable — cannot build the sector report.")
    nc = nifty["Close"]
    asof_date = pd.Timestamp(nifty.index[-1]).date()
    # NSE holidays are NOT inferred from the weekday calendar; a flagged lag
    # needs the exchange calendar checked rather than falsely claiming an outage.
    expected_weekday = (pd.Timestamp(prepared.date()) if prepared.weekday() < 5
                        and prepared.hour >= 16 else
                        pd.Timestamp(prepared.date()) - pd.offsets.BDay(1)).date()
    freshness_warning = (
        f"DATA DATE WARNING: Nifty last recorded close {asof_date:%d-%b-%Y}; "
        f"most recent expected weekday close {expected_weekday:%d-%b-%Y}. "
        "May reflect a market holiday or delayed provider. Verify NSE calendar "
        "and updated candles before relying on levels."
        if asof_date < expected_weekday else None
    )

    idx = {}
    for name, tkr, grp, kind in INDICES:
        df = hist.get(tkr)
        if df is None or len(df) < 70:
            continue
        # Inconsistent constituent/index dates distort relative 3M returns and
        # sector rankings. Omit them rather than silently compare unlike days.
        if pd.Timestamp(df.index[-1]).date() != asof_date:
            logger.warning("Sector report: index %s date differs from Nifty %s", name, asof_date)
            continue
        try:
            m = index_metrics(df, nc)
            m.update(name=name, tkr=tkr, grp=grp, kind=kind)
            idx[name] = m
        except Exception as e:
            logger.warning(f"index {name} failed: {e}")
    if len(idx) < 3:
        raise ValueError("Too few index series downloaded (Yahoo may be throttling). Try again shortly.")
    sector_count = sum(1 for v in idx.values() if v["kind"] == "sector")
    if sector_count < 5:
        raise ValueError("Not enough sector index histories to compare rotation reliably; no sector ranking issued.")

    # ── constituent breadth ────────────────────────────────────────────
    breadth, all_stocks = {}, []
    nc_series = nc
    for grp, syms in SECTOR_STOCKS.items():
        rows = []
        for s in syms:
            df = hist.get(s)
            if df is None:
                continue
            if pd.Timestamp(df.index[-1]).date() != asof_date:
                continue
            sm = stock_metrics(df)
            if not sm:
                continue
            sm["sym"], sm["grp"] = s, grp
            try:
                b = stocks_buzz.analyze_stock(s, df, nc_series)
                sm["buzz"] = b["kind"] if b else None
            except Exception:
                sm["buzz"] = None
            rows.append(sm)
        if len(rows) >= 3:
            def share(key):
                vals = [r[key] for r in rows if r[key] is not None]
                return round(sum(vals) / len(vals) * 100) if vals else None
            r1 = [r["r1m"] for r in rows if r["r1m"] is not None]
            r3 = [r["r3m"] for r in rows if r["r3m"] is not None]
            breadth[grp] = {
                "n": len(rows), "configured": len(set(syms)),
                "coverage": round(100 * len(rows) / len(set(syms))) if syms else None,
                "n200": sum(1 for r in rows if r["above200"] is not None),
                "p50": share("above50"), "p200": share("above200"),
                "near_high": share("near_high"),
                "med1m": round(float(np.median(r1)), 1) if r1 else None,
                "med3m": round(float(np.median(r3)), 1) if r3 else None,
                "breakouts": sum(1 for r in rows if r["buzz"] == "BREAKOUT"),
                "watch": sum(1 for r in rows if r["buzz"] == "WATCH"),
            }
            all_stocks += rows

    # ── rotation score (rule-based) ────────────────────────────────────
    sec = {k: v for k, v in idx.items() if v["kind"] == "sector"}
    p_rel = _pctile({k: v["rel3m"] for k, v in sec.items()})
    p_mom = _pctile({k: v["rs_mom"] for k, v in sec.items()})
    for k, v in sec.items():
        b = breadth.get(v["grp"]) if v["grp"] else None
        parts = [(0.35, p_rel.get(k)), (0.25, p_mom.get(k)), (0.25, v["trend_pts"])]
        if b and b["p50"] is not None:
            parts.append((0.15, b["p50"]))
        wsum = sum(w for w, x in parts if x is not None)
        v["score"] = round(sum(w * x for w, x in parts if x is not None) / wsum) if wsum else None
        v["breadth"] = b
        v["risk"] = _sector_risk(v, b)
        v["stance"] = ("OVERWEIGHT" if (v["score"] or 0) >= 65 else
                       "NEUTRAL" if (v["score"] or 0) >= 40 else "UNDERWEIGHT")

    # overall breadth
    def share_all(key):
        vals = [r[key] for r in all_stocks if r[key] is not None]
        return round(sum(vals) / len(vals) * 100) if vals else None

    count200 = sum(1 for r in all_stocks if r["above200"] is not None)
    leaders = sorted([s for s in all_stocks if s["r1m"] is not None], key=lambda r: -r["r1m"])
    configured_tickers = {s for items in SECTOR_STOCKS.values() for s in items}
    covered_tickers = {r["sym"] for r in all_stocks}
    R = {
        "idx": idx, "sec": sec, "breadth": breadth, "n_stocks": len(all_stocks),
        "n_unique": len(covered_tickers), "n_configured": len(configured_tickers),
        "p50_all": share_all("above50"), "p200_all": share_all("above200"),
        "n200_stocks": count200,
        "gainers": leaders[:8], "losers": leaders[::-1][:8],
        "regime": stocks_buzz.market_regime(nifty),
        "nifty_df": nifty, "hist": hist,
        "generated": datetime.now(IST).strftime("%d-%b-%Y %H:%M IST"),
        "asof": nifty.index[-1].strftime("%d-%b-%Y") if hasattr(nifty.index[-1], "strftime") else "",
        "freshness_warning": freshness_warning,
        "expected_weekday": expected_weekday.isoformat(),
        "index_coverage": (len(idx), len(INDICES)),
        "missing_indices": [n for n, _, _, _ in INDICES if n not in idx],
    }
    R["tech"] = {}
    for name in ("Nifty 50", "Nifty Bank", "Nifty Midcap 100", "Nifty Smallcap 100"):
        m = idx.get(name)
        if not m:
            continue
        try:
            df = hist[m["tkr"]]
            R["tech"][name] = eng.compute_technicals(df, nifty if name != "Nifty 50" else None)
            R["tech"][name]["risk"] = eng.compute_risk(df, nifty if name != "Nifty 50" else None)
        except Exception as e:
            logger.warning(f"technicals {name} failed: {e}")
    R["takeaways"], R["headline"] = _takeaways(R)
    return R


def _takeaways(R: dict) -> Tuple[List[str], str]:
    sec = R["sec"]
    ranked = sorted([v for v in sec.values() if v["score"] is not None], key=lambda v: -v["score"])
    out: List[str] = [R["regime"]["text"] + "."]
    # Do not call a neutral/lagging industry a sector 'leader' merely because
    # its score is third-best in a broadly negative market.
    lead = [v for v in ranked if v["stance"] == "OVERWEIGHT"
            and v["quad"] in ("Leading", "Improving")][:3]
    lag = [v for v in ranked[::-1] if v["stance"] == "UNDERWEIGHT"][:3]
    if lead:
        out.append("<b>Sector leaders:</b> " + ", ".join(
            f"{v['name'].replace('Nifty ', '')} ({_s(v['r1m'])} 1M, {_s(v['r3m'])} 3M)" for v in lead) + ".")
    if lag:
        out.append("<b>Sector laggards:</b> " + ", ".join(
            f"{v['name'].replace('Nifty ', '')} ({_s(v['r1m'])} 1M, {_s(v['r3m'])} 3M)" for v in lag) + ".")
    if R.get("freshness_warning"):
        out.append("<b>Data caution:</b> benchmark prices may not include the most recent completed market session.")
    if R["p50_all"] is not None:
        tone = "healthy" if R["p50_all"] >= 60 else "mixed" if R["p50_all"] >= 40 else "weak"
        out.append(f"<b>Breadth is {tone}:</b> {R['p50_all']}% of {R['n_stocks']} "
                   "sampled sector observations above the 50-day EMA"
                   + (f"; {R['p200_all']}% of {R.get('n200_stocks', R['n_stocks'])} "
                      "with enough history above the 200-day EMA." if R["p200_all"] is not None else "."))
    imp = [v["name"].replace("Nifty ", "") for v in sec.values() if v["quad"] == "Improving"]
    wk = [v["name"].replace("Nifty ", "") for v in sec.values() if v["quad"] == "Weakening"]
    if imp:
        out.append("<b>Improving (relative strength turning up):</b> " + ", ".join(imp) + ".")
    if wk:
        out.append("<b>Weakening (leadership fading):</b> " + ", ".join(wk) + ".")
    bk = sorted(R["breadth"].items(), key=lambda kv: -kv[1]["breakouts"])
    if bk and bk[0][1]["breakouts"] > 0:
        total = sum(b["breakouts"] for _, b in bk)
        out.append(f"<b>Breakout concentration:</b> {total} confirmed breakouts across the tracked stocks, most in "
                   f"{rg.strip_emoji(bk[0][0])} ({bk[0][1]['breakouts']}).")
    if lead:
        leaders = " and ".join(v["name"].replace("Nifty ", "") for v in lead[:2])
        head = leaders + " lead" + (
            " while " + lag[0]["name"].replace("Nifty ", "") + " lags" if lag else "")
    else:
        head = "Sector rotation — no clear leading group"
    return out, head


def _s(v):
    return "n/a" if v is None else f"{v:+.1f}%"


# ══════════════════════════════════════════════════════════════════════════
# Charts
# ══════════════════════════════════════════════════════════════════════════
def _short(n: str) -> str:
    short = n.replace("Nifty ", "").replace("Financial Services", "Fin. Svcs")
    return n if short.strip().isdigit() else short


def chart_returns(R: dict) -> Optional[str]:
    try:
        rows = sorted([v for v in R["idx"].values() if v["r3m"] is not None], key=lambda v: v["r3m"])
        if len(rows) < 3:
            return None
        y = np.arange(len(rows))
        fig, ax = plt.subplots(figsize=(7.4, 0.28 * len(rows) + 1.2))
        h = 0.38
        r1 = [v["r1m"] if v["r1m"] is not None else 0 for v in rows]
        r3 = [v["r3m"] for v in rows]
        ax.barh(y + h / 2, r3, h, color=[charts.GREEN if x >= 0 else charts.RED for x in r3], label="3 months")
        ax.barh(y - h / 2, r1, h, color=[("#7CC6A4" if x >= 0 else "#E59A9A") for x in r1], label="1 month")
        for yi, a, b in zip(y, r3, r1):
            ax.text(a + (0.25 if a >= 0 else -0.25), yi + h / 2, f"{a:+.1f}", va="center", fontsize=6.8,
                    ha="left" if a >= 0 else "right", color=charts.NAVY)
            ax.text(b + (0.25 if b >= 0 else -0.25), yi - h / 2, f"{b:+.1f}", va="center", fontsize=6.4,
                    ha="left" if b >= 0 else "right", color=charts.GREY)
        ax.set_yticks(y)
        ax.set_yticklabels([_short(v["name"]) + ("  ◆" if v["kind"] == "broad" else "") for v in rows], fontsize=8)
        ax.axvline(0, color=charts.GREY, lw=0.6)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.grid(True, axis="x")
        ax.set_title("Index returns — 1 month vs 3 months  (◆ = broad index)", loc="left")
        ax.legend(frameon=False, fontsize=7.5, loc="lower right")
        return charts._save(fig, "sector_returns.png")
    except Exception as e:
        logger.warning(f"returns chart failed: {e}", exc_info=True)
        plt.close("all")
        return None


def chart_rrg(R: dict) -> Optional[str]:
    try:
        pts = [v for v in R["sec"].values() if v["rs_ratio"] is not None and v["rs_mom"] is not None]
        if len(pts) < 3:
            return None
        xs = [v["rs_ratio"] for v in pts]
        ys = [v["rs_mom"] for v in pts]
        px = max(2.0, max(abs(x - 100) for x in xs) * 1.25)
        py = max(1.0, max(abs(y - 100) for y in ys) * 1.25)
        fig, ax = plt.subplots(figsize=(7.4, 4.6))
        ax.axhspan(100, 100 + py, xmin=0.5, xmax=1, color="#E3F4EB")
        ax.axhspan(100 - py, 100, xmin=0.5, xmax=1, color="#FFF4D6")
        ax.axhspan(100 - py, 100, xmin=0, xmax=0.5, color="#FBE3E3")
        ax.axhspan(100, 100 + py, xmin=0, xmax=0.5, color="#E3EEFB")
        ax.axhline(100, color=charts.GREY, lw=0.7)
        ax.axvline(100, color=charts.GREY, lw=0.7)
        cmap = {"Leading": charts.GREEN, "Improving": charts.ACCENT, "Weakening": charts.AMBER,
                "Lagging": charts.RED, "Neutral": charts.GREY}
        for i, v in enumerate(pts):
            ax.scatter(v["rs_ratio"], v["rs_mom"], s=46, color=cmap[v["quad"]], zorder=3, edgecolor="white", linewidth=0.8)
            dy = 0.06 * py * (1 if i % 2 == 0 else -1.6)
            ax.text(v["rs_ratio"] + 0.04 * px, v["rs_mom"] + dy, _short(v["name"]), fontsize=7.2, color=charts.NAVY, zorder=4)
        ax.set_xlim(100 - px, 100 + px)
        ax.set_ylim(100 - py, 100 + py)
        for (tx, ty, t, c, ha, va) in [(100 + px * 0.97, 100 + py * 0.94, "LEADING", charts.GREEN, "right", "top"),
                                       (100 + px * 0.97, 100 - py * 0.94, "WEAKENING", charts.AMBER, "right", "bottom"),
                                       (100 - px * 0.97, 100 - py * 0.94, "LAGGING", charts.RED, "left", "bottom"),
                                       (100 - px * 0.97, 100 + py * 0.94, "IMPROVING", charts.ACCENT, "left", "top")]:
            ax.text(tx, ty, t, color=c, fontsize=8.5, fontweight="bold", ha=ha, va=va, alpha=0.85)
        ax.set_xlabel("Relative strength vs Nifty 50  (>100 = outperforming trend)")
        ax.set_ylabel("RS momentum  (>100 = improving)")
        ax.set_title("Sector rotation map (simplified relative-rotation view)", loc="left")
        return charts._save(fig, "sector_rrg.png")
    except Exception as e:
        logger.warning(f"rrg chart failed: {e}", exc_info=True)
        plt.close("all")
        return None


def chart_breadth(R: dict) -> Optional[str]:
    try:
        rows = [(rg.strip_emoji(k), b) for k, b in R["breadth"].items() if b["p50"] is not None]
        if len(rows) < 3:
            return None
        rows.sort(key=lambda kv: kv[1]["p50"])
        y = np.arange(len(rows))
        h = 0.38
        fig, ax = plt.subplots(figsize=(7.4, 0.3 * len(rows) + 1.2))
        ax.barh(y + h / 2, [b["p50"] for _, b in rows], h, color=charts.NAVY, label="% above 50-day EMA")
        ax.barh(y - h / 2, [b["p200"] or 0 for _, b in rows], h, color=charts.ACCENT, label="% above 200-day EMA")
        ax.set_yticks(y)
        ax.set_yticklabels([n[:30] for n, _ in rows], fontsize=7.8)
        ax.set_xlim(0, 100)
        ax.axvline(50, color=charts.GREY, lw=0.6, ls=":")
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.grid(True, axis="x")
        ax.legend(frameon=False, fontsize=7.5, loc="lower right")
        ax.set_title("Sector breadth — share of constituents in an uptrend", loc="left")
        return charts._save(fig, "sector_breadth.png")
    except Exception as e:
        logger.warning(f"breadth chart failed: {e}", exc_info=True)
        plt.close("all")
        return None


# ══════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════
def _ps(name, **kw):
    base = dict(fontName=rg.FONT, fontSize=7.4, leading=9.6, textColor=rg.INK)
    base.update(kw)
    return ParagraphStyle(name, **base)


_T = {
    "c": _ps("c"), "cb": _ps("cb", fontName=rg.BOLD),
    "r": _ps("r", alignment=TA_RIGHT), "rb": _ps("rb", fontName=rg.BOLD, alignment=TA_RIGHT),
    "ctr": _ps("ctr", alignment=TA_CENTER), "ctrb": _ps("ctrb", fontName=rg.BOLD, alignment=TA_CENTER),
    "h": _ps("h", fontName=rg.BOLD, textColor=colors.white, fontSize=7.2),
    "hr": _ps("hr", fontName=rg.BOLD, textColor=colors.white, fontSize=7.2, alignment=TA_RIGHT),
    "hc": _ps("hc", fontName=rg.BOLD, textColor=colors.white, fontSize=7.2, alignment=TA_CENTER),
}


def Tp(text, style="c"):
    return Paragraph(rg._rs(text), _T[style])


def heat(v: Optional[float], scale: float = 8.0):
    """Red ↔ white ↔ green background for return cells."""
    if v is None:
        return colors.white
    t = max(-1.0, min(1.0, v / scale))
    if t >= 0:
        return colors.Color(1 - 0.55 * t, 1 - 0.12 * t, 1 - 0.45 * t)
    t = -t
    return colors.Color(1 - 0.06 * t, 1 - 0.55 * t, 1 - 0.55 * t)


def _stance_hex(s):
    return rg.GREEN_H if s == "OVERWEIGHT" else rg.AMBER_H if s == "NEUTRAL" else rg.RED_H


def _quad_hex(q):
    return {"Leading": rg.GREEN_H, "Improving": "#1E88E5", "Weakening": rg.AMBER_H, "Lagging": rg.RED_H, "Neutral": rg.GREY_H}.get(q, rg.GREY_H)


def _pct(v, nd=1):
    return "—" if v is None else f"{v:+.{nd}f}%"


def _cover(R, story):
    W = rg.W
    banner = Table([
        [rg.P("Index &amp; Sectoral Update", "title"), rg.P(esc_d(R["asof"]), "px")],
        [rg.P(rg.esc(R["headline"]) + " — " + ("risk-on tape" if R["regime"]["supportive"] else
                                              "selective tape" if R["regime"]["supportive"] is None else "defensive tape"), "sub"),
         rg.P("Market data as of last close", "pxs")],
    ], colWidths=[W * 0.70, W * 0.30])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), rg.NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("TOPPADDING", (0, 0), (-1, 0), 13), ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
                                ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 13)]))
    story += [banner, Spacer(1, 3),
              rg.P(f"Prepared by: <b>{rg.esc(BRAND)} automated research engine</b> · {R['generated']}", "small"),
              Spacer(1, 4)]
    if R.get("freshness_warning"):
        story.append(rg.P(rg.colored(rg.esc(R["freshness_warning"]), rg.RED_H), "small"))
        story.append(Spacer(1, 4))
    loaded, total = R.get("index_coverage", (0, len(INDICES)))
    tracked = R.get("n_unique", R["n_stocks"])
    configured = R.get("n_configured") or tracked
    pct = round(100 * tracked / configured) if configured else 0
    story += [rg.P(f"<b>Data quality:</b> indices {loaded}/{total} matched the Nifty session; "
                   f"stock breadth: {tracked}/{configured} configured unique stocks ({pct}%), "
                   "not the complete NSE sector universe.", "small"),
              Spacer(1, 8), rg.P("Summary", "h1"),
              Table([[""]], colWidths=[W], rowHeights=[1], style=[("LINEABOVE", (0, 0), (-1, 0), 1.2, rg.NAVY)]),
              Spacer(1, 4)]
    story += rg.bullets(R["takeaways"], rg.NAVY_H)
    story.append(Spacer(1, 8))

    # scorecard (the sample's "Rating and Target Price" table, as a rotation scorecard)
    story.append(rg.P("Sector rotation scorecard", "h2"))
    rows = sorted([v for v in R["sec"].values() if v["score"] is not None], key=lambda v: -v["score"])
    hdr = [Tp("Sector index", "h"), Tp("Stance", "hc"), Tp("Score", "hr"), Tp("1M", "hr"), Tp("3M", "hr"),
           Tp("vs Nifty 3M", "hr"), Tp("Rotation", "hc"), Tp("%>50EMA", "hr"), Tp("Risk", "hc")]
    data, extra = [hdr], []
    for i, v in enumerate(rows, start=1):
        b = v["breadth"]
        data.append([Tp(_short(v["name"]), "cb"),
                     Tp(rg.colored(v["stance"], _stance_hex(v["stance"])), "ctr"),
                     Tp(str(v["score"]), "rb"), Tp(_pct(v["r1m"]), "r"), Tp(_pct(v["r3m"]), "r"),
                     Tp(_pct(v["rel3m"]), "r"), Tp(rg.colored(v["quad"], _quad_hex(v["quad"])), "ctr"),
                     Tp(f"{b['p50']}%" if b and b["p50"] is not None else "—", "r"),
                     Tp(rg.colored(v.get("risk", "N/A"), rg.RED_H if v.get("risk")=="HIGH" else
                                   rg.AMBER_H if v.get("risk")=="ELEVATED" else rg.GREEN_H), "ctr")])
        extra += [("BACKGROUND", (3, i), (3, i), heat(v["r1m"], 6)), ("BACKGROUND", (4, i), (4, i), heat(v["r3m"], 12)),
                  ("BACKGROUND", (5, i), (5, i), heat(v["rel3m"], 8))]
    widths = [36 * mm, 25 * mm, 12 * mm, 16 * mm, 16 * mm, 19 * mm, 21 * mm, 17 * mm, 16 * mm]
    story.append(rg.table(data, widths, extra=extra + [("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(rg.P("Stance = relative rotation ranking, NOT a portfolio weight or investment recommendation. "
                      "Risk = trend, last-20-session annualised volatility and sampled 50EMA breadth "
                      "(HIGH / ELEVATED / MODERATE, not a probability). "
                      "Sector score weighs relative 3M return 35%, RS momentum 25%, trend 25% and available breadth 15%.", "tiny"))
    # Plain-language decision snapshot uses data from the same exact session.
    ranked = sorted(rows, key=lambda v: -v["score"])
    leaders = [v for v in ranked if v["stance"] == "OVERWEIGHT"
               and v["quad"] in ("Leading", "Improving")][:2]
    weak = [v for v in ranked[::-1] if v["stance"] == "UNDERWEIGHT"][:2]
    narrative = []
    for v in leaders:
        narrative.append(
            f"<b>WATCH LEADER — {rg.esc(v['name'])}:</b> strength {v['score']}/100; "
            f"market-risk flag {v['risk']}; 3M relative return {_pct(v['rel3m'])}. "
            "Favourable rotation alone is not a buy signal.")
    for v in weak:
        narrative.append(
            f"<b>CAUTION — {rg.esc(v['name'])}:</b> score {v['score']}/100, "
            f"risk {v['risk']}, 3M relative return {_pct(v['rel3m'])}. "
            "Avoid chasing a bounce without trend confirmation.")
    if narrative:
        story.append(Spacer(1, 9))
        story.append(rg.P("Client decision snapshot", "h2"))
        story += rg.bullets(narrative, rg.NAVY_H)


def esc_d(s):
    return rg.esc(s)


def _exhibit_index_table(R, story):
    story += rg.section("Exhibit 1 — Index performance", "Price returns, trend and distance from the 52-week high")
    hdr = [Tp("Index", "h"), Tp("Last", "hr"), Tp("1D", "hr"), Tp("1W", "hr"), Tp("1M", "hr"), Tp("3M", "hr"),
           Tp("6M", "hr"), Tp("1Y", "hr"), Tp("vs 52W high", "hr"), Tp("RSI", "hr"), Tp("Trend", "hc")]
    data, extra = [hdr], []
    ordered = [v for v in R["idx"].values() if v["kind"] == "broad"] + sorted(
        [v for v in R["idx"].values() if v["kind"] == "sector"], key=lambda v: -(v["r3m"] or -999))
    for i, v in enumerate(ordered, start=1):
        tcol = rg.GREEN_H if "up" in v["trend"].lower() else rg.RED_H if "down" in v["trend"].lower() else rg.AMBER_H
        data.append([Tp(v["name"], "cb"), Tp(f"{v['ltp']:,.0f}", "rb"), Tp(_pct(v["chg"]), "r"), Tp(_pct(v["r1w"]), "r"),
                     Tp(_pct(v["r1m"]), "r"), Tp(_pct(v["r3m"]), "r"), Tp(_pct(v["r6m"]), "r"), Tp(_pct(v["r1y"]), "r"),
                     Tp(_pct(v["from_high"]), "r"), Tp(f"{v['rsi']:.0f}" if v["rsi"] else "—", "r"),
                     Tp(rg.colored(v["trend"], tcol), "ctr")])
        for col, key, sc in ((2, "chg", 2), (3, "r1w", 4), (4, "r1m", 6), (5, "r3m", 12), (6, "r6m", 18), (7, "r1y", 30)):
            extra.append(("BACKGROUND", (col, i), (col, i), heat(v[key], sc)))
    widths = [33 * mm, 17 * mm, 12 * mm, 12 * mm, 13 * mm, 13 * mm, 13 * mm, 13 * mm, 17 * mm, 10 * mm, 25 * mm]
    n_broad = sum(1 for v in ordered if v["kind"] == "broad")
    extra += [("LINEBELOW", (0, n_broad), (-1, n_broad), 1.0, rg.NAVY),
              ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    story.append(rg.table(data, widths, zebra=False, extra=extra))
    story.append(rg.P(f"Source: Yahoo Finance daily closes; {BRAND} calculations. Broad indices listed first, then sectors ranked by 3-month return.", "tiny"))


def _with_chart(story, title, sub, path):
    """Heading + chart as one unbreakable block (no orphaned headings)."""
    head = rg.section(title, sub)
    im = rg.img(path, rg.W) if path else None
    if im:
        story.append(KeepTogether(head + [im]))
        return True
    return False


def _exhibit_charts(R, story):
    ch = R["charts"]
    _with_chart(story, "Exhibit 2 — Who is leading", "One-month and three-month index returns", ch.get("returns"))
    if _with_chart(story, "Exhibit 3 — Sector rotation map",
                   "Where each sector sits relative to the Nifty 50 and which way it is moving", ch.get("rrg")):
        by = {}
        for v in R["sec"].values():
            by.setdefault(v["quad"], []).append(_short(v["name"]))
        lines = []
        for q, txt in (("Leading", "outperforming and gaining strength"), ("Improving", "still behind but turning up — early rotation candidates"),
                       ("Weakening", "still ahead but momentum fading — watch for rollover"), ("Lagging", "underperforming and weakening")):
            if by.get(q):
                lines.append(f"<b>{rg.colored(q, _quad_hex(q))}</b> — {txt}: {', '.join(by[q])}.")
        story += [Spacer(1, 4)] + rg.bullets(lines)
        story.append(rg.P("RS = index ÷ Nifty 50, normalised to its 50-day average; momentum = change in that ratio over 10 sessions. A simplified version of the relative-rotation method.", "tiny"))


def _exhibit_breadth(R, story):
    if not R["breadth"]:
        return
    if not _with_chart(story, "Exhibit 4 — Sector breadth & breakouts",
                       f"Measured on {R['n_stocks']} tracked constituents (not the whole market)", R["charts"].get("breadth")):
        story += rg.section("Exhibit 4 — Sector breadth & breakouts", f"Measured on {R['n_stocks']} tracked constituents")
    story.append(Spacer(1, 5))
    hdr = [Tp("Constituent group", "h"), Tp("Valid/map", "hr"), Tp("% > 50EMA", "hr"), Tp("% > 200EMA", "hr"), Tp("Near 52W high", "hr"),
           Tp("Median 1M", "hr"), Tp("Median 3M", "hr"), Tp("Breakouts", "hr"), Tp("Watch", "hr")]
    data, extra = [hdr], []
    for i, (k, b) in enumerate(sorted(R["breadth"].items(), key=lambda kv: -(kv[1]["p50"] or 0)), start=1):
        data.append([Tp(rg.strip_emoji(k), "cb"), Tp(f"{b['n']}/{b.get('configured', b['n'])}", "r"), Tp(f"{b['p50']}%" if b["p50"] is not None else "—", "rb"),
                     Tp(f"{b['p200']}%" if b["p200"] is not None else "—", "r"), Tp(f"{b['near_high']}%" if b["near_high"] is not None else "—", "r"),
                     Tp(_pct(b["med1m"]), "r"), Tp(_pct(b["med3m"]), "r"), Tp(str(b["breakouts"]), "rb"), Tp(str(b["watch"]), "r")])
        extra += [("BACKGROUND", (2, i), (2, i), heat((b["p50"] or 50) - 50, 40)),
                  ("BACKGROUND", (5, i), (5, i), heat(b["med1m"], 6)), ("BACKGROUND", (6, i), (6, i), heat(b["med3m"], 12))]
    widths = [44 * mm, 13 * mm, 17 * mm, 18 * mm, 20 * mm, 17 * mm, 17 * mm, 20 * mm, 12 * mm]
    story.append(rg.table(data, widths, zebra=False, extra=extra + [("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(rg.P("Valid/map = aligned, usable stock price histories / configured sample tickers, "
                      "NOT a full NSE constituent list. 200EMA percent denominator excludes companies "
                      "without 200 completed sessions. Breakouts/watch use Stocks Buzz criteria.", "tiny"))


def _exhibit_movers(R, story):
    """Two readable full-width tables, no two mini-tables colliding in A4."""
    if not R["gainers"]:
        return
    story += rg.section("Exhibit 5 — Leaders and laggards (1 month)",
                        "Best and worst performers among tracked constituents")
    for title, rows, good in (
        ("Top gainers", R["gainers"], True),
        ("Top decliners", R["losers"], False),
    ):
        story.append(rg.P(title, "h2"))
        hdr = [Tp("Stock", "h"), Tp("Constituent group", "h"), Tp("Close", "hr"),
               Tp("1M", "hr"), Tp("3M", "hr")]
        data = [hdr]
        for row in rows:
            data.append([
                Tp(rg.esc(row["sym"]), "cb"),
                Tp(rg.esc(rg.strip_emoji(row["grp"])), "c"),
                Tp(f"₹{row['ltp']:,.1f}", "r"),
                Tp(rg.colored(_pct(row["r1m"]), rg.GREEN_H if good else rg.RED_H), "r"),
                Tp(_pct(row["r3m"]), "r"),
            ])
        # 173 mm < 178 mm content width: does not overlap the adjacent column.
        story.append(rg.table(
            data, [22 * mm, 65 * mm, 28 * mm, 29 * mm, 29 * mm],
            extra=[("LEFTPADDING", (0, 0), (-1, -1), 5),
                   ("RIGHTPADDING", (0, 0), (-1, -1), 5)],
        ))
        story.append(Spacer(1, 7))


def _nifty_technical(R, story):
    t = R["tech"].get("Nifty 50")
    if not t:
        return
    if not _with_chart(story, "Index Technical View — Nifty 50", "Trend, momentum and the levels that matter next", R["charts"].get("nifty")):
        story += rg.section("Index Technical View — Nifty 50", "Trend, momentum and the levels that matter next")
    story.append(Spacer(1, 5))
    rk = t.get("risk") or {}
    story.append(rg.kv([
        ["Daily trend", t["trend"].title(), "Weekly trend", (t.get("weekly_trend") or "—").title()],
        ["RSI (14)", str(t["rsi"]), "ADX (+DI / −DI)", f"{t['adx']} ({t['plus_di']} / {t['minus_di']})"],
        ["EMA 20 / 50 / 200", f"{t['ema20']:,.0f} / {t['ema50']:,.0f} / {rg.n(t.get('ema200'), 0)}", "52W range", f"{t['w52l']:,.0f} – {t['w52h']:,.0f}"],
        ["Realised volatility (ann.)", rg.n(rk.get("ann_vol"), 1, "%"), "Max drawdown (1Y)", rg.n(rk.get("max_dd"), 1, "%")],
    ]))
    sup, res = t["supports"], t["resistances"]
    hdr = [rg.P("Level", "th"), rg.P("Price", "thr"), rg.P("Distance", "thr"), rg.P("Basis", "th")]
    rows = []
    for r in reversed(res):
        rows.append([rg.P(rg.colored("Resistance", rg.RED_H), "cell"), rg.P(f"{r['price']:,.0f}", "cellrb"), rg.P(f"{r['dist']:+.1f}%", "cellr"), rg.P(rg.esc(r["src"]), "cell")])
    rows.append([rg.P("<b>Nifty 50 last</b>", "cell"), rg.P(f"{t['ltp']:,.0f}", "cellrb"), rg.P("—", "cellr"), rg.P("Last close", "cell")])
    for s_ in sup:
        rows.append([rg.P(rg.colored("Support", rg.GREEN_H), "cell"), rg.P(f"{s_['price']:,.0f}", "cellrb"), rg.P(f"{s_['dist']:+.1f}%", "cellr"), rg.P(rg.esc(s_["src"]), "cell")])
    story += [Spacer(1, 6), KeepTogether([rg.P("Key levels", "h2"), rg.table([hdr] + rows, [30 * mm, 34 * mm, 30 * mm, 84 * mm])])]

    sig = (rk.get("ann_vol") or 0) / 100 * (21 / 252) ** 0.5
    bull = res[0]["price"] if res else t["w52h"]
    bear = sup[0]["price"] if sup else t["ema50"]
    lines = [f"<b>Bullish confirmation:</b> daily close above {bull:,.0f} keeps the up-move intact."
             f" <b>Bearish trigger:</b> daily close below {bear:,.0f} opens the next support."]
    if sig:
        lines.append(f"<b>One-month volatility range (±1σ):</b> {t['ltp'] * (1 - sig):,.0f} – {t['ltp'] * (1 + sig):,.0f}. A statistical range, not a forecast.")
    story += [Spacer(1, 5)] + rg.bullets(lines)

    # other broad indices
    others = [(n, tt) for n, tt in R["tech"].items() if n != "Nifty 50"]
    if others:
        hdr = [Tp("Index", "h"), Tp("Last", "hr"), Tp("Trend", "hc"), Tp("RSI", "hr"), Tp("EMA 50", "hr"), Tp("EMA 200", "hr"),
               Tp("Next support", "hr"), Tp("Next resistance", "hr")]
        data = [hdr]
        for n_, tt in others:
            data.append([Tp(n_, "cb"), Tp(f"{tt['ltp']:,.0f}", "rb"), Tp(tt["trend"].title(), "ctr"), Tp(str(tt["rsi"]), "r"),
                         Tp(f"{tt['ema50']:,.0f}", "r"), Tp(rg.n(tt.get("ema200"), 0), "r"),
                         Tp(f"{tt['supports'][0]['price']:,.0f}" if tt["supports"] else "—", "r"),
                         Tp(f"{tt['resistances'][0]['price']:,.0f}" if tt["resistances"] else "—", "r")])
        story += [Spacer(1, 6), rg.P("Other broad indices — technical snapshot", "h2"),
                  rg.table(data, [34 * mm, 18 * mm, 24 * mm, 12 * mm, 20 * mm, 20 * mm, 25 * mm, 25 * mm],
                           extra=[("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)])]


def _outlook(R, story):
    story += rg.section("Outlook & Positioning Ideas", "Rule-based rotation read — what the data favours and what it does not")
    ranked = sorted([v for v in R["sec"].values() if v["score"] is not None], key=lambda v: -v["score"])
    ow = [v for v in ranked if v["stance"] == "OVERWEIGHT"]
    uw = [v for v in ranked if v["stance"] == "UNDERWEIGHT"]

    def ev(v):
        b = v["breadth"]
        bits = [f"risk {v.get('risk','not rated').lower()}", f"{_pct(v['rel3m'])} vs Nifty (3M)",
                f"{v['quad'].lower()} on rotation map", v["trend"].lower()]
        if b and b["p50"] is not None:
            bits.append(f"{b['p50']}% of constituents above 50EMA")
        return ", ".join(bits)
    left = [rg.P(rg.colored("FAVOURED (OVERWEIGHT)", rg.GREEN_H), "h2")]
    left += rg.bullets([f"<b>{rg.esc(_short(v['name']))}</b> (score {v['score']}): {ev(v)}" for v in ow], rg.GREEN_H) or \
        [rg.P("No sector clears the overweight bar today.", "small")]
    right = [rg.P(rg.colored("AVOID / UNDERWEIGHT", rg.RED_H), "h2")]
    right += rg.bullets([f"<b>{rg.esc(_short(v['name']))}</b> (score {v['score']}): {ev(v)}" for v in uw], rg.RED_H) or \
        [rg.P("No sector is flagged underweight today.", "small")]
    box = Table([[left, right]], colWidths=[rg.W / 2, rg.W / 2])
    box.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, rg.BORDER),
                             ("LINEAFTER", (0, 0), (0, -1), 0.5, rg.BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(box)

    notes = []
    imp = [v for v in ranked if v["quad"] == "Improving"]
    if imp:
        notes.append("<b>Watch for rotation into:</b> " + ", ".join(_short(v["name"]) for v in imp) +
                     " — relative strength is below trend but momentum has turned up; confirmation needs a move into the Leading quadrant.")
    wk = [v for v in ranked if v["quad"] == "Weakening"]
    if wk:
        notes.append("<b>Trim into strength:</b> " + ", ".join(_short(v["name"]) for v in wk) +
                     " — still ahead of the market but momentum is fading.")
    if R["regime"]["supportive"] is False:
        notes.append("<b>Regime caution:</b> the Nifty is below its 50-day EMA, so even favoured sectors carry higher failure risk — use smaller position sizes.")
    elif R["regime"]["supportive"]:
        notes.append("<b>Regime tailwind:</b> the Nifty is above its 50 and 200-day EMAs, which historically supports trend-following in the leading sectors.")
    story += [Spacer(1, 6)] + rg.bullets(notes)

    story += [Spacer(1, 8), rg.P("Methodology & data notes", "h2")] + rg.bullets([
        f"Index series: {len(R['idx'])}/{len(INDICES)} configured NSE index tickers matched the "
        "Nifty closing session. Missing or date-mismatched series are omitted: "
        + (", ".join(R.get("missing_indices") or []) or "none") + ".",
        f"Constituents: {R.get('n_unique', R['n_stocks'])}/{R.get('n_configured', 'unknown')} "
        "configured unique stocks, grouped into {0} stock observations across sector samples; "
        "breadth and breakouts describe this sample only, NOT full NSE sector membership.".format(R["n_stocks"]),
        "Stance = weighted percentile of 3-month relative return, relative-strength momentum, trend and available sampled breadth. "
        "Missing breadth is excluded and remaining weights renormalised; scores are not directly comparable to complete-coverage scores.",
        "Risk flag = 20-session annualised volatility, trend and sampled 50EMA breadth. It is NOT a recommended position size, price target or forecast.",
        "The rotation quadrant reflects the last ~10 weeks against each sector's 50-day relative-strength average, so a sector can sit in "
        "“Leading” while its 3-month return still ranks low (or the reverse). The stance score blends both views.",
        "Sector indices and constituent groups do not always match one-for-one (e.g. Nifty Metal, PSE, Media and Financial Services have no mapped group), so breadth columns show “—” for them.",
    ])
    story += [Spacer(1, 8), rg.HRFlowable(width="100%", thickness=0.5, color=rg.BORDER, spaceAfter=5),
              rg.P("<b>Disclaimer.</b> This report is generated automatically from public market data. It is for educational and informational purposes only and does not "
                   "constitute investment advice, a recommendation, or an offer to buy or sell any security. The author is not a SEBI-registered Research Analyst or "
                   "Investment Adviser. Data may be delayed, incomplete or inaccurate; past performance and technical signals do not guarantee future results. "
                   "Equity investments are subject to market risk. Consult a SEBI-registered adviser before making investment decisions.", "disc")]


def build_pdf(R: dict, path: str) -> str:
    story: list = []
    _cover(R, story)
    story.append(PageBreak())
    _exhibit_index_table(R, story)
    _exhibit_charts(R, story)
    _exhibit_breadth(R, story)
    _exhibit_movers(R, story)
    _nifty_technical(R, story)
    _outlook(R, story)
    doc = SimpleDocTemplate(path, pagesize=rg.A4, topMargin=19 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
                            title="Index & Sectoral Update", author=BRAND)
    meta = {"brand": "MSK Bot", "sym": "", "name": "", "generated": R["generated"], "kind": "Sector Report", "right": f"Index & Sectoral Update  ·  {R['asof']}"}
    doc.build(story, canvasmaker=rg.make_canvas(meta))
    return path


# ══════════════════════════════════════════════════════════════════════════
# Orchestration + public entrypoint
# ══════════════════════════════════════════════════════════════════════════
def collect(progress_cb=None) -> Dict[str, pd.DataFrame]:
    syms = [t for _, t, _, _ in INDICES]
    for lst in SECTOR_STOCKS.values():
        syms += [s.upper() for s in lst]
    return bulk_history(syms, "1y", chunk_size=100, progress_cb=progress_cb, cache_ttl=CACHE_TTL)


def assemble(hist: Dict[str, pd.DataFrame]) -> dict:
    R = analyze(hist)
    nifty_t = R["tech"].get("Nifty 50")
    R["charts"] = {
        "returns": chart_returns(R), "rrg": chart_rrg(R), "breadth": chart_breadth(R),
        "nifty": charts.chart_technical(R["nifty_df"], "NIFTY 50", nifty_t, unit="") if nifty_t else None,
    }
    return R


def summary_lines(R: dict) -> List[str]:
    """Plain-text version of the takeaways for the Telegram caption."""
    import re
    return [re.sub(r"<[^>]+>", "", x).replace("&amp;", "&") for x in R["takeaways"]]


def _prune_old_files(max_age_s: int = 2 * 3600):
    try:
        for f in os.listdir(OUTPUT_DIR):
            fp = os.path.join(OUTPUT_DIR, f)
            if f.startswith("Index_Sectoral_Update_") and time.time() - os.path.getmtime(fp) > max_age_s:
                os.remove(fp)
    except Exception:
        pass


def generate_sector_report_pdf(progress_cb=None) -> Tuple[bool, str, List[str]]:
    with _gen_lock:                       # one build at a time; repeats within 30 min reuse the file
        if _cache["path"] and os.path.exists(_cache["path"]) and time.time() - _cache["ts"] < CACHE_TTL:
            return True, _cache["path"], _cache["summary"]
        _prune_old_files()
        try:
            hist = collect(progress_cb)
            R = assemble(hist)
            out = os.path.join(OUTPUT_DIR, f"Index_Sectoral_Update_{datetime.now(IST).strftime('%Y%m%d_%H%M')}.pdf")
            build_pdf(R, out)
            summ = summary_lines(R)
            _cache.update(ts=time.time(), path=out, summary=summ)
            return True, out, summ
        except ValueError as e:
            return False, str(e), []
        except Exception as e:
            logger.error(f"[SectorReport] failed: {e}", exc_info=True)
            return False, f"Sector report failed: {e}", []
