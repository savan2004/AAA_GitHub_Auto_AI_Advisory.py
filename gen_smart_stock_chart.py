#!/usr/bin/env python3
# gen_smart_stock_chart.py — Pro Chart v7.0  (Team Sprint Upgrade)
# 11-check weighted scoring | S/R proximity | Weekly trend | Candle confirm
# BB squeeze | No-Trade Zone | EMA freshness decay | MACD slope-aware
import sys, os, warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.collections import LineCollection
import matplotlib.patheffects as pe
import matplotlib.ticker as mticker
warnings.filterwarnings("ignore")

# ── PRO DARK THEME PALETTE ────────────────────────────────────────────────────
BG_DARK    = "#131722"
BG_PANEL   = "#1E222D"
BG_CARD    = "#2A2E39"
GRID_COL   = "#1E222D"
BORDER_COL = "#363A45"
TEXT_PRI   = "#D1D4DC"
TEXT_SEC   = "#787B86"
TEXT_ACC   = "#FFFFFF"
TV_GREEN   = "#26A69A"
TV_GREEN_L = "#0D4842"
TV_RED     = "#EF5350"
TV_RED_L   = "#4C1B1B"
TV_BLUE    = "#2962FF"
TV_ORANGE  = "#FF9800"
TV_PURPLE  = "#9C27B0"
TV_TEAL    = "#26C6DA"
TV_GOLD    = "#FFD700"
TV_AMBER   = "#FF6D00"
TV_VIOLET  = "#E040FB"
TV_YELLOW  = "#F6C026"

SIGNAL_COLORS = {
    "STRONG BUY":  ("#00E676", "#0D3B2E"),
    "BUY":         ("#26A69A", "#0D2E2B"),
    "WAIT":        ("#F6C026", "#2A2410"),   # No-Trade Zone
    "SELL":        ("#EF5350", "#3B1212"),
    "STRONG SELL": ("#FF1744", "#4C0F0F"),
}

# ── DATA CLASSES ──────────────────────────────────────────────────────────────
@dataclass
class Pivot:
    idx: int
    price: float
    kind: str

@dataclass
class Pattern:
    name: str
    confidence: float
    direction: str
    start_idx: int
    end_idx: int
    key_levels: list = field(default_factory=list)
    description: str = ""

@dataclass
class ElliottWave:
    pivots: list
    labels: list
    fib_ext: dict
    wave_complete: bool

# ── PIVOT DETECTION ───────────────────────────────────────────────────────────
def find_pivots(data, left=5, right=5):
    highs = data["High"].values
    lows  = data["Low"].values
    n     = len(highs)
    pivots = []
    for i in range(left, n - right):
        is_h = all(highs[i] >= highs[i-j] for j in range(1, left+1)) and \
               all(highs[i] >= highs[i+j] for j in range(1, right+1))
        is_l = all(lows[i]  <= lows[i-j]  for j in range(1, left+1)) and \
               all(lows[i]  <= lows[i+j]  for j in range(1, right+1))
        if is_h: pivots.append(Pivot(i, float(highs[i]), "high"))
        elif is_l: pivots.append(Pivot(i, float(lows[i]), "low"))
    cleaned = []
    for p in pivots:
        if cleaned and cleaned[-1].kind == p.kind:
            if p.kind=="high" and p.price > cleaned[-1].price: cleaned[-1] = p
            elif p.kind=="low" and p.price < cleaned[-1].price: cleaned[-1] = p
        else:
            cleaned.append(p)
    return cleaned

FIB_RETRACE = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

def fib_levels(swing_low, swing_high):
    diff = swing_high - swing_low
    levels = {}
    for f in FIB_RETRACE:
        levels[f"{f*100:.1f}%"] = swing_high - diff * f
    for f in [1.272, 1.414, 1.618, 2.0, 2.618]:
        levels[f"{f:.3f}x"] = swing_low + diff * f
    return levels

def elliott_wave(data, pivots):
    if len(pivots) < 4: return None
    recent = pivots[-6:]
    def try_bull(pts):
        if len(pts) < 4: return None
        kinds = [p.kind for p in pts]
        if kinds != ["low","high","low","high","low","high"][:len(pts)]: return None
        w1 = pts[1].price - pts[0].price
        if w1 <= 0: return None
        base = pts[2].price if len(pts) > 2 else pts[0].price
        ext = {"1.618": base + 1.618*w1, "2.618": base + 2.618*w1}
        return ElliottWave(pts, ["0","i","ii","iii","iv","v"][:len(pts)], ext, len(pts)>=6)
    def try_bear(pts):
        if len(pts) < 4: return None
        kinds = [p.kind for p in pts]
        if kinds != ["high","low","high","low","high","low"][:len(pts)]: return None
        w1 = pts[0].price - pts[1].price
        if w1 <= 0: return None
        ext = {"1.618": pts[0].price - 1.618*w1, "2.618": pts[0].price - 2.618*w1}
        return ElliottWave(pts, ["0","i","ii","iii","iv","v"][:len(pts)], ext, len(pts)>=6)
    for size in [6, 5, 4]:
        pts = recent[-size:]
        ew = try_bull(pts) or try_bear(pts)
        if ew: return ew
    return None

def detect_patterns(data, pivots, lookback=60):
    if len(data) < 20 or len(pivots) < 4: return []
    n = len(data)
    peaks   = [p for p in pivots if p.kind=="high" and p.idx >= n-lookback]
    troughs = [p for p in pivots if p.kind=="low"  and p.idx >= n-lookback]
    detected = []
    def pct(a, b): return abs(a-b)/((a+b)/2)*100
    if len(peaks) >= 3:
        for i in range(len(peaks)-2):
            l, m, r = peaks[i], peaks[i+1], peaks[i+2]
            if m.price > l.price and m.price > r.price and pct(l.price, r.price) < 8:
                vt = [t.price for t in troughs if l.idx < t.idx < r.idx]
                nk = min(vt) if vt else m.price * 0.97
                h  = m.price - nk
                detected.append(Pattern("Head & Shoulders", max(0.3, 0.9 - pct(l.price,r.price)/20),
                    "bearish", l.idx, r.idx, [(nk,"Neckline"), (nk-h,"Target")]))
    if len(troughs) >= 3:
        for i in range(len(troughs)-2):
            l, m, r = troughs[i], troughs[i+1], troughs[i+2]
            if m.price < l.price and m.price < r.price and pct(l.price, r.price) < 8:
                pk = [p.price for p in peaks if l.idx < p.idx < r.idx]
                nk = max(pk) if pk else m.price * 1.03
                h  = nk - m.price
                detected.append(Pattern("Inv. H&S", max(0.3, 0.9 - pct(l.price,r.price)/20),
                    "bullish", l.idx, r.idx, [(nk,"Neckline"), (nk+h,"Target")]))
    if len(peaks) >= 2:
        l, r = peaks[-2], peaks[-1]
        if pct(l.price, r.price) < 3 and r.idx - l.idx >= 5:
            vt     = [t.price for t in troughs if l.idx < t.idx < r.idx]
            valley = min(vt) if vt else l.price * 0.97
            h      = l.price - valley
            detected.append(Pattern("Double Top", 0.8, "bearish", l.idx, r.idx,
                [(valley,"Neckline"), (valley-h,"Target")]))
    if len(troughs) >= 2:
        l, r = troughs[-2], troughs[-1]
        if pct(l.price, r.price) < 3 and r.idx - l.idx >= 5:
            pk  = [p.price for p in peaks if l.idx < p.idx < r.idx]
            res = max(pk) if pk else l.price * 1.03
            h   = res - l.price
            detected.append(Pattern("Double Bottom", 0.8, "bullish", l.idx, r.idx,
                [(res,"Neckline"), (res+h,"Target")]))
    seen = {}
    for p in detected:
        if p.name not in seen or p.confidence > seen[p.name].confidence:
            seen[p.name] = p
    return sorted(seen.values(), key=lambda x: -x.confidence)

# ── INDICATORS ────────────────────────────────────────────────────────────────
def calc_rsi(prices, period=14):
    d  = np.diff(prices)
    g  = np.where(d > 0, d, 0.0)
    l  = np.where(d < 0, -d, 0.0)
    ag = np.convolve(g, np.ones(period)/period, "full")[:len(g)]
    al = np.convolve(l, np.ones(period)/period, "full")[:len(l)]
    rs = np.where(al != 0, ag/al, 100.0)
    rsi= np.concatenate([[np.nan]*period, (100 - 100/(1+rs))[period-1:]])
    return rsi[:len(prices)]

def build_cross_signals(fast, slow, data):
    diff = fast.values - slow.values
    bulls, bears = [np.nan]*len(diff), [np.nan]*len(diff)
    for i in range(1, len(diff)):
        if diff[i] > 0 and diff[i-1] <= 0:
            bulls[i] = float(data["Low"].iloc[i]) * 0.996
        elif diff[i] < 0 and diff[i-1] >= 0:
            bears[i] = float(data["High"].iloc[i]) * 1.004
    return pd.Series(bulls, index=data.index), pd.Series(bears, index=data.index)

# ── CANDLESTICK PATTERN DETECTION ────────────────────────────────────────────
def detect_candle_pattern(data):
    """
    Detect single-bar reversal patterns on the last candle.
    Returns: (pattern_name, direction) or ("", "")
    """
    if len(data) < 2:
        return "", ""
    o  = float(data["Open"].iloc[-1])
    h  = float(data["High"].iloc[-1])
    l  = float(data["Low"].iloc[-1])
    c  = float(data["Close"].iloc[-1])
    o2 = float(data["Open"].iloc[-2])
    c2 = float(data["Close"].iloc[-2])
    body      = abs(c - o)
    rng       = h - l if h > l else 0.0001
    upper_wick= h - max(o, c)
    lower_wick= min(o, c) - l

    # Doji
    if body < 0.1 * rng:
        return "Doji", "neutral"
    # Hammer (bullish reversal)
    if lower_wick >= 2.0 * body and upper_wick <= 0.3 * body and c > o:
        return "Hammer", "bullish"
    # Inverted Hammer / Shooting Star
    if upper_wick >= 2.0 * body and lower_wick <= 0.3 * body:
        return ("Inv Hammer", "bullish") if c > o else ("Shooting Star", "bearish")
    # Bullish Engulfing
    if c2 < o2 and c > o and c > o2 and o < c2:
        return "Bull Engulf", "bullish"
    # Bearish Engulfing
    if c2 > o2 and c < o and o > c2 and c < o2:
        return "Bear Engulf", "bearish"
    # Marubozu (strong momentum)
    if body > 0.85 * rng:
        return ("Bull Marubozu", "bullish") if c > o else ("Bear Marubozu", "bearish")
    return "", ""

# ── NEW: WEIGHTED 11-CHECK SCORING ENGINE ────────────────────────────────────
def score_symbol_weighted(sym, name):
    """
    Lightweight scorer for auto-scan. Uses weighted EMA freshness.
    Returns candidate dict or None.
    """
    try:
        df = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 55: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open","High","Low","Close","Volume"])
        if len(df) < 55: return None
        c   = df["Close"]
        e9  = c.ewm(span=9,  adjust=False).mean()
        e21 = c.ewm(span=21, adjust=False).mean()
        s20 = c.rolling(20).mean()
        s50 = c.rolling(50).mean()
        ed_diff = (e9 - e21).values
        dd_diff = (s20 - s50).values

        # EMA freshness weighted
        score = 0
        reasons = []
        cross_age = None
        cross_dir = 0
        for i in range(1, min(6, len(ed_diff))):
            idx = len(ed_diff) - i
            if ed_diff[idx] > 0 and ed_diff[idx-1] <= 0:
                pts = 3 if i <= 2 else (2 if i <= 4 else 1)
                score += pts; cross_dir = +1; cross_age = i
                reasons.append(f"EMA 9/21 Bull Cross {i}d ago (+{pts})")
                break
            elif ed_diff[idx] < 0 and ed_diff[idx-1] >= 0:
                pts = 3 if i <= 2 else (2 if i <= 4 else 1)
                score -= pts; cross_dir = -1; cross_age = i
                reasons.append(f"EMA 9/21 Bear Cross {i}d ago (-{pts})")
                break

        e9l = float(e9.iloc[-1]); e21l = float(e21.iloc[-1])
        score += 1 if e9l > e21l else -1

        reason = reasons[0] if reasons else ("EMA Bullish" if e9l > e21l else "EMA Bearish")
        return {"sym": sym, "name": name, "score": score,
                "cross_dir": cross_dir, "cross_age": cross_age,
                "reason": reason, "close": float(c.iloc[-1])}
    except Exception:
        return None

def compute_full_score(data, weekly_data, pivots, rsi_last, hist, macd,
                       ema9, ema21, sma20, sma50, bb_upper, bb_lower, bb_pct,
                       last_close, vol_s, vol_ma20):
    """
    11-check weighted scoring engine. Max = 20 pts.
    Returns: (checks_list, score, signal_text, sig_col)
    Each check: (label, raw_score, weight, color, detail)
    """
    checks = []  # (label, points, color, detail)

    # ── CHECK 1: EMA 9/21 freshness-weighted cross (+3/+2/+1) ────────────────
    ed_diff = (ema9 - ema21).values
    cross_age, cross_pts = None, 0
    for i in range(1, min(6, len(ed_diff))):
        idx = len(ed_diff) - i
        if ed_diff[idx] > 0 and ed_diff[idx-1] <= 0:
            cross_pts = 3 if i <= 2 else (2 if i <= 4 else 1)
            cross_age = i
            checks.append((f"EMA 9/21 Bull Cross  {i}d ago", +cross_pts, TV_GREEN, f"Fresh={i}d → +{cross_pts}pts"))
            break
        elif ed_diff[idx] < 0 and ed_diff[idx-1] >= 0:
            cross_pts = -(3 if i <= 2 else (2 if i <= 4 else 1))
            cross_age = i
            checks.append((f"EMA 9/21 Bear Cross  {i}d ago", cross_pts, TV_RED, f"Fresh={i}d → {cross_pts}pts"))
            break
    if cross_age is None:
        e9l = float(ema9.iloc[-1]); e21l = float(ema21.iloc[-1])
        pts = +1 if e9l > e21l else -1
        col = TV_GREEN if pts > 0 else TV_RED
        checks.append(("EMA 9 > EMA 21" if pts > 0 else "EMA 9 < EMA 21", pts, col, "No fresh cross"))

    # ── CHECK 2: SMA 20/50 alignment + slope (+2) ─────────────────────────────
    s20l = float(sma20.dropna().iloc[-1]) if sma20.dropna().shape[0] > 0 else last_close
    s50l = float(sma50.dropna().iloc[-1]) if sma50.dropna().shape[0] > 0 else last_close
    sma_align = s20l > s50l
    sma_slope = float(sma20.iloc[-1]) - float(sma20.iloc[-3]) if len(sma20) >= 3 else 0
    sma_pts   = 2 if (sma_align and sma_slope > 0) else (-2 if (not sma_align and sma_slope < 0) else (1 if sma_align else -1))
    sma_col   = TV_GREEN if sma_pts > 0 else TV_RED
    checks.append((f"SMA 20{'>'if sma_align else '<'}SMA 50  slope={'↑' if sma_slope>0 else '↓'}",
        sma_pts, sma_col, f"Slope {sma_slope:+.1f}"))

    # ── CHECK 3: MACD slope-aware (+2 expand, +1 pos-flat, -1 neg, -2 shrink) ──
    hist_vals = hist.dropna().values
    if len(hist_vals) >= 3:
        h0, h1, h2 = hist_vals[-1], hist_vals[-2], hist_vals[-3]
        expanding  = (h0 > h1 > h2 and h0 > 0) or (h0 < h1 < h2 and h0 < 0)
        macd_pts   = (2 if h0 > 0 else -2) if expanding else (1 if h0 > 0 else (-1 if h0 < 0 else 0))
    else:
        h0 = float(hist.dropna().iloc[-1]) if hist.dropna().shape[0] > 0 else 0
        macd_pts = 1 if h0 > 0 else (-1 if h0 < 0 else 0)
    macd_col = TV_GREEN if macd_pts > 0 else (TV_RED if macd_pts < 0 else TEXT_SEC)
    macd_lbl  = "MACD Expanding +" if macd_pts == 2 else \
                "MACD Flat +"      if macd_pts == 1 else \
                "MACD Flat -"      if macd_pts == -1 else "MACD Expanding -"
    checks.append((macd_lbl, macd_pts, macd_col, f"Hist {h0:+.3f}"))

    # ── CHECK 4: RSI zone + momentum direction (+2 max) ───────────────────────
    rsi_vals  = calc_rsi(data["Close"].values)
    rsi_slope = rsi_vals[-1] - rsi_vals[-3] if len(rsi_vals) >= 3 else 0
    if rsi_last > 60:
        rsi_pts = 2 if rsi_slope > 0 else 1
        rsi_lbl = f"RSI {rsi_last:.0f} Bullish {'↑' if rsi_slope>0 else '→'}"
        rsi_col = TV_GREEN
    elif rsi_last < 40:
        rsi_pts = -2 if rsi_slope < 0 else -1
        rsi_lbl = f"RSI {rsi_last:.0f} Bearish {'↓' if rsi_slope<0 else '→'}"
        rsi_col = TV_RED
    else:
        rsi_pts = 0
        rsi_lbl = f"RSI {rsi_last:.0f} Neutral"
        rsi_col = TEXT_SEC
    checks.append((rsi_lbl, rsi_pts, rsi_col, f"Slope {rsi_slope:+.1f}"))

    # ── CHECK 5: S/R Proximity (+3 within 1.5%, 0 mid-range) ─────────────────
    sr_pts = 0; sr_lbl = "Mid-Range (no S/R nearby)"; sr_col = TEXT_SEC
    if len(pivots) >= 2:
        pivot_prices = sorted(set(round(p.price, 2) for p in pivots[-20:]))
        nearest_dist = min(abs(last_close - pp) / last_close * 100 for pp in pivot_prices)
        nearest_p    = min(pivot_prices, key=lambda pp: abs(last_close - pp))
        above_sr     = last_close > nearest_p
        if nearest_dist <= 1.5:
            # Price at key level — is it support or resistance?
            if above_sr:
                sr_pts = +3; sr_lbl = f"At S/R Support ₹{nearest_p:,.0f} ({nearest_dist:.1f}%)"; sr_col = TV_GREEN
            else:
                sr_pts = -3; sr_lbl = f"At S/R Resist  ₹{nearest_p:,.0f} ({nearest_dist:.1f}%)"; sr_col = TV_RED
        else:
            sr_pts = 0; sr_lbl = f"Mid-Range  Δ{nearest_dist:.1f}% from ₹{nearest_p:,.0f}"; sr_col = TEXT_SEC
    checks.append((sr_lbl, sr_pts, sr_col, "S/R proximity check"))

    # ── CHECK 6: Weekly trend alignment (+3 aligned, -2 opposed) ─────────────
    wk_pts = 0; wk_lbl = "Weekly: No data"; wk_col = TEXT_SEC
    if weekly_data is not None and len(weekly_data) >= 10:
        wc    = weekly_data["Close"]
        we21  = wc.ewm(span=21, adjust=False).mean()
        ws50  = wc.rolling(10).mean()
        wltp  = float(wc.iloc[-1])
        we21l = float(we21.iloc[-1])
        ws50l = float(ws50.iloc[-1])
        w_bull = wltp > we21l and we21l > ws50l
        w_bear = wltp < we21l and we21l < ws50l
        e9l   = float(ema9.iloc[-1]); e21l = float(ema21.iloc[-1])
        d_bull = last_close > e21l
        if w_bull and d_bull:
            wk_pts = +3; wk_lbl = "Weekly BULLISH  aligned ✓"; wk_col = TV_GREEN
        elif w_bear and not d_bull:
            wk_pts = -3 ; wk_lbl = "Weekly BEARISH  aligned ✓"; wk_col = TV_RED  # Bearish confirmed
            wk_pts = -2  # team spec: -2 for opposed, -3 when fully bearish on daily too — keep -2
            wk_pts = -3
        elif w_bull and not d_bull:
            wk_pts = -2; wk_lbl = "Weekly Bull | Daily Bear — CAUTION"; wk_col = TV_AMBER
        elif w_bear and d_bull:
            wk_pts = -2; wk_lbl = "Weekly Bear | Daily Bull — CAUTION"; wk_col = TV_AMBER
        else:
            wk_pts = 0; wk_lbl = "Weekly SIDEWAYS"; wk_col = TEXT_SEC
    checks.append((wk_lbl, wk_pts, wk_col, "Weekly EMA21/SMA50 trend"))

    # ── CHECK 7: Candlestick confirmation (+1) ────────────────────────────────
    candle_name, candle_dir = detect_candle_pattern(data)
    e9l = float(ema9.iloc[-1]); e21l_c = float(ema21.iloc[-1])
    signal_dir = "bullish" if last_close > e21l_c else "bearish"
    if candle_name:
        if candle_dir == signal_dir:
            ck_pts = +1; ck_col = TV_GREEN
            ck_lbl = f"Candle: {candle_name}  confirms {candle_dir.upper()}"
        elif candle_dir == "neutral":
            ck_pts = 0; ck_col = TEXT_SEC
            ck_lbl = f"Candle: {candle_name}  (Doji — indecision)"
        else:
            ck_pts = -1; ck_col = TV_RED
            ck_lbl = f"Candle: {candle_name}  OPPOSES signal ⚠"
    else:
        ck_pts = 0; ck_col = TEXT_SEC
        ck_lbl = "Candle: No clear pattern"
    checks.append((ck_lbl, ck_pts, ck_col, f"Bar: {candle_name or 'plain'}"))

    # ── CHECK 8: ADX strength ≥ 28 (+1) ──────────────────────────────────────
    try:
        high_v = data["High"].values; low_v = data["Low"].values; close_v = data["Close"].values
        tr = np.maximum(high_v[1:]-low_v[1:], np.maximum(np.abs(high_v[1:]-close_v[:-1]),
                                                           np.abs(low_v[1:]-close_v[:-1])))
        dm_p = np.where((high_v[1:]-high_v[:-1]) > (low_v[:-1]-low_v[1:]),
                        np.maximum(high_v[1:]-high_v[:-1], 0.0), 0.0)
        dm_n = np.where((low_v[:-1]-low_v[1:]) > (high_v[1:]-high_v[:-1]),
                        np.maximum(low_v[:-1]-low_v[1:], 0.0), 0.0)
        p=14
        tr14 = pd.Series(tr).rolling(p).mean().values
        dp14 = pd.Series(dm_p).rolling(p).mean().values
        dn14 = pd.Series(dm_n).rolling(p).mean().values
        dip  = np.where(tr14>0, 100*dp14/tr14, 0.0)
        din  = np.where(tr14>0, 100*dn14/tr14, 0.0)
        dx   = np.where((dip+din)>0, 100*np.abs(dip-din)/(dip+din), 0.0)
        adx_val = float(pd.Series(dx).rolling(p).mean().iloc[-1])
    except Exception:
        adx_val = 20.0
    adx_pts = +1 if adx_val >= 28 else -1
    adx_col = TV_GREEN if adx_val >= 28 else TEXT_SEC
    checks.append((f"ADX {adx_val:.1f}  {'Strong trend ≥28' if adx_val>=28 else 'Weak trend <28'}",
        adx_pts, adx_col, f"ADX={adx_val:.1f}"))

    # ── CHECK 9: Volume confirmation ≥ 2× avg (+1) ───────────────────────────
    lv = float(vol_s.iloc[-1])
    av = float(vol_ma20.dropna().iloc[-1]) if vol_ma20.dropna().shape[0] > 0 else lv
    vr = lv / av if av > 0 else 1.0
    vol_pts = +1 if vr >= 2.0 else (-1 if vr < 0.5 else 0)
    vol_col = TV_GREEN if vol_pts > 0 else (TV_RED if vol_pts < 0 else TEXT_SEC)
    vol_lbl = f"Volume {vr:.1f}×avg  {'Surge ✓' if vr>=2 else ('Low ×' if vr<0.5 else 'Normal')}"
    checks.append((vol_lbl, vol_pts, vol_col, f"Vol ratio {vr:.2f}"))

    # ── CHECK 10: BB squeeze breakout (+1) ───────────────────────────────────
    bw     = (bb_upper - bb_lower) / bb_lower.replace(0, 1)       # bandwidth
    bw_avg = bw.rolling(20).mean()
    bw_now = float(bw.dropna().iloc[-1])   if bw.dropna().shape[0]   > 0 else 0.1
    bw_mean= float(bw_avg.dropna().iloc[-1]) if bw_avg.dropna().shape[0] > 0 else 0.1
    in_squeeze   = bw_now < 0.75 * bw_mean
    bb_pct_now   = float(bb_pct.dropna().iloc[-1]) if bb_pct.dropna().shape[0] > 0 else 0.5
    breakout_up  = in_squeeze and bb_pct_now > 0.8
    breakout_dn  = in_squeeze and bb_pct_now < 0.2
    if breakout_up:
        bb_pts = +1; bb_lbl = "BB Squeeze Breakout UP ✓"; bb_col = TV_GREEN
    elif breakout_dn:
        bb_pts = -1; bb_lbl = "BB Squeeze Breakout DN ✓"; bb_col = TV_RED
    elif in_squeeze:
        bb_pts = 0; bb_lbl = f"BB Squeeze — no break yet"; bb_col = TV_YELLOW
    elif bb_pct_now > 0.85:
        bb_pts = -1; bb_lbl = f"BB Overbought ({bb_pct_now*100:.0f}%B)"; bb_col = TV_RED
    elif bb_pct_now < 0.15:
        bb_pts = +1; bb_lbl = f"BB Oversold ({bb_pct_now*100:.0f}%B)"; bb_col = TV_GREEN
    else:
        bb_pts = 0; bb_lbl = f"BB Mid-range ({bb_pct_now*100:.0f}%B)"; bb_col = TEXT_SEC
    checks.append((bb_lbl, bb_pts, bb_col, f"BW={bw_now:.3f} avg={bw_mean:.3f}"))

    # ── CHECK 11: Higher High / Higher Low structure (+2 / -2) ───────────────
    if len(pivots) >= 4:
        recent_highs = [p.price for p in pivots[-8:] if p.kind == "high"]
        recent_lows  = [p.price for p in pivots[-8:] if p.kind == "low"]
        hh = len(recent_highs) >= 2 and recent_highs[-1] > recent_highs[-2]
        hl = len(recent_lows)  >= 2 and recent_lows[-1]  > recent_lows[-2]
        lh = len(recent_highs) >= 2 and recent_highs[-1] < recent_highs[-2]
        ll = len(recent_lows)  >= 2 and recent_lows[-1]  < recent_lows[-2]
        if hh and hl:
            hhhl_pts = +2; hhhl_lbl = "HH + HL Structure  Bullish ✓"; hhhl_col = TV_GREEN
        elif lh and ll:
            hhhl_pts = -2; hhhl_lbl = "LH + LL Structure  Bearish ✓"; hhhl_col = TV_RED
        elif hh or hl:
            hhhl_pts = +1; hhhl_lbl = f"Partial Bull: {'HH' if hh else 'HL'}"; hhhl_col = TV_GREEN
        elif lh or ll:
            hhhl_pts = -1; hhhl_lbl = f"Partial Bear: {'LH' if lh else 'LL'}"; hhhl_col = TV_RED
        else:
            hhhl_pts = 0; hhhl_lbl = "HH/HL: Mixed / Sideways"; hhhl_col = TEXT_SEC
    else:
        hhhl_pts = 0; hhhl_lbl = "HH/HL: Insufficient pivots"; hhhl_col = TEXT_SEC
    checks.append((hhhl_lbl, hhhl_pts, hhhl_col, "Price structure"))

    # ── FINAL SCORE ───────────────────────────────────────────────────────────
    score     = sum(pts for _, pts, _, _ in checks)
    max_score = 20   # theoretical max from all checks combined

    # Signal tiers (team agreement)
    if   score >= 16: signal_text, sig_col = "STRONG BUY",  "#00E676"
    elif score >= 12: signal_text, sig_col = "BUY",          TV_GREEN
    elif score <= -12:signal_text, sig_col = "STRONG SELL",  "#FF1744"
    elif score <= -7: signal_text, sig_col = "SELL",          TV_RED
    else:             signal_text, sig_col = "WAIT",          TV_YELLOW  # No-Trade Zone

    return checks, score, max_score, signal_text, sig_col

# ── DRAWING ───────────────────────────────────────────────────────────────────
def draw_fib_levels(ax, lo, hi, n):
    diff = hi - lo
    levels = [(0.0,"#787B86","--"),(0.236,"#FF9800",":"),(0.382,"#FF6D00",":"),
              (0.5,"#9C27B0",":"),(0.618,"#26A69A","-"),(0.786,"#2962FF",":"),(1.0,"#787B86","--")]
    for frac, col, ls in levels:
        price = hi - diff * frac
        ax.axhline(price, color=col, linestyle=ls, linewidth=0.7, alpha=0.55, zorder=2)
        ax.text(n * 0.003, price, f" {frac*100:.1f}% ({price:,.0f})",
            color=col, fontsize=6, va="center", fontweight="bold",
            transform=ax.get_yaxis_transform(), zorder=6)
    for frac, col in [(1.618, TV_GREEN), (2.618, TV_RED)]:
        price = lo + diff * frac
        ax.axhline(price, color=col, linestyle="--", linewidth=0.8, alpha=0.6, zorder=2)
        ax.text(n * 0.003, price, f" {frac:.3f}x ({price:,.0f})",
            color=col, fontsize=6, va="center", fontweight="bold",
            transform=ax.get_yaxis_transform(), zorder=6)

def draw_ew_labels(ax, ew, data):
    if not ew: return
    xs = [p.idx for p in ew.pivots]; ys = [p.price for p in ew.pivots]
    ax.plot(xs, ys, color=TV_BLUE, lw=1.0, ls="--", alpha=0.5, zorder=9)
    for pivot, label in zip(ew.pivots, ew.labels):
        offset = float(data["Close"].values[-1]) * 0.013
        y_txt  = pivot.price + offset if pivot.kind=="high" else pivot.price - offset
        ax.annotate(label, xy=(pivot.idx, pivot.price), xytext=(pivot.idx, y_txt),
            fontsize=8, color=TV_BLUE, fontweight="bold", ha="center",
            va="bottom" if pivot.kind=="low" else "top", zorder=10,
            bbox=dict(boxstyle="circle,pad=0.25", fc=BG_DARK, ec=TV_BLUE, lw=1.2, alpha=0.95))

def draw_pattern(ax, pattern, data):
    if not pattern: return
    si = max(0, pattern.start_idx)
    ei = min(len(data)-1, pattern.end_idx)
    col = TV_GREEN if pattern.direction=="bullish" else (TV_RED if pattern.direction=="bearish" else TEXT_SEC)
    ax.axvspan(si, ei, alpha=0.06, color=col, zorder=1)
    for price, lbl in pattern.key_levels[:2]:
        ax.axhline(price, color=col, ls="--", lw=0.8, alpha=0.7, zorder=3)
    mid   = (si + ei) / 2
    sl    = data["High"].values[si:ei+1] if ei > si else data["High"].values[si:si+2]
    y_pos = float(np.max(sl)) * 1.003 if pattern.direction != "bearish" else \
            float(np.min(data["Low"].values[si:ei+1])) * 0.997
    ax.text(mid, y_pos, f"  {pattern.name}", color=col, fontsize=7, ha="center",
        va="bottom" if pattern.direction != "bearish" else "top",
        fontweight="bold", alpha=0.9, zorder=8,
        bbox=dict(boxstyle="round,pad=0.25", fc=BG_DARK, ec=col, alpha=0.92, lw=0.9))

# ── MAIN ─────────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    import mplfinance as mpf
    # Fix 9: Use curl_cffi session to bypass Render's yfinance 403 restriction
    # curl_cffi impersonates a browser — works on restricted networks
    try:
        from curl_cffi import requests as curl_requests
        _curl_session = curl_requests.Session(impersonate="chrome110")
        yf.utils.requests = _curl_session
        # Also patch the yfinance download session
        import yfinance.utils
        yfinance.utils.requests = _curl_session
    except Exception as _ce:
        pass  # curl_cffi unavailable — will use standard requests (may 403 on Render)
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}", file=sys.stderr); sys.exit(1)

import time as _time

OUT_DIR = "output"
os.makedirs(OUT_DIR, exist_ok=True)

def _cleanup_old_charts(directory, max_age_sec=7200):
    try:
        now = _time.time()
        for fname in os.listdir(directory):
            if fname.endswith(".png") and fname.startswith("chart_"):
                fpath = os.path.join(directory, fname)
                if now - os.path.getmtime(fpath) > max_age_sec:
                    os.remove(fpath)
    except Exception:
        pass

_cleanup_old_charts(OUT_DIR)

# Universe
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from nifty500_collector import SECTOR_STOCKS as _SS
    _all = sorted({s for v in _SS.values() for s in v})
    UNIVERSE = [(f"{s}.NS", s.title()) for s in _all]
    print(f"SCAN: {len(UNIVERSE)} stocks from nifty500_collector")
except Exception:
    UNIVERSE = [
        ("RELIANCE.NS","Reliance Industries"),("TCS.NS","TCS"),
        ("HDFCBANK.NS","HDFC Bank"),("INFY.NS","Infosys"),
        ("ICICIBANK.NS","ICICI Bank"),("SBIN.NS","SBI"),
        ("BHARTIARTL.NS","Airtel"),("ITC.NS","ITC"),
        ("KOTAKBANK.NS","Kotak Bank"),("LT.NS","L&T"),
        ("AXISBANK.NS","Axis Bank"),("BAJFINANCE.NS","Bajaj Finance"),
        ("SUNPHARMA.NS","Sun Pharma"),("MARUTI.NS","Maruti"),
        ("TITAN.NS","Titan"),("WIPRO.NS","Wipro"),
        ("HCLTECH.NS","HCL Tech"),("ADANIENT.NS","Adani Ent"),
        ("NTPC.NS","NTPC"),("TATAMOTORS.NS","Tata Motors"),
    ]

# ── ARGUMENT PARSING ─────────────────────────────────────────────────────────
VALID_PERIODS = {"1mo","3mo","6mo","1y","2y"}
CHART_PERIOD  = "6mo"
winner        = None

if len(sys.argv) >= 3:
    forced_sym  = sys.argv[1]
    forced_name = sys.argv[2]
    if len(sys.argv) >= 4 and sys.argv[3] in VALID_PERIODS:
        CHART_PERIOD = sys.argv[3]
    winner = {"sym": forced_sym, "name": forced_name, "score": 0, "reason": "Manual pick"}
    print(f"SCAN: skipped (manual) period={CHART_PERIOD}")
else:
    import time
    candidates = []; checked = 0
    for i in range(0, len(UNIVERSE), 10):
        batch = UNIVERSE[i:i+10]
        for sym, name in batch:
            r = score_symbol_weighted(sym, name); checked += 1
            if r and r.get("cross_dir") != 0: candidates.append(r)
        time.sleep(0.4)
        if len(candidates) >= 12: break
    if not candidates:
        for sym, name in UNIVERSE:
            r = score_symbol_weighted(sym, name); checked += 1
            if r: candidates.append(r)
    print(f"SCAN: checked {checked}, candidates {len(candidates)}")
    if not candidates:
        print("[ERROR] No candidates", file=sys.stderr); sys.exit(1)
    candidates.sort(key=lambda r: -r["score"])
    winner = candidates[0]
    print(f"WINNER: {winner['sym']}|{winner['name']}|{winner['reason']}|{winner['score']:+d}")
    forced_sym = winner["sym"]; forced_name = winner["name"]

symbol       = forced_sym
company_name = forced_name
_sym_safe    = symbol.replace(".NS","").replace(".BO","").upper()
OUT_FILE     = os.path.join(OUT_DIR, f"chart_{_sym_safe}_{int(_time.time())}.png")

# ── DATA DOWNLOAD ─────────────────────────────────────────────────────────────
data = yf.download(symbol, period=CHART_PERIOD, interval="1d", progress=False, auto_adjust=True)
if data.empty:
    print(f"[ERROR] No data for {symbol}", file=sys.stderr); sys.exit(1)
if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
data = data.dropna(subset=["Open","High","Low","Close","Volume"])

# Weekly data for trend alignment check
try:
    weekly_data = yf.download(symbol, period="1y", interval="1wk", progress=False, auto_adjust=True)
    if isinstance(weekly_data.columns, pd.MultiIndex):
        weekly_data.columns = weekly_data.columns.get_level_values(0)
    weekly_data = weekly_data.dropna(subset=["Close"])
    if len(weekly_data) < 5: weekly_data = None
except Exception:
    weekly_data = None

# Fetch real company name + sector
sector_name = ""
try:
    _ti  = yf.Ticker(symbol).info
    _fetched = _ti.get("longName") or _ti.get("shortName") or company_name
    if _fetched and len(_fetched) > 2:
        company_name = _fetched
    sector_name = _ti.get("sector") or _ti.get("industry") or ""
    _mcap_raw   = _ti.get("marketCap")
    _mcap_str   = ""
    if _mcap_raw:
        cr = _mcap_raw / 1e7
        _mcap_str = f"₹{cr/1000:.1f}K Cr" if cr > 1000 else f"₹{cr:.0f} Cr"
    _pe_str = f"PE {_ti.get('trailingPE',0):.1f}" if _ti.get("trailingPE") else ""
    _52h    = _ti.get("fiftyTwoWeekHigh")
    _52l    = _ti.get("fiftyTwoWeekLow")
except Exception:
    _mcap_str = ""; _pe_str = ""; _52h = None; _52l = None

# ── INDICATORS ────────────────────────────────────────────────────────────────
close_s  = data["Close"]; vol_s = data["Volume"]
close    = close_s.values; n = len(close)
ema9     = close_s.ewm(span=9,  adjust=False).mean()
ema21    = close_s.ewm(span=21, adjust=False).mean()
ema50    = close_s.ewm(span=50, adjust=False).mean()
sma20    = close_s.rolling(20).mean()
sma50    = close_s.rolling(50).mean()
ema12    = close_s.ewm(span=12, adjust=False).mean()
ema26    = close_s.ewm(span=26, adjust=False).mean()
macd     = ema12 - ema26
macd_sig = macd.ewm(span=9, adjust=False).mean()
hist     = macd - macd_sig
vol_ma20 = vol_s.rolling(20).mean()

bb_mid   = sma20
bb_std   = close_s.rolling(20).std()
bb_upper = bb_mid + 2 * bb_std
bb_lower = bb_mid - 2 * bb_std
bb_pct   = ((close_s - bb_lower) / (bb_upper - bb_lower)).clip(0, 1)

rsi_s    = pd.Series(calc_rsi(close)[:n], index=data.index)

eb_s, er_s = build_cross_signals(ema9,  ema21, data)
db_s, dr_s = build_cross_signals(sma20, sma50, data)

# Key scalars
recent     = data.tail(20)
support    = float(recent["Low"].min())
resistance = float(recent["High"].max())
last_close = float(close[-1])
prev_close = float(close[-2]) if n >= 2 else last_close
change_pct = (last_close - prev_close) / prev_close * 100
e9l  = float(ema9.iloc[-1]); e21l = float(ema21.iloc[-1]); e50l = float(ema50.iloc[-1])
rsi_last  = float(rsi_s.dropna().iloc[-1])
lv = float(vol_s.iloc[-1])
av = float(vol_ma20.dropna().iloc[-1]) if vol_ma20.dropna().shape[0] > 0 else lv
vr = lv / av if av > 0 else 1.0
_bb_u = float(bb_upper.dropna().iloc[-1]); _bb_l = float(bb_lower.dropna().iloc[-1])
s20l = float(sma20.dropna().iloc[-1]) if sma20.dropna().shape[0] > 0 else e9l
s50l = float(sma50.dropna().iloc[-1]) if sma50.dropna().shape[0] > 0 else e21l

_h = data["High"].values; _l = data["Low"].values; _c = data["Close"].values
_tr= np.maximum(_h[1:]-_l[1:], np.maximum(np.abs(_h[1:]-_c[:-1]), np.abs(_l[1:]-_c[:-1])))
atr_val = round(float(np.mean(_tr[-14:])), 2) if len(_tr) >= 14 else round(float(np.mean(_tr)), 2)

hist_n = min(252, n)
if _52h is None: _52h = float(data["High"].rolling(hist_n).max().iloc[-1])
if _52l is None: _52l = float(data["Low"].rolling(hist_n).min().iloc[-1])
_52w_pct = round((last_close - _52l) / (_52h - _52l) * 100, 1) if _52h != _52l else 50.0

# ── 11-CHECK WEIGHTED SCORING ─────────────────────────────────────────────────
pivots = find_pivots(data, left=5, right=5)
checks, score, max_score, signal_text, sig_col = compute_full_score(
    data, weekly_data, pivots, rsi_last, hist, macd,
    ema9, ema21, sma20, sma50, bb_upper, bb_lower, bb_pct,
    last_close, vol_s, vol_ma20
)
score_str = f"{score:+d}/{max_score}"
is_bull   = score >= 0
is_wait   = signal_text == "WAIT"

# ── TARGETS (only shown when NOT in WAIT zone) ────────────────────────────────
if not is_wait:
    if is_bull:
        sl_val = min(float(data["Low"].tail(5).min()), e21l) * 0.997
        sl_pct = (last_close - sl_val) / last_close * 100
        t1_val = last_close + 1.5 * (last_close - sl_val)
        t2_val = last_close + 2.5 * (last_close - sl_val)
        t1_pct = (t1_val - last_close) / last_close * 100
        t2_pct = (t2_val - last_close) / last_close * 100
    else:
        sl_val = max(float(data["High"].tail(5).max()), e21l) * 1.003
        sl_pct = (sl_val - last_close) / last_close * 100
        t1_val = last_close - 1.5 * (sl_val - last_close)
        t2_val = last_close - 2.5 * (sl_val - last_close)
        t1_pct = (last_close - t1_val) / last_close * 100
        t2_pct = (last_close - t2_val) / last_close * 100
    _risk = abs(last_close - sl_val)
    _rr1  = round(abs(t1_val - last_close) / _risk, 1) if _risk > 0 else 0
    _rr2  = round(abs(t2_val - last_close) / _risk, 1) if _risk > 0 else 0
else:
    sl_val = t1_val = t2_val = 0.0
    sl_pct = t1_pct = t2_pct = _rr1 = _rr2 = 0.0

# ── MPLFINANCE STYLE ──────────────────────────────────────────────────────────
mc = mpf.make_marketcolors(
    up=TV_GREEN, down=TV_RED,
    wick={"up": TV_GREEN, "down": TV_RED},
    edge={"up": TV_GREEN, "down": TV_RED},
    volume={"up": "#0D4842", "down": "#4C1B1B"},
)
tv_style = mpf.make_mpf_style(
    marketcolors=mc,
    facecolor=BG_DARK, edgecolor=BORDER_COL,
    figcolor=BG_DARK, gridcolor="#1E222D",
    gridstyle="-",
    rc={
        "axes.labelcolor": TEXT_SEC,
        "xtick.color": TEXT_SEC, "ytick.color": TEXT_SEC,
        "text.color": TEXT_PRI,
        "font.family": "DejaVu Sans", "font.size": 8.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.spines.bottom": False,
        "xtick.bottom": False, "ytick.left": False,
    }
)

hc = [TV_GREEN if v >= 0 else TV_RED for v in hist.fillna(0)]

from datetime import datetime
today    = datetime.now().strftime("%d %b %Y")
ticker   = symbol.replace(".NS","").replace(".BO","")
chg_icon = "▲" if change_pct >= 0 else "▼"
title    = f"{ticker}  |  {company_name}  |  ₹{last_close:,.2f}  {chg_icon} {change_pct:+.2f}%  |  {today}"

apds = [
    mpf.make_addplot(ema9,    color=TV_ORANGE, width=1.5),
    mpf.make_addplot(ema21,   color=TV_BLUE,   width=1.5),
    mpf.make_addplot(ema50,   color=TV_GOLD,   width=1.2, linestyle="--"),
    mpf.make_addplot(sma20,   color=TV_VIOLET, width=1.0, linestyle="--"),
    mpf.make_addplot(bb_upper,color=TV_TEAL,   width=0.8, linestyle="--", alpha=0.65),
    mpf.make_addplot(bb_lower,color=TV_TEAL,   width=0.8, linestyle="--", alpha=0.65),
    mpf.make_addplot([support]*n,    color=TV_GREEN, linestyle=":", width=0.8, alpha=0.7),
    mpf.make_addplot([resistance]*n, color=TV_RED,   linestyle=":", width=0.8, alpha=0.7),
    mpf.make_addplot(macd,     panel=2, color=TV_BLUE,   width=1.1, ylabel="MACD"),
    mpf.make_addplot(macd_sig, panel=2, color=TV_AMBER,  width=1.1),
    mpf.make_addplot([0]*n,    panel=2, color=BORDER_COL, linestyle="--", width=0.7),
    mpf.make_addplot(hist,     panel=2, type="bar", color=hc, alpha=0.6),
    mpf.make_addplot(rsi_s,    panel=3, color=TV_PURPLE, width=1.3, ylabel="RSI"),
    mpf.make_addplot([70]*n,   panel=3, color="#EF535055", linestyle="--", width=0.7),
    mpf.make_addplot([30]*n,   panel=3, color="#26A69A55", linestyle="--", width=0.7),
    mpf.make_addplot([50]*n,   panel=3, color="#55555580", linestyle=":",  width=0.6),
]

# ── RENDER ────────────────────────────────────────────────────────────────────
try:
    fig, axes = mpf.plot(
        data, type="candle", style=tv_style, addplot=apds,
        title="", figratio=(18, 11), figscale=1.25,
        volume=True, panel_ratios=(5, 1.2, 2, 1.8),
        returnfig=True, tight_layout=False,
    )

    _all_axes = fig.get_axes()
    ax0 = _all_axes[0]
    # Fix 10: mplfinance creates twin-axes for some panels.
    # Filter by vertical position (y-position) to get unique panel axes.
    # Axes are ordered top→bottom in mplfinance output:
    # [0]=price, [1]=volume, [2]=MACD, [3]=RSI (plus possible twins)
    _unique = []
    _seen_pos = set()
    for _ax in _all_axes:
        _ypos = round(_ax.get_position().y0, 3)
        if _ypos not in _seen_pos:
            _unique.append(_ax)
            _seen_pos.add(_ypos)
    _unique.sort(key=lambda ax: -ax.get_position().y0)  # top to bottom
    ax0 = _unique[0] if len(_unique) > 0 else ax0
    ax1 = _unique[1] if len(_unique) > 1 else None
    ax2 = _unique[2] if len(_unique) > 2 else None
    ax3 = _unique[3] if len(_unique) > 3 else None

    for ax in [a for a in [ax0, ax1, ax2, ax3] if a]:
        ax.set_facecolor(BG_DARK)
        ax.tick_params(colors=TEXT_SEC, labelsize=7.5)
        for spine in ax.spines.values(): spine.set_edgecolor(BORDER_COL)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    # BB fill
    _xs = np.arange(n)
    ax0.fill_between(_xs, bb_upper.values, bb_lower.values, alpha=0.06, color=TV_TEAL, zorder=1)

    # RSI shading + label
    if ax3:
        _rv = rsi_s.fillna(50).values
        ax3.fill_between(range(n), _rv, 70, where=(_rv > 70), color=TV_RED,   alpha=0.18, zorder=2)
        ax3.fill_between(range(n), _rv, 30, where=(_rv < 30), color=TV_GREEN, alpha=0.18, zorder=2)
        _rc = TV_RED if rsi_last > 70 else (TV_GREEN if rsi_last < 30 else TV_PURPLE)
        ax3.annotate(f" {rsi_last:.1f}", xy=(n-1, rsi_last), xycoords="data",
            fontsize=8, color=_rc, fontweight="bold", va="center", zorder=8,
            bbox=dict(boxstyle="round,pad=0.2", fc=BG_DARK, ec=_rc, lw=0.8, alpha=0.9))
        ax3.set_facecolor(BG_DARK)

    # Volume spikes
    _vv = vol_s.values; _vm = vol_ma20.fillna(method="bfill").values
    if ax1:
        ax1.set_facecolor(BG_DARK)
        for _vi, (_v, _vm_) in enumerate(zip(_vv, _vm)):
            if _vm_ > 0 and _v > 2.0 * _vm_:
                ax1.axvline(_vi, color=TV_AMBER, lw=1.0, alpha=0.55, zorder=4)
                ax0.axvline(_vi, color=TV_AMBER, lw=0.6, alpha=0.18, zorder=3)
                ax1.text(_vi, _v*1.03, f"{_v/_vm_:.1f}x", fontsize=5, color=TV_AMBER,
                    ha="center", va="bottom", fontweight="bold", rotation=90, zorder=6)
        if len(_vv) > 0 and _vm[-1] > 0:
            _vc = TV_AMBER if vr > 2 else (TV_GREEN if vr > 1 else TEXT_SEC)
            ax1.annotate(f" {vr:.1f}x", xy=(n-1, _vv[-1]), xycoords="data",
                fontsize=7.5, color=_vc, fontweight="bold", va="bottom", zorder=8)

    if ax2: ax2.set_facecolor(BG_DARK)

    # Elliott + patterns
    ew       = elliott_wave(data, pivots)
    patterns = detect_patterns(data, pivots, lookback=80)
    top_pat  = patterns[0] if patterns else None
    al = sorted(pivots, key=lambda p: p.price)
    ah = sorted(pivots, key=lambda p: -p.price)
    fib_lo = al[0].price if al else float(data["Low"].min())
    fib_hi = ah[0].price if ah else float(data["High"].max())
    draw_fib_levels(ax0, fib_lo, fib_hi, n)
    if ew:      draw_ew_labels(ax0, ew, data)
    if top_pat: draw_pattern(ax0, top_pat, data)

    ax0.annotate(f"S: ₹{support:,.0f}", xy=(1.002, support),
        xycoords=("axes fraction","data"), color=TV_GREEN, fontsize=7, va="center", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.2", fc=TV_GREEN_L, ec=TV_GREEN, lw=0.8, alpha=0.92))
    ax0.annotate(f"R: ₹{resistance:,.0f}", xy=(1.002, resistance),
        xycoords=("axes fraction","data"), color=TV_RED, fontsize=7, va="center", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.2", fc=TV_RED_L, ec=TV_RED, lw=0.8, alpha=0.92))

    # Legend
    leg = [
        mlines.Line2D([],[],color=TV_ORANGE,lw=1.8,label=f"EMA 9 ({e9l:,.0f})"),
        mlines.Line2D([],[],color=TV_BLUE,  lw=1.8,label=f"EMA 21 ({e21l:,.0f})"),
        mlines.Line2D([],[],color=TV_GOLD,  lw=1.2,ls="--",label=f"EMA 50 ({e50l:,.0f})"),
        mlines.Line2D([],[],color=TV_VIOLET,lw=1.0,ls="--",label=f"SMA 20 ({s20l:,.0f})"),
        mlines.Line2D([],[],color=TV_TEAL,  lw=1.0,ls="--",label=f"BB({_bb_l:,.0f}–{_bb_u:,.0f})"),
        mlines.Line2D([],[],color=TV_GREEN, lw=0.8,ls=":",label=f"Sup {support:,.0f}"),
        mlines.Line2D([],[],color=TV_RED,   lw=0.8,ls=":",label=f"Res {resistance:,.0f}"),
    ]
    legend = ax0.legend(handles=leg, loc="upper left", fontsize=6.5,
        facecolor=BG_CARD, edgecolor=BORDER_COL, framealpha=0.95,
        labelcolor=TEXT_PRI, ncol=2, borderpad=0.6, handlelength=2.0)
    for text in legend.get_texts(): text.set_color(TEXT_PRI)

    # ── LAYOUT ────────────────────────────────────────────────────────────────
    fw, fh  = fig.get_size_inches()
    panel_w = 4.8
    fig.set_size_inches(fw + panel_w, fh)
    ratio   = fw / (fw + panel_w)
    for ax_i in fig.get_axes():
        p = ax_i.get_position()
        ax_i.set_position([p.x0 * ratio, p.y0, p.width * ratio, p.height])

    # ── HEADER ────────────────────────────────────────────────────────────────
    header_h = 0.055
    header   = fig.add_axes([0.0, 1.0 - header_h, ratio, header_h])
    header.set_facecolor(BG_PANEL); header.set_xlim(0,1); header.set_ylim(0,1); header.axis("off")
    header.text(0.015, 0.72, ticker, fontsize=14, fontweight="bold", color=TEXT_ACC, va="center")
    header.text(0.015, 0.25, company_name, fontsize=8, color=TEXT_SEC, va="center")
    price_col = TV_GREEN if change_pct >= 0 else TV_RED
    price_bg  = TV_GREEN_L if change_pct >= 0 else TV_RED_L
    header.add_patch(FancyBboxPatch((0.15,0.15), 0.22, 0.70,
        boxstyle="round,pad=0.02", fc=price_bg, ec=price_col, lw=1.0, zorder=5))
    header.text(0.17, 0.72, f"₹{last_close:,.2f}", fontsize=11, fontweight="bold", color=price_col, va="center")
    header.text(0.17, 0.22, f"{chg_icon} {change_pct:+.2f}%  Today", fontsize=7.5, color=price_col, va="center")
    pills = []
    if _mcap_str: pills.append(("MCap", _mcap_str))
    if _pe_str:   pills.append(("",     _pe_str))
    pills += [("ATR", f"₹{atr_val:,.1f}"), ("Vol", f"{vr:.1f}x avg"),
              ("52W%", f"{_52w_pct:.0f}%"), ("Period", CHART_PERIOD.upper()),
              ("Score", score_str)]
    px = 0.40
    for lbl, val in pills:
        txt = f"{lbl}: {val}" if lbl else val
        header.text(px, 0.5, txt, fontsize=7, color=TEXT_SEC, va="center",
            bbox=dict(boxstyle="round,pad=0.3", fc=BG_CARD, ec=BORDER_COL, lw=0.7, alpha=0.9))
        px += 0.092
        if px > 0.90: break
    if sector_name:
        header.text(0.99, 0.5, sector_name[:18], fontsize=6.5, color="#B2B5BE",
            va="center", ha="right",
            bbox=dict(boxstyle="round,pad=0.3", fc=BG_CARD, ec=BORDER_COL, lw=0.7, alpha=0.85))

    # ── RIGHT SIGNAL PANEL ────────────────────────────────────────────────────
    sa = fig.add_axes([ratio + 0.004, 0.0, 1.0 - ratio - 0.004, 1.0])
    sa.set_facecolor(BG_PANEL); sa.set_xlim(0,1); sa.set_ylim(0,1); sa.axis("off")

    sig_fg, sig_bg_c = SIGNAL_COLORS.get(signal_text, (TEXT_SEC, BG_CARD))
    sa.add_patch(FancyBboxPatch((0.03,0.02), 0.94, 0.95,
        boxstyle="round,pad=0.01", lw=1.8, ec=sig_fg, fc=BG_PANEL, zorder=1))

    # Signal header
    sa.add_patch(FancyBboxPatch((0.03,0.89), 0.94, 0.08,
        boxstyle="round,pad=0.01", lw=0, fc=sig_bg_c, zorder=3))
    sa.text(0.5, 0.985, "AUTOAI ADVISORY  v7.0", fontsize=6.5, color=TEXT_SEC,
        ha="center", va="top", style="italic", zorder=5)
    hmap = {"STRONG BUY":"⬆⬆ STRONG BUY","BUY":"⬆ BUY","WAIT":"⏸ WAIT — No Setup",
            "SELL":"⬇ SELL","STRONG SELL":"⬇⬇ STRONG SELL"}
    sa.text(0.5, 0.950, hmap.get(signal_text, signal_text), fontsize=12,
        fontweight="bold", color=sig_fg, ha="center", va="top", zorder=5)

    # Score + bar
    sa.text(0.5, 0.900, f"Score  {score_str}  (max ±20)", fontsize=8.5, color=sig_fg,
        ha="center", va="top", zorder=5)
    bar_y = 0.867; bar_h = 0.016
    bar_fill = max(0.0, min(1.0, (score + max_score) / (2 * max_score)))
    sa.add_patch(FancyBboxPatch((0.06,bar_y), 0.88, bar_h,
        boxstyle="round,pad=0.003", fc=BG_CARD, ec=BORDER_COL, lw=0.6, zorder=4))
    if bar_fill > 0:
        sa.add_patch(FancyBboxPatch((0.06,bar_y), 0.88*bar_fill, bar_h,
            boxstyle="round,pad=0.003", fc=sig_fg, ec="none", zorder=5))
    sa.text(0.5, bar_y + bar_h/2, "BEARISH ◄──────────────► BULLISH",
        fontsize=5.5, color=TEXT_SEC, ha="center", va="center", zorder=6)
    sa.plot([0.05,0.95],[0.848,0.848], color=BORDER_COL, lw=0.8, zorder=5)

    # Reason
    reason_txt = winner.get("reason","Best crossover")
    sa.text(0.5, 0.838, reason_txt[:40], fontsize=7.5, color=sig_fg,
        ha="center", va="top", fontweight="bold", zorder=5)
    sa.plot([0.05,0.95],[0.814,0.814], color=BORDER_COL, lw=0.6, zorder=5)

    # ── 11 Signal rows ────────────────────────────────────────────────────────
    # Dynamic row height based on check count
    n_checks = len(checks)  # always 11
    row_top  = 0.798
    row_gap  = (row_top - 0.20) / n_checks   # auto-fit all 11

    for i, (label, pts, col, detail) in enumerate(checks):
        y    = row_top - i * row_gap
        ic   = "●" if pts > 0 else ("○" if pts == 0 else "●")
        ic_c = col
        # Row background
        bg_c = "#26A69A12" if pts > 0 else ("#EF535012" if pts < 0 else BG_DARK+"00")
        sa.add_patch(FancyBboxPatch((0.04, y - row_gap*0.85), 0.92, row_gap*0.82,
            boxstyle="round,pad=0.003", fc=bg_c, ec="none", zorder=3))
        sa.text(0.10, y - row_gap*0.38, ic, fontsize=8, color=ic_c, va="center", ha="center", zorder=5)
        # Weight badge
        w_col = TV_GREEN if pts > 0 else (TV_RED if pts < 0 else TEXT_SEC)
        sa.add_patch(FancyBboxPatch((0.16, y - row_gap*0.70), 0.11, row_gap*0.65,
            boxstyle="round,pad=0.002", fc=BG_CARD, ec=w_col, lw=0.6, zorder=4))
        sa.text(0.215, y - row_gap*0.38, f"{pts:+d}", fontsize=6.5, color=w_col,
            va="center", ha="center", fontweight="bold", zorder=5)
        # Label
        sa.text(0.30, y - row_gap*0.38, label[:34], fontsize=6.8,
            color=TEXT_PRI if pts != 0 else TEXT_SEC, va="center", zorder=5)

    sa.plot([0.05,0.95],[0.20,0.20], color=BORDER_COL, lw=0.8, zorder=5)

    # ── TRADE LEVELS or No-Trade Zone ────────────────────────────────────────
    tl_top = 0.188; tl_gap = 0.048
    if is_wait:
        # No-Trade Zone — show no levels, just explanation
        sa.add_patch(FancyBboxPatch((0.04,0.042), 0.92, 0.145,
            boxstyle="round,pad=0.01", fc="#2A2410", ec=TV_YELLOW, lw=1.2, zorder=4))
        sa.text(0.5, 0.172, "⏸  NO TRADE ZONE", fontsize=9, fontweight="bold",
            color=TV_YELLOW, ha="center", va="top", zorder=5)
        sa.text(0.5, 0.145, f"Score {score:+d} — need ≥+12 for BUY", fontsize=7.5,
            color=TEXT_SEC, ha="center", va="top", zorder=5)
        sa.text(0.5, 0.120, "Wait for stronger confirmation:", fontsize=7,
            color=TEXT_SEC, ha="center", va="top", zorder=5)
        needs = []
        if score < 12:
            gap = 12 - score
            needs.append(f"Need +{gap} more pts for BUY signal")
        if weekly_data is not None:
            needs.append("Check weekly trend first")
        sa.text(0.5, 0.098, "\n".join(needs[:2]), fontsize=6.5, color=TV_YELLOW,
            ha="center", va="top", zorder=5)
        sa.text(0.5, 0.052, "ATR(14): ₹" + f"{atr_val:,.2f}", fontsize=7.5,
            color=TEXT_SEC, ha="center", va="top", zorder=5)
    else:
        # Full trade levels
        sa.text(0.07, tl_top + tl_gap*0.5, "ATR (14)", fontsize=7.5, color=TEXT_SEC, va="top", zorder=5)
        sa.text(0.93, tl_top + tl_gap*0.5, f"₹{atr_val:,.2f}", fontsize=7.5, color=TEXT_PRI,
            va="top", ha="right", zorder=5, fontfamily="monospace")
        trows = [
            ("Entry",     last_close, None,   None, TEXT_ACC, True),
            ("Stop Loss", sl_val,    -sl_pct, None, TV_RED,   False),
            ("Target 1",  t1_val,     t1_pct, _rr1, TV_GREEN, False),
            ("Target 2",  t2_val,     t2_pct, _rr2, TV_GREEN, False),
        ]
        for i, (lbl, price, pct, rr, col, bold) in enumerate(trows):
            y   = tl_top - i * tl_gap
            fw2 = "bold" if bold else "normal"
            if pct is not None:
                bg_c = "#EF535010" if col == TV_RED else "#26A69A10"
                sa.add_patch(FancyBboxPatch((0.04, y - 0.040), 0.92, 0.045,
                    boxstyle="round,pad=0.003", fc=bg_c, ec="none", zorder=3))
            sa.text(0.07, y - 0.006, lbl, fontsize=8, color=TEXT_SEC if not bold else TEXT_ACC,
                va="top", fontweight=fw2, zorder=5)
            sa.text(0.62, y - 0.006, f"₹{price:,.2f}", fontsize=8, color=col,
                va="top", ha="right", fontweight=fw2, zorder=5, fontfamily="monospace")
            if pct is not None:
                rr_str = f"  1:{rr}" if rr is not None else ""
                sa.text(0.64, y - 0.006, f"{pct:+.1f}%{rr_str}", fontsize=6.8,
                    color=col, va="top", zorder=5)

    # ── 52W bar ───────────────────────────────────────────────────────────────
    sa.text(0.5, 0.038, "52-WEEK RANGE", fontsize=6, color=TEXT_SEC, ha="center", va="center")
    sa.add_patch(FancyBboxPatch((0.06,0.018), 0.88, 0.014,
        boxstyle="round,pad=0.002", fc=BG_CARD, ec=BORDER_COL, lw=0.5, zorder=4))
    _52f = max(0.0, min(1.0, _52w_pct/100))
    sa.add_patch(FancyBboxPatch((0.06,0.018), 0.88*_52f, 0.014,
        boxstyle="round,pad=0.002", fc=sig_fg, ec="none", alpha=0.8, zorder=5))
    sa.text(0.06, 0.014, f"₹{_52l:,.0f}", fontsize=5.5, color=TV_RED, va="top", zorder=6)
    sa.text(0.94, 0.014, f"₹{_52h:,.0f}", fontsize=5.5, color=TV_GREEN, va="top", ha="right", zorder=6)

    # ── Watermark + Footer ────────────────────────────────────────────────────
    fig.text(0.5 * ratio, 0.5, "AutoAI\nAdvisory",
        fontsize=52, color="#FFFFFF08", fontweight="bold",
        ha="center", va="center", rotation=30, zorder=0)
    fig.text(0.38*ratio, 0.005,
        "AI-generated. Not SEBI registered. Not financial advice.",
        ha="center", va="bottom", color=TEXT_SEC, fontsize=6.5)
    fig.text(0.99, 0.005, "AutoAiAdvisory (SK)  v7.0",
        ha="right", va="bottom", color=TEXT_SEC, fontsize=6.5, fontweight="bold", style="italic")

    # ── SAVE ──────────────────────────────────────────────────────────────────
    fig.savefig(OUT_FILE, dpi=160, bbox_inches="tight",
        facecolor=BG_DARK, format="png")
    plt.close(fig)

    print(f"OUTPUT: {OUT_FILE}")
    print(f"META: {symbol}|{company_name}|{signal_text}|{score_str}|{last_close:.2f}|{sl_val:.2f}|{t1_val:.2f}|{t2_val:.2f}")

except Exception as e:
    print(f"[ERROR] Chart generation failed: {e}", file=sys.stderr)
    import traceback; traceback.print_exc(file=sys.stderr)
    sys.exit(1)
