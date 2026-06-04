#!/usr/bin/env python3
# gen_smart_stock_chart.py  v7.0 + Timeframe Upgrade
# Virtual-team redesign: Product Dev + TA Expert + Trader + Code/Design
# ─────────────────────────────────────────────────────────────────────
# Features: Multi-Timeframe | 11-check weighted scoring | ADX | BB Squeeze
# S/R proximity | Weekly trend | Candle confirm | Advanced Patterns

import sys, os, warnings, textwrap, logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
import time as _time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mticker
import yfinance as yf
import mplfinance as mpf

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ── PRO DARK THEME & TV PALETTE ───────────────────────────────────────────────
BG_DARK    = "#131722"
BG_PANEL   = "#1E222D"
BG_CARD    = "#2A2E39"
BORDER_COL = "#363A45"
TEXT_PRI   = "#D1D4DC"
TEXT_SEC   = "#787B86"
TEXT_ACC   = "#FFFFFF"
TV_GREEN   = "#089981"
TV_GREEN_L = "#0D4842"
TV_RED     = "#F23645"
TV_RED_L   = "#4C1B1B"
TV_BLUE    = "#2962FF"
TV_ORANGE  = "#F7931A"
TV_PURPLE  = "#9C27B0"
TV_TEAL    = "#26A69A"
FIB_GOLD   = "#D4AF37"
TV_AMBER   = "#FF6D00"
TV_GRAY    = "#787B86"
TV_YELLOW  = "#F6C026"

SIGNAL_COLORS = {
    "STRONG BUY":  ("#00E676", "#0D3B2E"),
    "BUY":         ("#26A69A", "#0D2E2B"),
    "WAIT":        ("#F6C026", "#2A2410"), 
    "NEUTRAL":     (TV_GRAY,   "#F5F5F7"),
    "SELL":        ("#EF5350", "#3B1212"),
    "STRONG SELL": ("#FF1744", "#4C0F0F"),
}

# ── TIMEFRAME CONFIGURATION ───────────────────────────────────────────────────
TIMEFRAME_MAP = {
    "30min":  dict(period="5d",   interval="30m", min_candles=80,  label="30 Min"),
    "30m":    dict(period="5d",   interval="30m", min_candles=80,  label="30 Min"),
    "60min":  dict(period="10d",  interval="60m", min_candles=80,  label="60 Min"),
    "60m":    dict(period="10d",  interval="60m", min_candles=80,  label="60 Min"),
    "1h":     dict(period="10d",  interval="60m", min_candles=80,  label="60 Min"),
    "daily":  dict(period="6mo",  interval="1d",  min_candles=126, label="Daily"),
    "1d":     dict(period="6mo",  interval="1d",  min_candles=126, label="Daily"),
    "weekly": dict(period="2y",   interval="1wk", min_candles=104, label="Weekly"),
    "1wk":    dict(period="2y",   interval="1wk", min_candles=104, label="Weekly"),
}
DEFAULT_TIMEFRAME = "daily"

def resolve_timeframe(raw: str) -> dict:
    key = str(raw).strip().lower()
    return TIMEFRAME_MAP.get(key, TIMEFRAME_MAP[DEFAULT_TIMEFRAME])

# ── DATA CLASSES ──────────────────────────────────────────────────────────────
@dataclass
class Pivot:
    idx: int; price: float; kind: str

@dataclass
class Pattern:
    name: str; confidence: float; direction: str
    start_idx: int; end_idx: int
    key_levels: list = field(default_factory=list)
    description: str = ""

@dataclass
class ElliottWave:
    pivots: list; labels: list
    fib_ext: dict = field(default_factory=dict)
    wave_complete: bool = False

# ── MATH & INDICATORS ─────────────────────────────────────────────────────────
def find_pivots(data: pd.DataFrame, left: int = 5, right: int = 5) -> list:
    highs = data["High"].values; lows = data["Low"].values; n = len(highs)
    pivots = []
    for i in range(left, n - right):
        is_high = all(highs[i] >= highs[i-j] for j in range(1, left+1)) and \
                  all(highs[i] >= highs[i+j] for j in range(1, right+1))
        is_low  = all(lows[i]  <= lows[i-j]  for j in range(1, left+1)) and \
                  all(lows[i]  <= lows[i+j]  for j in range(1, right+1))
        if is_high: pivots.append(Pivot(i, float(highs[i]), 'high'))
        elif is_low: pivots.append(Pivot(i, float(lows[i]),  'low'))
    cleaned = []
    for p in pivots:
        if cleaned and cleaned[-1].kind == p.kind:
            if (p.kind == 'high' and p.price > cleaned[-1].price) or \
               (p.kind == 'low'  and p.price < cleaned[-1].price):
                cleaned[-1] = p
        else:
            cleaned.append(p)
    return cleaned

def calc_rsi(prices, period=14):
    d = np.diff(prices)
    g = np.where(d > 0, d, 0.0); l = np.where(d < 0, -d, 0.0)
    ag = np.convolve(g, np.ones(period)/period, "full")[:len(g)]
    al = np.convolve(l, np.ones(period)/period, "full")[:len(l)]
    rs = np.where(al != 0, ag/al, 100.0)
    return np.concatenate([[np.nan]*period, (100 - 100/(1+rs))[period-1:]])

def calc_adx(data, period=14):
    hi = data["High"].values; lo = data["Low"].values; cl = data["Close"].values
    n  = len(cl)
    tr = np.maximum(hi[1:]-lo[1:], np.maximum(np.abs(hi[1:]-cl[:-1]), np.abs(lo[1:]-cl[:-1])))
    pdm = np.where(hi[1:]-hi[:-1] > lo[:-1]-lo[1:], np.maximum(hi[1:]-hi[:-1],0.), 0.)
    ndm = np.where(lo[:-1]-lo[1:] > hi[1:]-hi[:-1], np.maximum(lo[:-1]-lo[1:],0.), 0.)
    def smooth(arr, p):
        out = np.full(len(arr), np.nan); out[p-1] = np.sum(arr[:p])
        for i in range(p, len(arr)): out[i] = out[i-1] - out[i-1]/p + arr[i]
        return out
    atr = smooth(tr, period); pDI_raw = smooth(pdm, period); nDI_raw = smooth(ndm, period)
    pDI = np.where(atr>0, 100*pDI_raw/atr, 0.); nDI = np.where(atr>0, 100*nDI_raw/atr, 0.)
    dx  = np.where(pDI+nDI>0, 100*np.abs(pDI-nDI)/(pDI+nDI), 0.)
    return pd.Series(np.concatenate([[np.nan], smooth(dx, period)])[:n], index=data.index)

def detect_candle_pattern(data):
    if len(data) < 2: return "", ""
    o, h, l, c = float(data["Open"].iloc[-1]), float(data["High"].iloc[-1]), float(data["Low"].iloc[-1]), float(data["Close"].iloc[-1])
    o2, c2 = float(data["Open"].iloc[-2]), float(data["Close"].iloc[-2])
    body, rng = abs(c - o), (h - l if h > l else 0.0001)
    upper_wick, lower_wick = h - max(o, c), min(o, c) - l
    if body < 0.1 * rng: return "Doji", "neutral"
    if lower_wick >= 2.0 * body and upper_wick <= 0.3 * body and c > o: return "Hammer", "bullish"
    if upper_wick >= 2.0 * body and lower_wick <= 0.3 * body: return ("Inv Hammer", "bullish") if c > o else ("Shooting Star", "bearish")
    if c2 < o2 and c > o and c > o2 and o < c2: return "Bull Engulf", "bullish"
    if c2 > o2 and c < o and o > c2 and c < o2: return "Bear Engulf", "bearish"
    if body > 0.85 * rng: return ("Bull Marubozu", "bullish") if c > o else ("Bear Marubozu", "bearish")
    return "", ""

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

def detect_patterns(data: pd.DataFrame, pivots: list, lookback: int = 80) -> list:
    if len(data) < 20 or len(pivots) < 4: return []
    close = data["Close"].values; high = data["High"].values; low = data["Low"].values; n = len(close)
    recent = [p for p in pivots if p.idx >= n - lookback]
    peaks  = [p for p in recent if p.kind == 'high']
    troughs= [p for p in recent if p.kind == 'low']
    detected = []
    pct = lambda a,b: abs(a-b)/((a+b)/2)*100

    # Head & Shoulders / Inverse H&S
    for pk_list, tr_list, bull in [(troughs,peaks,True),(peaks,troughs,False)]:
        if len(pk_list) < 3: continue
        for i in range(len(pk_list)-2):
            l,m,r = pk_list[i],pk_list[i+1],pk_list[i+2]
            mid_is_extreme = (m.price < l.price and m.price < r.price) if bull else (m.price > l.price and m.price > r.price)
            if not mid_is_extreme: continue
            if pct(l.price,r.price) < 8:
                conf = max(0.3, 0.9 - pct(l.price,r.price)/20)
                between = [t for t in tr_list if l.idx < t.idx < r.idx]
                neckline = (max if bull else min)([t.price for t in between], default=m.price)
                h = abs(m.price - neckline)
                tgt = neckline + (h if bull else -h)
                detected.append(Pattern(
                    name="Inverse H&S" if bull else "Head & Shoulders",
                    confidence=conf, direction="bullish" if bull else "bearish",
                    start_idx=l.idx, end_idx=r.idx, key_levels=[(neckline,"Neckline"),(tgt,"Target")]))

    # Double Top / Double Bottom
    for pk_list, tr_list, bull in [(troughs,peaks,True),(peaks,troughs,False)]:
        if len(pk_list) < 2: continue
        l,r = pk_list[-2], pk_list[-1]
        if pct(l.price,r.price) < 3 and r.idx - l.idx >= 5:
            between = [t for t in tr_list if l.idx < t.idx < r.idx]
            neck = (max if bull else min)([t.price for t in between], default=l.price*(1.03 if bull else 0.97))
            h = abs(neck - l.price)
            detected.append(Pattern(
                name="Double Bottom" if bull else "Double Top",
                confidence=0.80, direction="bullish" if bull else "bearish",
                start_idx=l.idx, end_idx=r.idx, key_levels=[(neck,"Neckline"),(neck+(h if bull else -h),"Target")]))

    # Triangles / Wedges
    rec_h = sorted([p for p in recent if p.kind=='high'], key=lambda x: x.idx)
    rec_l = sorted([p for p in recent if p.kind=='low'],  key=lambda x: x.idx)
    if len(rec_h) >= 2 and len(rec_l) >= 2:
        h_sl = (rec_h[-1].price - rec_h[0].price) / max(1, rec_h[-1].idx - rec_h[0].idx)
        l_sl = (rec_l[-1].price - rec_l[0].price) / max(1, rec_l[-1].idx - rec_l[0].idx)
        p0   = close[-1] * 0.0005
        if abs(h_sl) < p0 and l_sl > p0:
            detected.append(Pattern("Ascending Triangle",0.75,"bullish",rec_l[0].idx,n-1, []))
        elif abs(l_sl) < p0 and h_sl < -p0:
            detected.append(Pattern("Descending Triangle",0.75,"bearish",rec_h[0].idx,n-1, []))
        elif h_sl > 0 and l_sl > 0 and h_sl < l_sl:
            detected.append(Pattern("Rising Wedge",0.65,"bearish",rec_l[0].idx,n-1, []))
        elif h_sl < 0 and l_sl < 0 and h_sl > l_sl:
            detected.append(Pattern("Falling Wedge",0.65,"bullish",rec_h[0].idx,n-1, []))

    # Cup & Handle
    if n >= 40:
        seg = close[max(0,n-60):n]
        if len(seg) >= 30:
            mid = len(seg)//2; lh = seg[0]; bot = np.min(seg[5:mid+5]); rh = seg[-10]
            hl = np.min(seg[-10:]) if len(seg)>10 else bot
            dep = (max(lh,rh)-bot)/max(lh,rh)
            if 0.1 < dep < 0.5 and pct(lh,rh) < 10 and hl > bot:
                detected.append(Pattern("Cup & Handle",0.70,"bullish",max(0,n-60),n-1, [(rh,"Breakout"),(rh+(rh-bot),"Target")]))

    # Flag / Pennant
    if len(recent) >= 4:
        move = abs(close[-1] - close[max(0,n-20)]); avgr = np.mean(high[n-20:n] - low[n-20:n]) if n>20 else 1
        cons = max(close[n-10:n]) - min(close[n-10:n]) if n>10 else avgr*99
        if cons < avgr*1.5 and move > avgr*5:
            bull = close[-1] > close[max(0,n-25)]
            detected.append(Pattern("Flag / Pennant",0.68,"bullish" if bull else "bearish", n-20,n-1,[]))

    # Rectangle
    if len(rec_h)>=2 and len(rec_l)>=2:
        rr = max(p.price for p in rec_h[-3:]) - min(p.price for p in rec_h[-3:])
        sr = max(p.price for p in rec_l[-3:]) - min(p.price for p in rec_l[-3:])
        ar = np.mean([p.price for p in rec_h[-3:]]); asl = np.mean([p.price for p in rec_l[-3:]])
        if rr/ar < 0.03 and sr/asl < 0.03:
            h = ar - asl
            detected.append(Pattern("Rectangle (Range)",0.70,"neutral",rec_l[0].idx,n-1, [(ar,"Resist"),(asl,"Support")]))

    seen = {}
    for p in detected:
        if p.name not in seen or p.confidence > seen[p.name].confidence:
            seen[p.name] = p
    return sorted(seen.values(), key=lambda x: -x.confidence)


# ── SCORING ENGINE ────────────────────────────────────────────────────────────
def score_symbol(sym, name):
    try:
        df = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=True, timeout=12)
        if df.empty or len(df) < 55: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open","High","Low","Close","Volume"])
        if len(df) < 55: return None
        c = df["Close"]
        e9, e21 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
        s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
        
        def last_cross(arr, window):
            for i in range(1, min(window+1, len(arr))):
                idx = len(arr)-i
                if np.isnan(arr[idx]) or np.isnan(arr[idx-1]): continue
                if arr[idx]>0 and arr[idx-1]<=0: return i, +1
                if arr[idx]<0 and arr[idx-1]>=0: return i, -1
            return None, 0
            
        ema_diff, dma_diff = e9.values - e21.values, (s20 - s50).values
        eb, ed = last_cross(ema_diff, 3); db, dd = last_cross(dma_diff, 5)
        
        e9l, e21l = float(e9.iloc[-1]), float(e21.iloc[-1])
        lc = float(c.iloc[-1]); pc = float(c.iloc[-2]) if len(c)>1 else lc
        chg = (lc-pc)/pc*100 if pc>0 else 0
        vm20 = float(df["Volume"].rolling(20).mean().iloc[-1])
        vr = float(df["Volume"].iloc[-1])/vm20 if vm20>0 else 1.0
        
        score = 0; reasons = []
        if ed==+1: score+=5; reasons.append(f"EMA 9/21 Bull Cross {eb}d ago")
        elif ed==-1: score-=5; reasons.append(f"EMA 9/21 Bear Cross {eb}d ago")
        if dd==+1: score+=4; reasons.append(f"SMA 20/50 Bull Cross {db}d ago")
        elif dd==-1: score-=4; reasons.append(f"SMA 20/50 Bear Cross {db}d ago")
        score += 1 if e9l>e21l else -1
        if vr > 1.5 and ed==+1: score += 1; reasons.append("Vol surge confirms bull")
        if abs(chg) > 2: score += (1 if chg>0 else -1)
        reason = reasons[0] if reasons else ("EMA bullish" if e9l>e21l else "EMA bearish")
        return {"sym":sym,"name":name,"score":score,"ed":ed,"dd":dd,"reason":reason,"close":lc,"chg":round(chg,2)}
    except Exception: return None

def compute_full_score(data, weekly_data, pivots, rsi_last, hist, macd,
                       ema9, ema21, sma20, sma50, bb_upper, bb_lower, bb_pct,
                       last_close, vol_s, vol_ma20, adx_last):
    checks = []

    # 1. EMA 9/21
    ed_diff = (ema9 - ema21).values
    cross_age = None
    for i in range(1, min(6, len(ed_diff))):
        idx = len(ed_diff) - i
        if ed_diff[idx] > 0 and ed_diff[idx-1] <= 0:
            checks.append((f"EMA 9/21 Bull Cross  {i}d ago", 2, TV_GREEN)); cross_age = i; break
        elif ed_diff[idx] < 0 and ed_diff[idx-1] >= 0:
            checks.append((f"EMA 9/21 Bear Cross  {i}d ago", -2, TV_RED)); cross_age = i; break
    if cross_age is None:
        pts = +1 if float(ema9.iloc[-1]) > float(ema21.iloc[-1]) else -1
        checks.append(("EMA 9 > EMA 21" if pts > 0 else "EMA 9 < EMA 21", pts, TV_GREEN if pts > 0 else TV_RED))

    # 2. SMA 20/50
    s20l = float(sma20.dropna().iloc[-1]) if sma20.dropna().shape[0] > 0 else last_close
    s50l = float(sma50.dropna().iloc[-1]) if sma50.dropna().shape[0] > 0 else last_close
    sma_align = s20l > s50l
    checks.append((f"SMA 20{'>'if sma_align else '<'}SMA 50", 1 if sma_align else -1, TV_GREEN if sma_align else TV_RED))

    # 3. MACD
    h0 = float(hist.dropna().iloc[-1]) if hist.dropna().shape[0] > 0 else 0
    macd_pts = 1 if h0 > 0 else -1
    checks.append(("MACD Expanding +" if macd_pts > 0 else "MACD Expanding -", macd_pts, TV_GREEN if macd_pts > 0 else TV_RED))

    # 4. RSI
    if rsi_last > 60: checks.append((f"RSI {rsi_last:.0f} Bullish", 1, TV_GREEN))
    elif rsi_last < 40: checks.append((f"RSI {rsi_last:.0f} Bearish", -1, TV_RED))
    else: checks.append((f"RSI {rsi_last:.0f} Neutral", 0, TEXT_SEC))

    # 5. ADX Trend
    adx_trending = adx_last > 25
    e9l = float(ema9.iloc[-1]); e21l = float(ema21.iloc[-1])
    checks.append((f"ADX {adx_last:.0f} Trending" if adx_trending else f"ADX {adx_last:.0f} Ranging",
                   +1 if adx_trending and e9l>e21l else (-1 if adx_trending and e9l<e21l else 0),
                   TV_GREEN if (adx_trending and e9l>e21l) else (TV_RED if (adx_trending and e9l<e21l) else TV_GRAY)))

    # 6. Volume
    lv = float(vol_s.iloc[-1]); av = float(vol_ma20.dropna().iloc[-1]) if vol_ma20.dropna().shape[0] > 0 else lv
    vr = lv / av if av > 0 else 1.0
    checks.append(("Volume surge +" if vr>1.5 else ("Volume normal" if vr>0.7 else "Volume low"),
                   +1 if vr>1.5 and e9l>e21l else (-1 if vr>1.5 and e9l<e21l else 0),
                   TV_GREEN if (vr>1.5 and e9l>e21l) else (TV_RED if (vr>1.5 and e9l<e21l) else TV_GRAY)))

    # 7. S/R Proximity
    sr_pts = 0; sr_lbl = "Mid-Range (no S/R nearby)"; sr_col = TEXT_SEC
    if len(pivots) >= 2:
        pivot_prices = sorted(set(round(p.price, 2) for p in pivots[-20:]))
        nearest_dist = min(abs(last_close - pp) / last_close * 100 for pp in pivot_prices)
        nearest_p    = min(pivot_prices, key=lambda pp: abs(last_close - pp))
        if nearest_dist <= 1.5:
            if last_close > nearest_p: sr_pts, sr_lbl, sr_col = +1, f"At S/R Support ₹{nearest_p:,.0f}", TV_GREEN
            else: sr_pts, sr_lbl, sr_col = -1, f"At S/R Resist  ₹{nearest_p:,.0f}", TV_RED
        else: sr_lbl = f"Mid-Range  Δ{nearest_dist:.1f}% from ₹{nearest_p:,.0f}"
    checks.append((sr_lbl, sr_pts, sr_col))

    score = sum(pts for _, pts, _ in checks)
    max_score = 9

    if   score >= 5:  signal_text, sig_col = "STRONG BUY",  TV_GREEN
    elif score >= 3:  signal_text, sig_col = "BUY",          TV_TEAL
    elif score <= -5: signal_text, sig_col = "STRONG SELL",  TV_RED
    elif score <= -3: signal_text, sig_col = "SELL",         "#EF5350"
    else:             signal_text, sig_col = "WAIT",         TV_YELLOW 

    return checks, score, max_score, signal_text, sig_col

# ── DRAWING LOGIC ─────────────────────────────────────────────────────────────
def draw_fib_levels(ax, lo, hi, n):
    diff = hi - lo
    levels = [(0.0,"0%","#787B86","--"),(0.236,"23.6%","#F7931A",":"),(0.382,"38.2%","#FF6D00",":"),
              (0.5,"50%","#9C27B0",":"),(0.618,"61.8%","#089981","-"),(0.786,"78.6%","#2962FF",":"),
              (1.0,"100%","#787B86","--")]
    ext = [(1.272,"127.2%","#26A69A"),(1.618,"161.8%","#089981"),(2.618,"261.8%","#F23645")]
    for frac,lbl,col,ls in levels:
        price = hi - diff*frac
        ax.axhline(price, color=col, linestyle=ls, linewidth=0.75, alpha=0.65, zorder=2)
        ax.annotate(f" {lbl} ({price:,.1f})", xy=(1.001,price), xycoords=("axes fraction","data"), color=col, fontsize=6, va="center", fontweight="bold", zorder=6, clip_on=False)
    for frac,lbl,col in ext:
        price = lo + diff*frac
        ax.axhline(price, color=col, linestyle="--", linewidth=0.8, alpha=0.6, zorder=2)
        ax.annotate(f" {lbl} ({price:,.1f})", xy=(1.001,price), xycoords=("axes fraction","data"), color=col, fontsize=6, va="center", fontweight="bold", zorder=6, clip_on=False)

def draw_ew_labels(ax, ew, data):
    if not ew: return
    yl0, yl1 = ax.get_ylim(); offset = max(yl1 - yl0, 1) * 0.025
    for pivot, label in zip(ew.pivots, ew.labels):
        y_txt = pivot.price + offset if pivot.kind=='high' else pivot.price - offset
        ax.annotate(label, xy=(pivot.idx, pivot.price), xytext=(pivot.idx, y_txt), fontsize=8, color=TV_BLUE, fontweight="bold", ha="center", va="bottom" if pivot.kind=='low' else "top", bbox=dict(boxstyle="circle,pad=0.22", facecolor="white", edgecolor=TV_BLUE, linewidth=1.1, alpha=0.92), zorder=11)
    xs = [p.idx for p in ew.pivots]; ys = [p.price for p in ew.pivots]
    ax.plot(xs, ys, color=TV_BLUE, linewidth=1.1, linestyle="-", alpha=0.55, zorder=9)

def draw_pattern(ax, pattern, data):
    if not pattern: return
    si = max(0, pattern.start_idx); ei = min(len(data)-1, pattern.end_idx)
    if ei <= si: return
    col_map = {"bullish":TV_GREEN,"bearish":TV_RED,"neutral":TV_GRAY}
    ec = col_map.get(pattern.direction, TV_GRAY)
    ax.axvspan(si, ei, alpha=0.06, color=ec, zorder=1)
    for price, lbl in pattern.key_levels[:2]: ax.axhline(price, color=ec, linestyle="--", linewidth=0.75, alpha=0.55, zorder=3)
    mid_x = (si + ei) / 2
    ax.text(mid_x, 1.0, f" {pattern.name} ", transform=ax.get_xaxis_transform(), color=ec, fontsize=7, ha="center", va="bottom", fontweight="bold", alpha=0.9, zorder=10, clip_on=False, bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor=ec, alpha=0.92, lw=0.75))

# ── MAIN EXECUTION ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    OUT_DIR = "output"
    os.makedirs(OUT_DIR, exist_ok=True)

    # UNIVERSE FOR SCANNER
    UNIVERSE = [
        ("RELIANCE.NS","Reliance Industries"),("TCS.NS","TCS"),
        ("HDFCBANK.NS","HDFC Bank"),("INFY.NS","Infosys"),
        ("ICICIBANK.NS","ICICI Bank"),("HINDUNILVR.NS","Hindustan Unilever"),
        ("SBIN.NS","State Bank of India"),("BHARTIARTL.NS","Bharti Airtel"),
        ("ITC.NS","ITC"),("KOTAKBANK.NS","Kotak Mahindra Bank"),
        ("LT.NS","L&T"),("AXISBANK.NS","Axis Bank"),
        ("ASIANPAINT.NS","Asian Paints"),("MARUTI.NS","Maruti Suzuki"),
        ("TATAMOTORS.NS","Tata Motors"),("WIPRO.NS","Wipro"),
        ("BAJFINANCE.NS","Bajaj Finance"),("SUNPHARMA.NS","Sun Pharma"),
        ("TITAN.NS","Titan"),("NESTLEIND.NS","Nestle India"),
        ("HCLTECH.NS","HCL Technologies"),("ADANIENT.NS","Adani Enterprises"),
        ("POWERGRID.NS","Power Grid"),("NTPC.NS","NTPC"),
        ("ULTRACEMCO.NS","UltraTech Cement"),("BAJAJFINSV.NS","Bajaj Finserv"),
        ("ONGC.NS","ONGC"),("COALINDIA.NS","Coal India"),
        ("JSWSTEEL.NS","JSW Steel"),("TATASTEEL.NS","Tata Steel"),
        ("HINDALCO.NS","Hindalco"),("TECHM.NS","Tech Mahindra"),
        ("DRREDDY.NS","Dr Reddy's"),("CIPLA.NS","Cipla"),
        ("APOLLOHOSP.NS","Apollo Hospitals"),("HEROMOTOCO.NS","Hero MotoCorp"),
        ("BAJAJ-AUTO.NS","Bajaj Auto"),("EICHERMOT.NS","Eicher Motors"),
        ("M&M.NS","Mahindra & Mahindra"),("IRCTC.NS","IRCTC"),
        ("HAL.NS","HAL"),("BEL.NS","BEL"),("RVNL.NS","RVNL"),
        ("TRENT.NS","Trent"),("DMART.NS","DMart"),
        ("PERSISTENT.NS","Persistent Systems"),("LTIM.NS","LTIMindtree"),
        ("COFORGE.NS","Coforge"),("ZOMATO.NS","Zomato"),
        ("RECLTD.NS","REC"),("PFC.NS","PFC"),
        ("ADANIPORTS.NS","Adani Ports"),("ADANIGREEN.NS","Adani Green"),
        ("IRFC.NS","IRFC"),("SIEMENS.NS","Siemens"),("ABB.NS","ABB India"),
        ("HAVELLS.NS","Havells"),("VOLTAS.NS","Voltas"),
        ("PAGEIND.NS","Page Industries"),("MUTHOOTFIN.NS","Muthoot Finance"),
        ("CHOLAFIN.NS","Chola Finance"),("PIDILITIND.NS","Pidilite Industries"),
        ("BERGEPAINT.NS","Berger Paints"),("INDIGO.NS","IndiGo"),
        ("SBILIFE.NS","SBI Life"),("HDFCLIFE.NS","HDFC Life"),("LICI.NS","LIC"),
        ("POLYCAB.NS","Polycab"),("CUMMINSIND.NS","Cummins India"),
        ("BHEL.NS","BHEL"),("NHPC.NS","NHPC"),("SJVN.NS","SJVN"),
        ("SAIL.NS","SAIL"),("NMDC.NS","NMDC"),
        ("JINDALSTEL.NS","Jindal Steel"),("VEDL.NS","Vedanta"),
        ("MPHASIS.NS","Mphasis"),("OFSS.NS","Oracle Fin Svcs"),
        ("KPITTECH.NS","KPIT Technologies"),("TATAELXSI.NS","Tata Elxsi"),
        ("DIVISLAB.NS","Divi's Labs"),("AUROPHARMA.NS","Aurobindo"),
        ("TORNTPHARM.NS","Torrent Pharma"),("ALKEM.NS","Alkem Labs"),
        ("IPCALAB.NS","IPCA Labs"),("GLENMARK.NS","Glenmark"),
        ("DABUR.NS","Dabur"),("MARICO.NS","Marico"),("COLPAL.NS","Colgate"),
        ("EMAMILTD.NS","Emami"),("GODREJCP.NS","Godrej Consumer"),
        ("TATACONSUM.NS","Tata Consumer"),
        ("FEDERALBNK.NS","Federal Bank"),("IDFCFIRSTB.NS","IDFC First Bank"),
        ("BANKINDIA.NS","Bank of India"),("CANBK.NS","Canara Bank"),
        ("UNIONBANK.NS","Union Bank"),("PNB.NS","Punjab National Bank"),
        ("INDUSINDBK.NS","IndusInd Bank"),("BANDHANBNK.NS","Bandhan Bank"),
        ("MOTHERSON.NS","Motherson Sumi"),("BOSCHLTD.NS","Bosch"),
        ("BHARATFORG.NS","Bharat Forge"),("BALKRISIND.NS","Balkrishna Ind"),
        ("DLF.NS","DLF"),("GODREJPROP.NS","Godrej Properties"),
        ("PRESTIGE.NS","Prestige Estates"),("OBEROI.NS","Oberoi Realty"),
        ("NAUKRI.NS","Naukri (Info Edge)"),("DELHIVERY.NS","Delhivery"),
        ("NYKAA.NS","Nykaa"),("PAYTM.NS","Paytm")
    ]

    tf_raw = DEFAULT_TIMEFRAME
    if len(sys.argv) >= 2:
        raw_sym = sys.argv[1]
        company_name = raw_sym
        if len(sys.argv) == 3: tf_raw = sys.argv[2]
        elif len(sys.argv) >= 4: company_name = sys.argv[2]; tf_raw = sys.argv[3]
        
        symbol = raw_sym if raw_sym.endswith(".NS") or raw_sym.endswith(".BO") else f"{raw_sym}.NS"
        winner = {"sym": symbol, "name": company_name, "score": 0, "reason": "Manual Request"}
        print(f"[*] Generating {resolve_timeframe(tf_raw)['label']} chart for {symbol}...")
    else:
        print("[*] No symbol provided. Running Auto-Scanner across Nifty 500 universe...")
        candidates = []; checked = 0
        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = {pool.submit(score_symbol,sym,name):(sym,name) for sym,name in UNIVERSE}
            for fut in as_completed(futures, timeout=150):
                checked += 1
                try:
                    r = fut.result()
                    if r: candidates.append(r)
                except Exception: pass
        if not candidates: print("[ERROR] No candidates found."); sys.exit(1)
        candidates.sort(key=lambda r:(-(1 if r["ed"]!=0 or r["dd"]!=0 else 0),-abs(r["score"]),-r["score"]))
        winner = candidates[0]
        symbol = winner["sym"]; company_name = winner["name"]
        print(f"[*] WINNER: {symbol} | Reason: {winner['reason']} | Score: {winner['score']}")

    tf_config = resolve_timeframe(tf_raw)
    _sym_safe = symbol.replace(".NS","").upper()
    OUT_FILE  = os.path.join(OUT_DIR, f"chart_{_sym_safe}_{int(_time.time())}.png")

    # Data Download
    data = yf.download(symbol, period=tf_config["period"], interval=tf_config["interval"], progress=False, auto_adjust=True)
    if data.empty: print(f"[ERROR] No data for {symbol}"); sys.exit(1)
    if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
    data = data.dropna(subset=["Open","High","Low","Close","Volume"])
    
    # Restrict to last 120 bars for cleaner plotting (like v3)
    data = data.tail(120)
    n = len(data)

    try:
        weekly_data = yf.download(symbol, period="2y", interval="1wk", progress=False, auto_adjust=True)
        if isinstance(weekly_data.columns, pd.MultiIndex): weekly_data.columns = weekly_data.columns.get_level_values(0)
        weekly_data = weekly_data.dropna(subset=["Close"])
    except Exception: weekly_data = None

    # Calculate indicators
    close_s = data["Close"]; vol_s = data["Volume"]
    ema9  = close_s.ewm(span=9,  adjust=False).mean()
    ema21 = close_s.ewm(span=21, adjust=False).mean()
    sma20 = close_s.rolling(20).mean(); sma50 = close_s.rolling(50).mean()
    macd  = close_s.ewm(span=12, adjust=False).mean() - close_s.ewm(span=26, adjust=False).mean()
    macd_sig = macd.ewm(span=9, adjust=False).mean(); hist  = macd - macd_sig
    vol_ma20 = vol_s.rolling(20).mean()
    bb_std = close_s.rolling(20).std()
    bb_upper = sma20 + 2 * bb_std; bb_lower = sma20 - 2 * bb_std
    bb_pct = ((close_s - bb_lower) / (bb_upper - bb_lower)).clip(0, 1)

    rsi_s = pd.Series(calc_rsi(close_s.values)[:n], index=data.index)
    adx_s = calc_adx(data)
    last_close = float(close_s.iloc[-1]); prev_close = float(close_s.iloc[-2]) if n >= 2 else last_close
    change_pct = (last_close - prev_close) / prev_close * 100

    pivots = find_pivots(data, left=5, right=5)
    checks, score, max_score, signal_text, sig_col = compute_full_score(
        data, weekly_data, pivots, float(rsi_s.dropna().iloc[-1]), hist, macd,
        ema9, ema21, sma20, sma50, bb_upper, bb_lower, bb_pct,
        last_close, vol_s, vol_ma20, float(adx_s.dropna().iloc[-1]) if adx_s.dropna().shape[0]>0 else 0.0
    )

    is_bull = score >= 0; is_wait = signal_text == "WAIT"
    score_str = f"{score:+d}/{max_score}"

    # Targets & Risk
    if not is_wait:
        sl_val = min(float(data["Low"].tail(5).min()), float(ema21.iloc[-1])) * 0.997 if is_bull else max(float(data["High"].tail(5).max()), float(ema21.iloc[-1])) * 1.003
        sl_pct = (last_close - sl_val) / last_close * 100 if is_bull else (sl_val - last_close) / last_close * 100
        t1_val = last_close + 1.5 * (last_close - sl_val) if is_bull else last_close - 1.5 * (sl_val - last_close)
        t2_val = last_close + 2.5 * (last_close - sl_val) if is_bull else last_close - 2.5 * (sl_val - last_close)
        t1_pct = abs(t1_val - last_close) / last_close * 100
        t2_pct = abs(t2_val - last_close) / last_close * 100
    else: sl_val = t1_val = t2_val = sl_pct = t1_pct = t2_pct = 0.0

    # 52-Week High/Low (v3 Feature)
    hi52 = float(data["High"].max()); lo52 = float(data["Low"].min())
    swing_hi = float(data.tail(60)["High"].max()); swing_lo = float(data.tail(60)["Low"].min())

    # Chart Plotting
    mc = mpf.make_marketcolors(up=TV_GREEN, down=TV_RED, wick={"up": TV_GREEN, "down": TV_RED}, edge={"up": TV_GREEN, "down": TV_RED}, volume={"up": "#C3EFEB", "down": "#FBCDD0"})
    tv_style = mpf.make_mpf_style(marketcolors=mc, facecolor="#FFFFFF", edgecolor="#E0E3EB", figcolor="#FFFFFF", gridcolor="#F0F3FA", gridstyle="-", rc={"axes.labelcolor":"#131722","xtick.color":"#787B86","ytick.color":"#787B86","text.color":"#131722","font.family":"DejaVu Sans","font.size":9,"axes.spines.top":False,"axes.spines.right":False})

    hc = [TV_GREEN if v >= 0 else TV_RED for v in hist.fillna(0)]
    apds = [
        mpf.make_addplot(ema9, color=TV_ORANGE, width=1.8),
        mpf.make_addplot(ema21, color=TV_BLUE, width=1.8),
        mpf.make_addplot(sma20, color=TV_PURPLE, width=1.1, linestyle="--"),
        mpf.make_addplot(sma50, color="#FF6D00", width=1.1, linestyle="--"),
        mpf.make_addplot(bb_upper.fillna(method="ffill"), color="#2962FF", width=0.8, linestyle=":", alpha=0.5),
        mpf.make_addplot(bb_lower.fillna(method="ffill"), color="#2962FF", width=0.8, linestyle=":", alpha=0.5),
        mpf.make_addplot(macd, panel=2, color=TV_BLUE, width=1.2, ylabel="MACD"),
        mpf.make_addplot(macd_sig, panel=2, color="#FF6D00", width=1.2),
        mpf.make_addplot(hist, panel=2, type="bar", color=hc, alpha=0.70),
        mpf.make_addplot(rsi_s, panel=3, color=TV_PURPLE, width=1.3, ylabel="RSI/ADX"),
        mpf.make_addplot(adx_s, panel=3, color=TV_ORANGE, width=1.1, linestyle="--"),
        mpf.make_addplot([70]*n, panel=3, color=TV_RED+"66", linestyle="--", width=0.7),
        mpf.make_addplot([30]*n, panel=3, color=TV_GREEN+"66", linestyle="--", width=0.7),
        mpf.make_addplot([25]*n, panel=3, color=TV_ORANGE+"55", linestyle=":", width=0.7),
    ]

    fig, axes = mpf.plot(data, type="candle", style=tv_style, addplot=apds, title="", figratio=(16, 12), figscale=1.3, volume=True, panel_ratios=(6, 2, 2, 2), returnfig=True, tight_layout=False)
    ax0 = axes[0]
    
    # Apply Overlays to Price Panel
    ax0.axhline(hi52, color=FIB_GOLD, linestyle="--", linewidth=1.0, alpha=0.75, zorder=4)
    ax0.axhline(lo52, color=TV_PURPLE, linestyle="--", linewidth=1.0, alpha=0.75, zorder=4)
    draw_fib_levels(ax0, swing_lo, swing_hi, n)
    ew = elliott_wave(data, pivots)
    patterns = detect_patterns(data, pivots, lookback=80)
    top_pat = patterns[0] if patterns else None
    if ew: draw_ew_labels(ax0, ew, data)
    if top_pat: draw_pattern(ax0, top_pat, data)

    # Adjust layout for GridSpec Sidebar (v3 architecture)
    fw, fh = fig.get_size_inches(); SIDEBAR_INCH = 3.8
    fig.set_size_inches(fw + SIDEBAR_INCH, fh)
    ratio = fw / (fw + SIDEBAR_INCH)
    for ax_i in fig.get_axes():
        p = ax_i.get_position(); ax_i.set_position([p.x0*ratio, p.y0, p.width*ratio, p.height])

    # Sidebar Rendering
    sa = fig.add_axes([ratio + 0.005, 0.05, 1.0 - ratio - 0.01, 0.89])
    sa.set_xlim(0,1); sa.set_ylim(0,1); sa.axis("off")
    bg_map = {"STRONG BUY":"#E6FAF4","BUY":"#F0FBF8","NEUTRAL":"#F5F5F7","WAIT":"#F5F5F7","SELL":"#FFF0F0","STRONG SELL":"#FFE8E8"}
    sa.add_patch(FancyBboxPatch((0.02,0.01), 0.96, 0.98, boxstyle="round,pad=0.015", lw=2, edgecolor=sig_col, facecolor=bg_map.get(signal_text,"#F5F5F7"), transform=sa.transAxes, zorder=1))

    # Signal header
    hmap = {"STRONG BUY":"⬆⬆ STRONG BUY","BUY":"⬆ BUY","WAIT":"⏸ WAIT — No Setup", "NEUTRAL":"── NEUTRAL", "SELL":"⬇ SELL","STRONG SELL":"⬇⬇ STRONG SELL"}
    sa.text(0.5, 0.985, f"AUTOAI ADVISORY | {tf_config['label']}", transform=sa.transAxes, fontsize=8, color="#787B86", ha="center", va="top", style="italic", zorder=5)
    sa.text(0.5, 0.955, hmap.get(signal_text, signal_text), transform=sa.transAxes, fontsize=15, fontweight="bold", color=sig_col, ha="center", va="top", zorder=5)
    sa.text(0.5, 0.910, f"Score: {score_str}  |  ADX: {float(adx_s.dropna().iloc[-1]) if adx_s.dropna().shape[0]>0 else 0:.0f}", transform=sa.transAxes, fontsize=9, color=sig_col, ha="center", va="top", zorder=5)
    sa.plot([0.05,0.95],[0.886,0.886], color="#CCCCCC", lw=1.0, transform=sa.transAxes, zorder=5)

    # Win reason (wrapped)
    win_reason = winner.get("reason", "Scanner Match")
    wrapped = textwrap.fill(win_reason, width=24)
    sa.text(0.5, 0.870, wrapped, transform=sa.transAxes, fontsize=8.5, color=sig_col, ha="center", va="top", fontweight="bold", zorder=5, linespacing=1.35)
    reason_lines = wrapped.count('\n') + 1
    sa.plot([0.05,0.95],[0.870 - 0.045*reason_lines, 0.870 - 0.045*reason_lines], color="#CCCCCC", lw=0.8, transform=sa.transAxes, zorder=5)

    # Indicator check rows
    check_top = 0.870 - 0.045*reason_lines - 0.015; row_gap = 0.087
    for i, (label, s, col) in enumerate(checks):
        y = check_top - i * row_gap
        sa.text(0.06, y, "+" if s>0 else ("–" if s<0 else "="), transform=sa.transAxes, fontsize=11, fontweight="bold", color=col, va="top", zorder=5)
        sa.text(0.20, y, label, transform=sa.transAxes, fontsize=8.2, color=col, va="top", zorder=5)

    divider_y = check_top - len(checks)*row_gap + 0.04
    sa.plot([0.05,0.95],[divider_y,divider_y], color="#CCCCCC", lw=1.0, transform=sa.transAxes, zorder=5)

    # Trade level rows
    tl_top = divider_y - 0.012; tl_gap = 0.082
    trows = [("Entry", last_close, None, "#131722", True), ("Stop Loss", sl_val, -sl_pct, TV_RED, False), ("Target 1", t1_val, t1_pct, TV_GREEN, False), ("Target 2", t2_val, t2_pct, TV_GREEN, False)]
    for i, (lbl, price, pct, col, bold) in enumerate(trows):
        y = tl_top - i * tl_gap; fw2 = "bold" if bold else "normal"
        sa.text(0.06, y, lbl, transform=sa.transAxes, fontsize=8.5, color="#131722", va="top", fontweight=fw2, zorder=5)
        sa.text(0.60, y, f"Rs.{price:,.1f}", transform=sa.transAxes, fontsize=10, color=col, va="top", fontweight=fw2, ha="right", zorder=5, fontfamily="monospace")
        if pct is not None: sa.text(0.98, y, f"({pct:+.1f}%)", transform=sa.transAxes, fontsize=8.5, color=col, va="top", ha="right", zorder=5)

    # Pattern & 52W info
    info_y = tl_top - len(trows)*tl_gap
    sa.plot([0.05,0.95],[info_y,info_y], color="#CCCCCC", lw=0.8, transform=sa.transAxes, zorder=5)
    info_y -= 0.010
    pat_name = top_pat.name if top_pat else "No Pattern"
    pat_dir = f"({top_pat.direction[:4].title()})" if top_pat else ""
    sa.text(0.06, info_y, "Pattern:", transform=sa.transAxes, fontsize=7.5, color="#787B86", va="top", zorder=5)
    sa.text(0.98, info_y, f"{pat_name} {pat_dir}", transform=sa.transAxes, fontsize=7.5, color=sig_col, va="top", ha="right", zorder=5, fontweight="bold")
    
    info_y -= 0.065
    sa.text(0.06, info_y, "52W High:", transform=sa.transAxes, fontsize=7.5, color="#787B86", va="top", zorder=5)
    sa.text(0.98, info_y, f"Rs.{hi52:,.0f}", transform=sa.transAxes, fontsize=8, color=FIB_GOLD, va="top", ha="right", fontweight="bold", zorder=5)

    info_y -= 0.065
    sa.text(0.06, info_y, "52W Low:", transform=sa.transAxes, fontsize=7.5, color="#787B86", va="top", zorder=5)
    sa.text(0.98, info_y, f"Rs.{lo52:,.0f}", transform=sa.transAxes, fontsize=8, color=TV_PURPLE, va="top", ha="right", fontweight="bold", zorder=5)

    fig.text(0.99, 0.005, "AutoAiAdvisory (SK) v7.0 | Investment & Right stocks Finding With AI", ha="right", va="bottom", color=TEXT_SEC, fontsize=6.5, style="italic")
    fig.savefig(OUT_FILE, dpi=170, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(fig)
    
    print(f"[*] Success! Chart generated at: {OUT_FILE}")
