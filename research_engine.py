"""
research_engine.py — Analytics core of the institutional research report
────────────────────────────────────────────────────────────────────────────────
Pure computation. No network calls, no PDF code. Takes already-fetched data and
returns a structured "research dict" that report_generator renders.

Pieces
  compute_technicals()   multi-timeframe trend, returns vs Nifty, levels, volume
  compute_risk()         volatility, drawdown, VaR, beta
  compute_fundamentals() growth, margins, cash conversion, leverage, valuation
  compute_scores()       six-pillar scorecard → composite → stance
  build_trade_plan()     entry / stop / targets / triggers / sizing example
  build_flags()          evidence-backed strengths and risks
  deterministic_narrative()  rule-written analyst commentary (always available)
  ai_analyst_note()      optional LLM narrative, NUMERICALLY GROUNDED: if it
                         states a figure that is not in the supplied facts, the
                         whole note is discarded.

The scoring is a transparent rule set (documented in the PDF appendix). It is a
structured way to summarise the data — not a forecast and not a backtested edge.
"""

import re
import math
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from technical_indicators import (
    calc_rsi, calc_macd, calc_atr, calc_adx, calc_bollinger,
)
from research_data import series_cagr

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════
def _f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _median(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)) and x == x)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _band(x, bands, default=None):
    """bands = [(threshold, score), ...] checked in order with x >= threshold."""
    if x is None:
        return default
    for thr, sc in bands:
        if x >= thr:
            return sc
    return bands[-1][1]


def _avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def is_financial_sector(sector: Optional[str]) -> bool:
    s = (sector or "").lower()
    return "banking" in s or "financ" in s


# ══════════════════════════════════════════════════════════════════════════
# TECHNICALS
# ══════════════════════════════════════════════════════════════════════════
def _swing_levels(h: pd.Series, l: pd.Series, window: int = 5, lookback: int = 180):
    hh, ll = h.iloc[-lookback:], l.iloc[-lookback:]
    highs, lows = [], []
    for i in range(window, len(hh) - window):
        if hh.iloc[i] == hh.iloc[i - window:i + window + 1].max():
            highs.append(float(hh.iloc[i]))
        if ll.iloc[i] == ll.iloc[i - window:i + window + 1].min():
            lows.append(float(ll.iloc[i]))
    return highs, lows


def _cluster(levels: List[float], tol: float = 0.012) -> List[float]:
    out: List[List[float]] = []
    for lv in sorted(levels):
        if out and abs(lv / (sum(out[-1]) / len(out[-1])) - 1) <= tol:
            out[-1].append(lv)
        else:
            out.append([lv])
    return [sum(g) / len(g) for g in out]


def compute_technicals(df: pd.DataFrame, nifty: Optional[pd.DataFrame]) -> dict:
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    n = len(c)
    ltp, prev = float(c.iloc[-1]), float(c.iloc[-2])
    t: dict = {"ltp": round(ltp, 2), "chg": round((ltp / prev - 1) * 100, 2) if prev else 0.0,
               "day_hi": _f(h.iloc[-1]), "day_lo": _f(l.iloc[-1]), "bars": n}

    # ── returns vs Nifty ────────────────────────────────────────────────
    nc = nifty["Close"] if nifty is not None and not nifty.empty else None
    rets = {}
    for lab, k in (("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 250)):
        kk = min(k, n - 1)
        if k == 250 and n < 200:
            continue
        if kk < 3 or (k > 5 and n - 1 < k * 0.8):
            continue
        s_ret = (ltp / float(c.iloc[-1 - kk]) - 1) * 100
        n_ret = None
        if nc is not None and len(nc) > kk:
            n_ret = (float(nc.iloc[-1]) / float(nc.iloc[-1 - kk]) - 1) * 100
        rets[lab] = {"stock": round(s_ret, 1),
                     "nifty": round(n_ret, 1) if n_ret is not None else None,
                     "rel": round(s_ret - n_ret, 1) if n_ret is not None else None}
    t["returns"] = rets

    # ── trend structure ─────────────────────────────────────────────────
    ema = lambda s, sp: s.ewm(span=sp, adjust=False).mean()
    e20, e50 = float(ema(c, 20).iloc[-1]), float(ema(c, 50).iloc[-1])
    e200 = float(ema(c, 200).iloc[-1]) if n >= 200 else None
    t.update(ema20=round(e20, 2), ema50=round(e50, 2), ema200=_f(e200))
    if e200 and ltp > e20 > e50 > e200:
        trend = "STRONG UPTREND"
    elif ltp > e50 and e20 > e50:
        trend = "UPTREND"
    elif e200 and ltp < e20 < e50 < e200:
        trend = "STRONG DOWNTREND"
    elif ltp < e20 < e50:
        trend = "DOWNTREND"
    else:
        trend = "SIDEWAYS / TRANSITION"
    t["trend"] = trend

    # ── oscillators ─────────────────────────────────────────────────────
    t["rsi"] = calc_rsi(c)
    m, ms, mh = calc_macd(c)
    t.update(macd=m, macd_sig=ms, macd_hist=mh)
    adx, pdi, mdi = calc_adx(df)
    t.update(adx=adx, plus_di=pdi, minus_di=mdi)
    atr = calc_atr(df)
    t.update(atr=atr, atr_pct=round(atr / ltp * 100, 2) if ltp else None)
    bm, bu, bl = calc_bollinger(c)
    t.update(bb_mid=bm, bb_up=bu, bb_lo=bl,
             pct_b=round((ltp - bl) / (bu - bl) * 100, 0) if bu > bl else None,
             bandwidth=round((bu - bl) / bm * 100, 1) if bm else None)

    # ── weekly timeframe ────────────────────────────────────────────────
    wk_trend, wk_rsi = None, None
    try:
        if isinstance(df.index, pd.DatetimeIndex):
            wk = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min",
                                           "Close": "last", "Volume": "sum"}).dropna()
            if len(wk) >= 20:
                wc = wk["Close"]
                w_fast, w_slow = float(ema(wc, 10).iloc[-1]), float(ema(wc, 30).iloc[-1])
                wl = float(wc.iloc[-1])
                wk_trend = ("UPTREND" if wl > w_fast > w_slow else
                            "DOWNTREND" if wl < w_fast < w_slow else "SIDEWAYS")
                if len(wc) >= 28:
                    wk_rsi = calc_rsi(wc)
    except Exception as e:
        logger.debug(f"weekly calc failed: {e}")
    t["weekly_trend"], t["weekly_rsi"] = wk_trend, wk_rsi

    # ── 52-week position ────────────────────────────────────────────────
    w = min(252, n)
    hi52, lo52 = float(h.iloc[-w:].max()), float(l.iloc[-w:].min())
    t.update(w52h=round(hi52, 2), w52l=round(lo52, 2),
             pos52=round((ltp - lo52) / (hi52 - lo52) * 100, 0) if hi52 > lo52 else None,
             dist_high=round((ltp / hi52 - 1) * 100, 1))

    # ── volume & accumulation ───────────────────────────────────────────
    v20 = float(v.iloc[-21:-1].mean()) if n > 21 else float(v.mean())
    v50 = float(v.iloc[-51:-1].mean()) if n > 51 else v20
    vr = float(v.iloc[-1]) / v20 if v20 > 0 else None
    d = c.diff()
    up_v = float(v.iloc[-20:][d.iloc[-20:] > 0].sum())
    dn_v = float(v.iloc[-20:][d.iloc[-20:] < 0].sum())
    udr = up_v / dn_v if dn_v > 0 else None
    obv = (np.sign(d.fillna(0)) * v).cumsum()
    t.update(vol_avg20=int(v20), vol_avg50=int(v50), vol_ratio=_f(vr),
             updown_vol=_f(udr),
             accumulation=("ACCUMULATION" if udr and udr >= 1.3 else
                           "DISTRIBUTION" if udr and udr <= 0.77 else "BALANCED"),
             obv_rising=bool(obv.iloc[-1] > obv.iloc[-21]) if n > 21 else None,
             traded_cr=round(float((c.iloc[-21:-1] * v.iloc[-21:-1]).mean()) / 1e7, 1) if n > 21 else None)

    # ── pivots & support / resistance ───────────────────────────────────
    H, L, C = float(h.iloc[-1]), float(l.iloc[-1]), ltp
    P = (H + L + C) / 3
    t["pivots"] = {"P": P, "R1": 2 * P - L, "S1": 2 * P - H, "R2": P + (H - L),
                   "S2": P - (H - L), "R3": H + 2 * (P - L), "S3": L - 2 * (H - P)}
    sh, sl = _swing_levels(h, l)
    cand = [(x, "Swing level") for x in _cluster(sh + sl)]
    cand += [(hi52, "52W high"), (lo52, "52W low"), (e20, "EMA 20"), (e50, "EMA 50")]
    if e200:
        cand.append((e200, "EMA 200"))
    cand += [(t["pivots"]["R1"], "Pivot R1"), (t["pivots"]["S1"], "Pivot S1")]
    sup = sorted([(x, s) for x, s in cand if x < ltp * 0.995], key=lambda z: -z[0])
    res = sorted([(x, s) for x, s in cand if x > ltp * 1.005], key=lambda z: z[0])

    def dedupe(lst):
        out = []
        for x, s in lst:
            if out and abs(x / out[-1][0] - 1) < 0.008:
                continue
            out.append((x, s))
        return out
    sup, res = dedupe(sup)[:3], dedupe(res)[:3]
    sup = [(x, "Swing support" if src == "Swing level" else src) for x, src in sup]
    res = [(x, "Swing resistance" if src == "Swing level" else src) for x, src in res]
    t["supports"] = [{"price": round(x, 2), "src": s, "dist": round((x / ltp - 1) * 100, 1)} for x, s in sup]
    t["resistances"] = [{"price": round(x, 2), "src": s, "dist": round((x / ltp - 1) * 100, 1)} for x, s in res]
    return t


# ══════════════════════════════════════════════════════════════════════════
# RISK
# ══════════════════════════════════════════════════════════════════════════
def compute_risk(df: pd.DataFrame, nifty: Optional[pd.DataFrame]) -> dict:
    c = df["Close"]
    r = c.pct_change().dropna()
    out: dict = {}
    if len(r) < 30:
        return {"level": "N/A"}
    ann = float(r.std()) * math.sqrt(252) * 100
    neg = r[r < 0]
    out["ann_vol"] = round(ann, 1)
    out["downside_vol"] = round(float(neg.std()) * math.sqrt(252) * 100, 1) if len(neg) > 5 else None
    dd = c / c.cummax() - 1
    out["max_dd"] = round(float(dd.min()) * 100, 1)
    out["cur_dd"] = round(float(dd.iloc[-1]) * 100, 1)
    out["var95"] = round(float(np.percentile(r, 5)) * 100, 2)
    out["worst_day"] = round(float(r.min()) * 100, 2)
    out["best_day"] = round(float(r.max()) * 100, 2)
    out["level"] = "Low" if ann < 25 else "Moderate" if ann < 40 else "High"

    out["beta"], out["corr"] = None, None
    try:
        if nifty is not None and not nifty.empty:
            nr = nifty["Close"].pct_change().dropna()
            j = pd.concat([r, nr], axis=1, join="inner").dropna()
            j.columns = ["s", "n"]
            if len(j) >= 60 and j["n"].var() > 0:
                out["beta"] = round(float(j["s"].cov(j["n"]) / j["n"].var()), 2)
                out["corr"] = round(float(j["s"].corr(j["n"])), 2)
    except Exception as e:
        logger.debug(f"beta calc failed: {e}")
    return out


# ══════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS
# ══════════════════════════════════════════════════════════════════════════
def _de_ratio(fund: dict, last_year: Optional[dict]) -> Optional[float]:
    """Debt/Equity as a plain ratio.

    The upstream fundamentals adapter is responsible for provider-specific
    units; never infer units from whether a ratio exceeds eight. An actual
    heavily leveraged company can legitimately exceed 8x.
    """
    if last_year and last_year.get("borrowings") is not None and last_year.get("networth"):
        if last_year["networth"] > 0:
            return round(last_year["borrowings"] / last_year["networth"], 2)
    de = fund.get("de")
    if de is None:
        return None
    try:
        value = float(de)
        return round(value, 2) if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def compute_fundamentals(fund: dict, stm: Optional[dict], extras: dict, peers: List[dict],
                         sector: Optional[str], ltp: float) -> dict:
    fv: dict = {"fin": is_financial_sector(sector)}
    years = (stm or {}).get("years") or []
    quarters = (stm or {}).get("quarters") or []
    growth = (extras or {}).get("growth") or {}
    last = years[-1] if years else None

    def g(title, period):
        return (growth.get(title) or {}).get(period)

    fv["rev_cagr3"] = g("Compounded Sales Growth", "3 Years")
    fv["rev_cagr5"] = g("Compounded Sales Growth", "5 Years")
    fv["pat_cagr3"] = g("Compounded Profit Growth", "3 Years")
    fv["pat_cagr5"] = g("Compounded Profit Growth", "5 Years")
    if fv["rev_cagr3"] is None:
        fv["rev_cagr3"] = _f(series_cagr(years, "revenue", 3), 1)
    if fv["rev_cagr5"] is None:
        fv["rev_cagr5"] = _f(series_cagr(years, "revenue", 5), 1)
    if fv["pat_cagr3"] is None:
        fv["pat_cagr3"] = _f(series_cagr(years, "net_profit", 3), 1)
    if fv["pat_cagr5"] is None:
        fv["pat_cagr5"] = _f(series_cagr(years, "net_profit", 5), 1)

    # latest quarter YoY / QoQ
    fv["q_rev_yoy"] = fv["q_pat_yoy"] = fv["q_rev_qoq"] = fv["q_pat_qoq"] = None
    if len(quarters) >= 5:
        a, b = quarters[-1], quarters[-5]
        if a.get("revenue") and b.get("revenue") and b["revenue"] > 0:
            fv["q_rev_yoy"] = round((a["revenue"] / b["revenue"] - 1) * 100, 1)
        if a.get("net_profit") is not None and b.get("net_profit") and b["net_profit"] > 0:
            fv["q_pat_yoy"] = round((a["net_profit"] / b["net_profit"] - 1) * 100, 1)
    if len(quarters) >= 2:
        a, b = quarters[-1], quarters[-2]
        if a.get("revenue") and b.get("revenue") and b["revenue"] > 0:
            fv["q_rev_qoq"] = round((a["revenue"] / b["revenue"] - 1) * 100, 1)
        if a.get("net_profit") is not None and b.get("net_profit") and b["net_profit"] > 0:
            fv["q_pat_qoq"] = round((a["net_profit"] / b["net_profit"] - 1) * 100, 1)

    # margins
    fv["opm"] = last.get("opm") if last else None
    fv["opm_3y_ago"] = years[-4].get("opm") if len(years) >= 4 else None
    fv["opm_delta"] = (round(fv["opm"] - fv["opm_3y_ago"], 1)
                       if fv["opm"] is not None and fv["opm_3y_ago"] is not None else None)
    fv["npm"] = (round(last["net_profit"] / last["revenue"] * 100, 1)
                 if last and last.get("net_profit") is not None and last.get("revenue") else None)

    # cash conversion (3y cumulative OCF / PAT) — not meaningful for banks/NBFCs
    fv["ocf_pat"] = None
    if not fv["fin"] and len(years) >= 3:
        o = [y.get("ocf") for y in years[-3:]]
        p = [y.get("net_profit") for y in years[-3:]]
        if all(x is not None for x in o + p) and sum(p) > 0:
            fv["ocf_pat"] = round(sum(o) / sum(p), 2)

    # profit consistency: down-years in last 5 transitions
    fv["down_years"] = None
    pats = [y.get("net_profit") for y in years[-6:]]
    if len(pats) >= 4 and all(x is not None for x in pats):
        fv["down_years"] = sum(1 for i in range(1, len(pats)) if pats[i] < pats[i - 1])

    fv["roe"] = fund.get("roe")
    fv["roce"] = last.get("roce") if last else None
    fv["de"] = None if fv["fin"] else _de_ratio(fund, last)
    fv["pe"], fv["fwd_pe"], fv["pb"] = fund.get("pe"), fund.get("fwd_pe"), fund.get("pb")
    fv["eps"], fv["div_y"], fv["mcap"], fv["rev_ttm"] = fund.get("eps"), fund.get("div_y"), fund.get("mcap"), fund.get("rev")

    # ── valuation vs peers ──────────────────────────────────────────────
    peer_pe = _median([p.get("pe") for p in peers if (p.get("pe") or 0) > 0])
    peer_roe = _median([p.get("roe") for p in peers])
    fv["peer_pe"], fv["peer_roe"] = _f(peer_pe, 1), _f(peer_roe, 1)
    fv["pe_prem"] = (round((fv["pe"] / peer_pe - 1) * 100, 0)
                     if fv["pe"] and fv["pe"] > 0 and peer_pe else None)
    fv["peg"] = (round(fv["pe"] / fv["pat_cagr3"], 2)
                 if fv["pe"] and fv["pe"] > 0 and fv["pat_cagr3"] and fv["pat_cagr3"] > 0 else None)
    fv["earn_yield"] = round(100.0 / fv["pe"], 2) if fv["pe"] and fv["pe"] > 0 else None
    # EPS, P/E and price come from different feeds; only trust the cross-check when they agree
    # (EPS × P/E should land near the market price). Otherwise a unit/period mismatch would
    # print a wildly wrong "implied value".
    consistent = bool(fv["eps"] and fv["eps"] > 0 and fv["pe"] and fv["pe"] > 0 and ltp
                      and abs(fv["eps"] * fv["pe"] / ltp - 1) <= 0.25)
    fv["implied_peer_value"] = round(fv["eps"] * peer_pe, 2) if consistent and peer_pe else None
    fv["implied_upside"] = (round((fv["implied_peer_value"] / ltp - 1) * 100, 1)
                            if fv["implied_peer_value"] and ltp else None)
    fv["has_statements"] = bool(years)
    fv["has_quarters"] = bool(quarters)
    return fv


# ══════════════════════════════════════════════════════════════════════════
# SCORECARD
# ══════════════════════════════════════════════════════════════════════════
WEIGHTS = {"Trend": 0.20, "Momentum": 0.15, "Quality": 0.20,
           "Valuation": 0.15, "Growth": 0.15, "Risk": 0.15}


def _trend_score(t: dict) -> float:
    ltp = t["ltp"]
    pts = 0.0
    pts += 15 if ltp > t["ema20"] else 0
    pts += 15 if ltp > t["ema50"] else 0
    if t.get("ema200"):
        pts += 15 if ltp > t["ema200"] else 0
        pts += 10 if t["ema50"] > t["ema200"] else 0
    else:
        pts += 12
    pts += 10 if t["ema20"] > t["ema50"] else 0
    wk = t.get("weekly_trend")
    pts += 15 if wk == "UPTREND" else 7 if wk in ("SIDEWAYS", None) else 0
    adx, up = t.get("adx") or 0, (t.get("plus_di") or 0) > (t.get("minus_di") or 0)
    pts += (20 if up else 0) if adx >= 25 else 10 if adx >= 18 else 8
    return min(100.0, pts)


def _momentum_score(t: dict) -> float:
    rsi = t["rsi"]
    if rsi > 80:
        r = 10
    elif rsi >= 70:
        r = 20
    elif rsi >= 55:
        r = 30
    elif rsi >= 50:
        r = 22
    elif rsi >= 45:
        r = 14
    elif rsi >= 40:
        r = 8
    elif rsi >= 30:
        r = 3
    else:
        r = 6
    mh, mm = t["macd_hist"], t["macd"]
    m = 20 if (mh > 0 and mm > 0) else 14 if mh > 0 else 8 if mm > 0 else 2
    ret3 = (t.get("returns") or {}).get("3M") or {}
    rel = ret3.get("rel") if ret3.get("rel") is not None else ret3.get("stock")
    rs = _band(rel, [(15, 30), (5, 22), (0, 15), (-5, 8), (-1e9, 2)], 15)
    ret1 = (t.get("returns") or {}).get("1M") or {}
    m1 = _band(ret1.get("stock"), [(8, 20), (3, 16), (0, 12), (-5, 6), (-1e9, 2)], 10)
    return float(r + m + rs + m1)


def _quality_score(fv: dict) -> Optional[float]:
    comps = [
        _band(fv.get("roe"), [(25, 100), (18, 85), (12, 65), (8, 40), (0.01, 20), (-1e9, 5)]),
        _band(fv.get("roce"), [(25, 100), (18, 85), (12, 65), (8, 40), (0.01, 20), (-1e9, 5)]),
    ]
    if not fv["fin"]:
        de = fv.get("de")
        comps.append(None if de is None else _band(-de, [(-0.2, 100), (-0.5, 85), (-1, 65), (-2, 35), (-1e9, 10)]))
        comps.append(_band(fv.get("ocf_pat"), [(1.0, 100), (0.8, 80), (0.5, 50), (-1e9, 20)]))
    comps.append(_band(fv.get("opm_delta"), [(2, 90), (-1, 65), (-4, 40), (-1e9, 15)]))
    dy = fv.get("down_years")
    comps.append(None if dy is None else {0: 100, 1: 75, 2: 50}.get(dy, 20))
    return _avg(comps)


def _growth_score(fv: dict) -> Optional[float]:
    comps = [
        _band(fv.get("rev_cagr3"), [(20, 100), (12, 80), (6, 55), (0, 30), (-1e9, 10)]),
        _band(fv.get("pat_cagr3"), [(25, 100), (15, 80), (8, 55), (0, 30), (-1e9, 10)]),
        _band(fv.get("q_rev_yoy"), [(20, 100), (10, 80), (0, 55), (-10, 30), (-1e9, 10)]),
        _band(fv.get("q_pat_yoy"), [(20, 100), (10, 80), (0, 55), (-10, 30), (-1e9, 10)]),
    ]
    return _avg(comps)


def _valuation_score(fv: dict) -> Optional[float]:
    comps = []
    pe = fv.get("pe")
    if pe is not None and pe <= 0:
        comps.append(5)                                    # loss-making
    elif pe:
        comps.append(_band(-pe, [(-12, 90), (-18, 75), (-25, 60), (-35, 42), (-50, 25), (-1e9, 10)]))
    prem = fv.get("pe_prem")
    if prem is not None:
        comps.append(_band(-prem, [(25, 95), (10, 80), (-10, 60), (-30, 40), (-60, 22), (-1e9, 10)]))
    peg = fv.get("peg")
    if peg is not None:
        comps.append(_band(-peg, [(-1, 95), (-1.5, 75), (-2, 55), (-3, 35), (-1e9, 15)]))
    if fv["fin"] and fv.get("pb"):
        comps.append(_band(-fv["pb"], [(-1.5, 85), (-3, 60), (-5, 35), (-1e9, 15)]))
    return _avg(comps)


def _risk_score(t: dict, rk: dict) -> Optional[float]:
    comps = [
        _band(-(rk.get("ann_vol") or 99), [(-25, 90), (-35, 65), (-50, 40), (-1e9, 15)]) if rk.get("ann_vol") else None,
        _band(rk.get("max_dd"), [(-10, 90), (-20, 70), (-30, 45), (-1e9, 20)]) if rk.get("max_dd") is not None else None,
        None if rk.get("beta") is None else _band(-rk["beta"], [(-0.9, 80), (-1.3, 60), (-1e9, 35)]),
        _band(t.get("traded_cr"), [(50, 100), (10, 80), (3, 55), (1, 30), (-1e9, 10)]) if t.get("traded_cr") is not None else None,
    ]
    return _avg(comps)


def compute_scores(t: dict, rk: dict, fv: dict) -> dict:
    s = {
        "Trend": _trend_score(t),
        "Momentum": _momentum_score(t),
        "Quality": _quality_score(fv),
        "Valuation": _valuation_score(fv),
        "Growth": _growth_score(fv),
        "Risk": _risk_score(t, rk),
    }
    avail = {k: v for k, v in s.items() if v is not None}
    wsum = sum(WEIGHTS[k] for k in avail)
    comp = sum(WEIGHTS[k] * v for k, v in avail.items()) / wsum if wsum else 0.0
    coverage = round(wsum * 100)
    fundamental_pillars = sum(s[k] is not None for k in ("Quality", "Valuation", "Growth"))
    # The presence of a vendor ratio alone does not establish a financial thesis.
    has_statements = bool(fv.get("has_statements"))
    has_valuation_basis = any(fv.get(k) is not None for k in ("pe", "pb", "fwd_pe"))
    insufficient_evidence = (coverage < 70 or fundamental_pillars < 2 or
                             not has_statements or not has_valuation_basis)
    fund_missing = fundamental_pillars == 0
    if comp >= 75:
        stance, action = "HIGH-CONVICTION CONSTRUCTIVE", "Accumulate on dips; add on confirmed breakouts above resistance."
    elif comp >= 62:
        stance, action = "CONSTRUCTIVE", "Accumulate in tranches near support; avoid chasing extended moves."
    elif comp >= 48:
        stance, action = "NEUTRAL / WAIT", "Hold existing exposure; wait for trend or valuation to improve before adding."
    elif comp >= 35:
        stance, action = "CAUTIOUS", "Avoid fresh longs; protect gains and respect stop-losses."
    else:
        stance, action = "NEGATIVE", "Avoid; reduce exposure into strength."

    veto = False
    if insufficient_evidence:
        stance, action, veto = (
            "INSUFFICIENT EVIDENCE",
            "Research only; no investment rating or actionable trade plan until financial evidence is complete.",
            True,
        )
    elif s["Trend"] is not None and s["Trend"] < 35 and comp >= 48:
        stance, action, veto = "NEUTRAL / WAIT", ("Fundamentals/valuation are not the problem — price trend is. "
                                                  "Wait for a reclaim of the 50-day EMA before committing."), True

    def hz(vals, hi, lo, names):
        a = _avg([s[k] for k in vals])
        if a is None:
            return "N/A", None
        return (names[0] if a >= hi else names[1] if a >= lo else names[2]), round(a)

    horizons = {
        "Short term (1–4 wks)": hz(["Trend", "Momentum"], 65, 45, ("BULLISH", "NEUTRAL", "BEARISH")),
        "Medium term (3–6 mths)": hz(["Trend", "Momentum", "Growth"], 65, 45, ("BULLISH", "NEUTRAL", "BEARISH")),
        "Long term (1–3 yrs)": hz(["Quality", "Valuation", "Growth"], 65, 45, ("FAVOURABLE", "NEUTRAL", "UNFAVOURABLE")),
    }
    if insufficient_evidence:
        horizons["Medium term (3–6 mths)"] = ("N/A", None)
        horizons["Long term (1–3 yrs)"] = ("N/A", None)
    return {"pillars": {k: (round(v) if v is not None else None) for k, v in s.items()},
            "composite": round(comp), "coverage": coverage, "stance": stance, "action": action,
            "veto": veto, "tech_only": fund_missing,
            "insufficient_evidence": insufficient_evidence,
            "fundamental_pillars": fundamental_pillars, "horizons": horizons}


# ══════════════════════════════════════════════════════════════════════════
# TRADE PLAN + SCENARIOS
# ══════════════════════════════════════════════════════════════════════════
def build_trade_plan(t: dict, rk: dict, scores: dict) -> dict:
    ltp, atr = t["ltp"], t["atr"]
    sup = [s["price"] for s in t["supports"]]
    res = [r["price"] for r in t["resistances"]]
    tr, mo = scores["pillars"]["Trend"], scores["pillars"]["Momentum"]
    bias = "BULLISH" if (tr is not None and mo is not None and tr >= 55 and mo >= 45) else "BEARISH" if tr is not None and tr < 35 else "NEUTRAL"

    stance = scores.get("stance", "")
    if scores.get("insufficient_evidence"):
        mode = "NO_TRADE"
    elif bias == "BEARISH" or stance in ("CAUTIOUS", "NEGATIVE"):
        mode = "DEFENSIVE"          # no fresh long setup
    elif stance == "NEUTRAL / WAIT" or bias == "NEUTRAL":
        mode = "CONDITIONAL"        # only on confirmation
    else:
        mode = "ACTIVE"
    plan: dict = {"bias": bias, "atr": atr, "mode": mode}
    cand = [s for s in sup if ltp - 2.5 * atr <= s < ltp - 0.5 * atr]
    sl = (cand[0] - 0.25 * atr) if cand else ltp - 2.0 * atr
    if sl >= ltp - 0.5 * atr:
        sl = ltp - 1.0 * atr
    risk = ltp - sl
    plan.update(entry_lo=round(ltp - 0.75 * atr, 2), entry_hi=round(ltp, 2), sl=round(sl, 2),
                risk_pct=round(risk / ltp * 100, 1),
                t1=round(ltp + 1.5 * risk, 2), t2=round(ltp + 2.5 * risk, 2), t3=round(ltp + 4.0 * risk, 2))
    plan["next_res"] = res[0] if res else None
    plan["next_sup"] = sup[0] if sup else None
    # sizing example
    capital, risk_pct = 1_000_000, 1.0
    risk_amt = capital * risk_pct / 100
    qty = int(risk_amt // risk) if risk > 0 else 0
    plan["sizing"] = {"capital": capital, "risk_pct": risk_pct, "risk_amt": risk_amt,
                      "qty": qty, "value": round(qty * ltp, 0),
                      "pct_capital": round(qty * ltp / capital * 100, 1)}
    plan["bull_trigger"] = (res[0] if res else t["w52h"])
    plan["bear_trigger"] = (sup[0] if sup else t["ema50"])
    plan["reclaim"] = t["ema50"] if ltp < t["ema50"] else (t["ema200"] if t.get("ema200") and ltp < t["ema200"] else None)

    sig = (rk.get("ann_vol") or 0) / 100 * math.sqrt(21 / 252)
    plan["range_1m"] = {"s1_lo": round(ltp * (1 - sig), 2), "s1_hi": round(ltp * (1 + sig), 2),
                        "s2_lo": round(ltp * (1 - 2 * sig), 2), "s2_hi": round(ltp * (1 + 2 * sig), 2),
                        "sigma_pct": round(sig * 100, 1)} if sig else None
    return plan


# ══════════════════════════════════════════════════════════════════════════
# FLAGS (strengths & risks backed by evidence)
# ══════════════════════════════════════════════════════════════════════════
def build_flags(t: dict, rk: dict, fv: dict, sh: List[dict], consensus: Optional[dict]) -> dict:
    pos: List[str] = []
    risks: List[dict] = []

    def R(sev, title, evidence):
        risks.append({"sev": sev, "title": title, "evidence": evidence})

    ltp = t["ltp"]
    # technical
    if t["trend"] in ("STRONG UPTREND", "UPTREND"):
        pos.append(f"Price structure is a {t['trend'].lower()} — trading above its 20 and 50-day EMAs"
                   + (" and 200-day EMA." if t.get("ema200") and ltp > t["ema200"] else "."))
    if t["trend"] in ("STRONG DOWNTREND", "DOWNTREND"):
        R("High", "Price in a downtrend",
          f"Trades below EMA20 ({t['ema20']:,.2f}) and EMA50 ({t['ema50']:,.2f}); {t['dist_high']:+.1f}% from the 52-week high.")
    elif t.get("ema200") and ltp < t["ema200"]:
        R("Medium", "Below the 200-day EMA", f"LTP {ltp:,.2f} vs EMA200 {t['ema200']:,.2f} — long-term trend not yet repaired.")
    r1 = (t.get("returns") or {}).get("1Y") or {}
    if r1.get("rel") is not None and r1["rel"] >= 5:
        pos.append(f"Outperformed the Nifty 50 by {r1['rel']:.1f} pts over 1 year ({r1['stock']:+.1f}% vs {r1['nifty']:+.1f}%).")
    elif r1.get("rel") is not None and r1["rel"] <= -10:
        R("Medium", "Persistent relative weakness",
          f"Lagged the Nifty 50 by {abs(r1['rel']):.1f} pts over 1 year ({r1['stock']:+.1f}% vs {r1['nifty']:+.1f}%).")
    if t["rsi"] > 75:
        R("Medium", "Overbought momentum", f"RSI(14) at {t['rsi']} — elevated pullback risk after a stretched run.")
    if t["accumulation"] == "ACCUMULATION":
        pos.append(f"Volume pattern shows accumulation (up-day volume {t['updown_vol']}× down-day volume over 20 sessions).")
    elif t["accumulation"] == "DISTRIBUTION":
        R("Medium", "Distribution in volume", f"Down-day volume dominates (up/down volume ratio {t['updown_vol']}) over the last 20 sessions.")
    # risk
    if rk.get("ann_vol") and rk["ann_vol"] >= 45:
        R("High", "Very high volatility", f"Annualised volatility {rk['ann_vol']}%; worst single day {rk['worst_day']}%.")
    elif rk.get("ann_vol") and rk["ann_vol"] >= 35:
        R("Medium", "Elevated volatility", f"Annualised volatility {rk['ann_vol']}%.")
    if rk.get("max_dd") is not None and rk["max_dd"] <= -35:
        R("High", "Deep drawdown history", f"Peak-to-trough fall of {rk['max_dd']}% within the last year; currently {rk['cur_dd']}% below the peak.")
    if t.get("traded_cr") is not None and t["traded_cr"] < 3:
        R("High" if t["traded_cr"] < 1 else "Medium", "Thin liquidity",
          f"Average daily traded value is only ₹{t['traded_cr']} Cr — entries/exits can move the price.")
    # fundamentals
    if fv.get("roe") is not None:
        if fv["roe"] >= 18:
            pos.append(f"High return on equity of {fv['roe']:.1f}% (above the 15% quality benchmark).")
        elif fv["roe"] < 8:
            R("Medium", "Low return on equity", f"ROE of {fv['roe']:.1f}% is below the cost of equity.")
    if fv.get("roce") is not None and fv["roce"] >= 20:
        pos.append(f"ROCE of {fv['roce']:.1f}% indicates efficient capital deployment.")
    if fv.get("de") is not None and not fv["fin"]:
        if fv["de"] > 1.5:
            R("High", "High leverage", f"Debt/Equity of {fv['de']:.2f} leaves little cushion if earnings slip.")
        elif fv["de"] > 0.8:
            R("Medium", "Moderate leverage", f"Debt/Equity of {fv['de']:.2f}.")
        elif fv["de"] <= 0.3:
            pos.append(f"Conservative balance sheet — Debt/Equity only {fv['de']:.2f}.")
    if fv.get("ocf_pat") is not None:
        if fv["ocf_pat"] < 0.6:
            R("High", "Weak cash conversion", f"3-year operating cash flow is only {fv['ocf_pat']:.2f}× reported profit — earnings quality needs checking.")
        elif fv["ocf_pat"] >= 0.9:
            pos.append(f"Profits are backed by cash — 3-year operating cash flow is {fv['ocf_pat']:.2f}× net profit.")
    if fv.get("rev_cagr3") is not None and fv["rev_cagr3"] >= 15:
        pos.append(f"Strong top-line compounding: sales CAGR of {fv['rev_cagr3']:.0f}% (3-yr)"
                   + (f" and {fv['rev_cagr5']:.0f}% (5-yr)." if fv.get("rev_cagr5") is not None else "."))
    if fv.get("pat_cagr3") is not None and fv["pat_cagr3"] >= 20:
        pos.append(f"Profit CAGR of {fv['pat_cagr3']:.0f}% over 3 years shows operating leverage.")
    if fv.get("pat_cagr3") is not None and fv["pat_cagr3"] < 0:
        R("High", "Shrinking profits", f"3-year profit CAGR is {fv['pat_cagr3']:.0f}%.")
    if fv.get("q_pat_yoy") is not None and fv["q_pat_yoy"] <= -15:
        R("High", "Latest-quarter profit decline", f"Net profit fell {abs(fv['q_pat_yoy']):.0f}% YoY in the latest quarter.")
    elif fv.get("q_pat_yoy") is not None and fv["q_pat_yoy"] >= 20:
        pos.append(f"Latest-quarter net profit grew {fv['q_pat_yoy']:.0f}% YoY.")
    if fv.get("opm_delta") is not None:
        if fv["opm_delta"] <= -4:
            R("Medium", "Margin compression", f"Operating margin down {abs(fv['opm_delta']):.1f} pts over 3 years (now {fv['opm']:.1f}%).")
        elif fv["opm_delta"] >= 3:
            pos.append(f"Operating margin expanded {fv['opm_delta']:.1f} pts in 3 years to {fv['opm']:.1f}%.")
    # valuation
    if fv.get("pe") is not None and fv["pe"] <= 0:
        R("High", "Loss-making on trailing basis", "Trailing EPS is negative; P/E based valuation is not meaningful.")
    if fv.get("pe_prem") is not None:
        if fv["pe_prem"] >= 40:
            R("High", "Rich valuation vs peers", f"P/E of {fv['pe']:.1f}× is a {fv['pe_prem']:.0f}% premium to the peer median ({fv['peer_pe']}×).")
        elif fv["pe_prem"] >= 15:
            R("Medium", "Premium valuation", f"P/E of {fv['pe']:.1f}× vs peer median {fv['peer_pe']}× ({fv['pe_prem']:+.0f}%).")
        elif fv["pe_prem"] <= -15:
            pos.append(f"Trades at a {abs(fv['pe_prem']):.0f}% P/E discount to peers ({fv['pe']:.1f}× vs {fv['peer_pe']}×).")
    elif fv.get("pe") and fv["pe"] > 60:
        R("High", "Very high absolute P/E", f"Trailing P/E of {fv['pe']:.1f}× prices in sustained high growth.")
    if (fv.get("pe_prem") is not None and fv["pe_prem"] <= -40
            and ((fv.get("roe") is not None and fv["roe"] < 8) or (fv.get("pat_cagr3") is not None and fv["pat_cagr3"] < 0))):
        R("Medium", "Possible value trap",
          f"P/E is {abs(fv['pe_prem']):.0f}% below peers, but profitability/earnings trend is weak — cheap for a reason until proven otherwise.")
    if fv.get("peg") is not None and fv["peg"] <= 1.0:
        pos.append(f"PEG of {fv['peg']} — price is modest relative to 3-year profit growth.")
    # ownership
    if len(sh) >= 5:
        first, lastq = sh[-5], sh[-1]
        for key, nm in (("promoters", "Promoter"), ("fii", "FII"), ("dii", "DII")):
            a, b = first.get(key), lastq.get(key)
            if a is None or b is None:
                continue
            d = b - a
            if key == "promoters" and d <= -3:
                R("High", "Promoter stake falling", f"Promoter holding down {abs(d):.1f} pts over 4 quarters ({a:.1f}% → {b:.1f}%).")
            elif key == "promoters" and d >= 1:
                pos.append(f"Promoters raised their stake by {d:.1f} pts over 4 quarters ({a:.1f}% → {b:.1f}%).")
            elif key in ("fii", "dii") and d >= 2:
                pos.append(f"{nm} holding up {d:.1f} pts over 4 quarters ({a:.1f}% → {b:.1f}%) — institutional accumulation.")
            elif key == "fii" and d <= -3:
                R("Medium", "FII selling", f"FII holding down {abs(d):.1f} pts over 4 quarters ({a:.1f}% → {b:.1f}%).")
    if consensus and consensus.get("upside") is not None:
        if consensus["upside"] >= 15:
            pos.append(f"Analyst consensus target ₹{consensus['mean']:,.0f} implies {consensus['upside']:+.0f}% upside.")
        elif consensus["upside"] <= -5:
            R("Medium", "Trades above analyst consensus", f"Consensus target ₹{consensus['mean']:,.0f} is {consensus['upside']:+.0f}% from the current price.")

    order = {"High": 0, "Medium": 1, "Low": 2}
    risks.sort(key=lambda r: order[r["sev"]])
    return {"strengths": pos, "risks": risks}


# ══════════════════════════════════════════════════════════════════════════
# NARRATIVE — deterministic (always available)
# ══════════════════════════════════════════════════════════════════════════
def _ordinal(n: int) -> str:
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _pct(x, nd=1, sign=True):
    return "n/a" if x is None else (f"{x:+.{nd}f}%" if sign else f"{x:.{nd}f}%")


def deterministic_narrative(name: str, sym: str, t: dict, rk: dict, fv: dict, sc: dict,
                            plan: dict, sh: List[dict]) -> Dict[str, str]:
    ltp = t["ltp"]
    out: Dict[str, str] = {}

    out["summary"] = (
        f"{name} ({sym}) scores {sc['composite']}/100 on our six-pillar framework, which translates to a "
        f"<b>{sc['stance']}</b> stance. {sc['action']}"
        + (" The score is driven by price action only because fundamental data could not be retrieved — "
           "treat the view as technical-only." if sc["tech_only"] else "")
    )

    parts = [f"The stock closed at ₹{ltp:,.2f}, {abs(t['dist_high']):.1f}% below its 52-week high of ₹{t['w52h']:,.2f} "
             f"and sits at the {_ordinal(t['pos52'])} percentile of its annual range." if t.get("pos52") is not None else
             f"The stock closed at ₹{ltp:,.2f}."]
    parts.append(f"The daily trend reads <b>{t['trend'].lower()}</b>"
                 + (f", and the weekly chart shows "
                    f"{ {'UPTREND': 'an uptrend', 'DOWNTREND': 'a downtrend'}.get(t['weekly_trend'], 'a sideways pattern') }"
                    if t.get("weekly_trend") else "") + ".")
    rsi_txt = ("overbought" if t["rsi"] > 70 else "oversold" if t["rsi"] < 30 else
               "firm" if t["rsi"] >= 55 else "soft" if t["rsi"] < 45 else "neutral")
    parts.append(f"RSI(14) of {t['rsi']} is {rsi_txt}; MACD histogram is {'positive' if t['macd_hist'] > 0 else 'negative'}. "
                 f"ADX of {t['adx']} indicates a {'trending' if t['adx'] >= 25 else 'weakly trending / range-bound'} market.")
    r3 = (t.get("returns") or {}).get("3M") or {}
    if r3.get("rel") is not None:
        parts.append(f"Over three months the stock has {'outperformed' if r3['rel'] >= 0 else 'underperformed'} "
                     f"the Nifty 50 by {abs(r3['rel']):.1f} pts.")
    parts.append(f"Volume flow is classified as <b>{t['accumulation'].lower()}</b>.")
    if t["supports"] and t["resistances"]:
        parts.append(f"Immediate support lies at ₹{t['supports'][0]['price']:,.2f} ({t['supports'][0]['src']}) and "
                     f"resistance at ₹{t['resistances'][0]['price']:,.2f} ({t['resistances'][0]['src']}).")
    out["technical"] = " ".join(parts)

    f_parts = []
    if fv.get("pe"):
        f_parts.append(f"The stock trades at {fv['pe']:.1f}× trailing earnings"
                       + (f", a {abs(fv['pe_prem']):.0f}% {'premium' if fv['pe_prem'] >= 0 else 'discount'} to the peer median of {fv['peer_pe']}×" if fv.get("pe_prem") is not None else "")
                       + (f", with a PEG of {fv['peg']}" if fv.get("peg") else "") + ".")
    if fv.get("roe") is not None:
        f_parts.append(f"ROE is {fv['roe']:.1f}%" + (f" and ROCE {fv['roce']:.1f}%" if fv.get("roce") is not None else "") + ".")
    if fv.get("rev_cagr3") is not None or fv.get("pat_cagr3") is not None:
        f_parts.append("Growth: " + ", ".join(x for x in [
            f"sales CAGR {fv['rev_cagr3']:.0f}% (3-yr)" if fv.get("rev_cagr3") is not None else None,
            f"profit CAGR {fv['pat_cagr3']:.0f}% (3-yr)" if fv.get("pat_cagr3") is not None else None] if x) + ".")
    if fv.get("q_rev_yoy") is not None or fv.get("q_pat_yoy") is not None:
        f_parts.append("The latest quarter showed " + " and ".join(x for x in [
            f"revenue {fv['q_rev_yoy']:+.0f}% YoY" if fv.get("q_rev_yoy") is not None else None,
            f"net profit {fv['q_pat_yoy']:+.0f}% YoY" if fv.get("q_pat_yoy") is not None else None] if x) + ".")
    if fv.get("de") is not None and not fv["fin"]:
        f_parts.append(f"Leverage is {'low' if fv['de'] <= 0.5 else 'moderate' if fv['de'] <= 1.2 else 'high'} at "
                       f"{fv['de']:.2f}× debt/equity.")
    out["fundamental"] = " ".join(f_parts) if f_parts else ""

    if len(sh) >= 2:
        a, b = sh[-min(5, len(sh))], sh[-1]
        bits = []
        for k, nm in (("promoters", "Promoters"), ("fii", "FIIs"), ("dii", "DIIs")):
            if a.get(k) is not None and b.get(k) is not None:
                bits.append(f"{nm} {b[k]:.1f}% ({b[k] - a[k]:+.1f} pts)")
        out["ownership"] = ("Shareholding as of " + b["label"] + ": " + ", ".join(bits) + ".") if bits else ""
    else:
        out["ownership"] = ""

    hz = sc["horizons"]
    out["outlook"] = (f"Short-term bias is <b>{hz['Short term (1–4 wks)'][0].lower()}</b>, medium-term "
                      f"<b>{hz['Medium term (3–6 mths)'][0].lower()}</b> and the long-term fundamental picture is "
                      f"<b>{hz['Long term (1–3 yrs)'][0].lower()}</b>. "
                      + (f"A close above ₹{plan['bull_trigger']:,.2f} on strong volume would confirm upside continuation; "
                         f"a close below ₹{plan['bear_trigger']:,.2f} would invalidate the constructive case."
                         if plan.get("bull_trigger") and plan.get("bear_trigger") else ""))
    return out


# ══════════════════════════════════════════════════════════════════════════
# NARRATIVE — optional LLM, numerically grounded
# ══════════════════════════════════════════════════════════════════════════
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers(text: str) -> List[float]:
    out = []
    for m in _NUM_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def numbers_grounded(text: str, facts: str, max_unmatched: int = 0) -> bool:
    """Reject invented numeric facts, including small % and invented years.

    Historical implementation let arbitrary values <=12 and years 1990-2040
    through, and tolerated one unmatched large number. Those exceptions
    allowed hallucinated earnings growth, P/E and reporting years. This is
    intentionally strict; reject LLM prose rather than edit it into truth.
    """
    fact_nums = [abs(x) for x in _numbers(facts)]
    missing = 0
    for number in _numbers(text):
        x = abs(number)
        # A plausible reporting YEAR must match exactly, not merely be
        # "within 0.5%" of another year (2024 vs 2026 is misinformation).
        if 1900 <= x <= 2100 and x.is_integer():
            matched = any(known == x for known in fact_nums)
        else:
            matched = any(abs(x - known) <= max(0.005 * known, 0.011)
                          for known in fact_nums)
        if not matched:
            missing += 1
            if missing > max_unmatched:
                return False
    return True


def facts_block(name, sym, sector, t, rk, fv, sc, plan, sh) -> str:
    L = [f"Company: {name} ({sym}); sector: {re.sub(r'[^A-Za-z &]', '', sector or 'n/a').strip()}",
         f"Price observation date: {t.get('asof') or 'Unavailable'}; quote is HISTORICAL closing price, not live LTP",
         f"Price feed: {t.get('price_source') or 'Unverified provider'}; financial data basis: {fv.get('source') or 'Unverified provider/period'}",
         f"Price: {t['ltp']}; 52W high {t['w52h']}; 52W low {t['w52l']}; distance from high {t['dist_high']}%",
         f"Trend: {t['trend']}; weekly {t.get('weekly_trend')}; RSI {t['rsi']}; MACD hist {t['macd_hist']}; ADX {t['adx']}",
         f"EMA20 {t['ema20']}; EMA50 {t['ema50']}; EMA200 {t.get('ema200')}",
         f"Volume flow: {t['accumulation']}",
         f"Composite score {sc['composite']}/100; stance {sc['stance']}",
         "Pillar scores: " + ", ".join(f"{k} {v}" for k, v in sc["pillars"].items() if v is not None),
         f"Annual volatility {rk.get('ann_vol')}%; max drawdown {rk.get('max_dd')}%; beta {rk.get('beta')}",
         f"PE {fv.get('pe')}; peer median PE {fv.get('peer_pe')}; PE premium {fv.get('pe_prem')}%; PEG {fv.get('peg')}; P/B {fv.get('pb')}",
         f"ROE {fv.get('roe')}%; ROCE {fv.get('roce')}%; Debt/Equity {fv.get('de')}; OCF/PAT {fv.get('ocf_pat')}",
         f"Sales CAGR 3Y {fv.get('rev_cagr3')}%; 5Y {fv.get('rev_cagr5')}%; Profit CAGR 3Y {fv.get('pat_cagr3')}%; 5Y {fv.get('pat_cagr5')}%",
         f"Latest quarter YoY: revenue {fv.get('q_rev_yoy')}%, profit {fv.get('q_pat_yoy')}%; OPM {fv.get('opm')}%",
         f"Support {plan.get('next_sup')}; resistance {plan.get('next_res')}; stop {plan['sl']}; T1 {plan['t1']}; T2 {plan['t2']}"]
    if sh:
        b = sh[-1]
        L.append(f"Shareholding {b['label']}: promoters {b.get('promoters')}%, FII {b.get('fii')}%, DII {b.get('dii')}%")
    return "\n".join(L)


def ai_analyst_note(name, sym, sector, t, rk, fv, sc, plan, sh) -> str:
    """Three-paragraph analyst note from the LLM, or '' if unavailable/ungrounded."""
    try:
        from ai_engine import ai_available, _call_ai
        if not ai_available():
            return ""
        # Do not produce a purported institutional analyst note from numbers
        # that have no documented price-session date. Deterministic sections
        # remain available when an AI evidence precondition is unavailable.
        if not t.get("asof"):
            return ""
        facts = facts_block(name, sym, sector, t, rk, fv, sc, plan, sh)
        system = ("You are a senior equity research analyst at an Indian institutional brokerage. "
                  "Write in a crisp, professional sell-side voice. Use ONLY the facts provided. "
                  "Never invent numbers, news, management commentary, guidance, orders or events. "
                  "Do not add price targets beyond those given. Plain text only — no markdown, no bullets, no headings.")
        prompt = (f"FACTS:\n{facts}\n\nWrite exactly three short paragraphs separated by a blank line:\n"
                  f"1) Investment view — what the combination of trend, momentum and fundamentals says.\n"
                  f"2) Business quality and valuation — interpret growth, returns, leverage and valuation vs peers; "
                  f"if a data point is 'None', say it is unavailable instead of guessing.\n"
                  f"3) Risks and what would change the view — reference the supplied support/resistance levels.\n"
                  f"Each paragraph max 70 words.")
        text, err = _call_ai([{"role": "user", "content": prompt}], 520, system)
        if not text:
            logger.info(f"[research_engine] AI note unavailable: {err}")
            return ""
        text = re.sub(r"[*_#`]+", "", text).strip()
        if not numbers_grounded(text, facts):
            logger.warning("[research_engine] AI note rejected: contained numbers not in supplied facts")
            return ""
        # A number-free statement can still invent a current event or
        # external research. If these appear without supplied evidence,
        # retain the deterministic report rather than publish the AI claim.
        forbidden = (
            "according to a recent report", "latest news confirms",
            "company announced today", "management has guided",
            "analysts expect", "guaranteed return",
        )
        if any(term in text.lower() for term in forbidden):
            logger.warning("[research_engine] AI note rejected: unsupported external event")
            return ""
        return text
    except Exception as e:
        logger.warning(f"[research_engine] AI note failed: {e}")
        return ""
