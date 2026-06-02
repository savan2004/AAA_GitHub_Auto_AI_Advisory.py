#!/usr/bin/env python3
# gen_smart_stock_chart.py - Scans Nifty 200, finds best crossover, charts it
# Outputs: output/smart_stock_chart.png
import sys, os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
import matplotlib
matplotlib.use("Agg")  # Set backend BEFORE importing pyplot
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import warnings
warnings.filterwarnings('ignore')

# === CHART UTILITIES (inlined) ===
TV_GREEN  = "#089981"
TV_RED    = "#F23645"
TV_BLUE   = "#2962FF"
TV_ORANGE = "#F7931A"
TV_PURPLE = "#9C27B0"
TV_GRAY   = "#787B86"
FIB_GOLD  = "#D4AF37"

# === DATA CLASSES ===
@dataclass
class Pivot:
    idx: int          # position in data array
    price: float
    kind: str         # 'high' or 'low'

@dataclass
class Pattern:
    name: str
    confidence: float    # 0-1
    direction: str       # 'bullish' or 'bearish' or 'neutral'
    start_idx: int
    end_idx: int
    key_levels: list = field(default_factory=list)  # [(price, label)]
    description: str = ""

@dataclass
class ElliottWave:
    pivots: list         # list of Pivot
    labels: list         # ['0','i','ii','iii','iv','v'] or similar
    fib_ext: dict        # {'1.618': price, '2.618': price}
    wave_complete: bool  # True if 5 waves visible

# === PIVOT DETECTION ===
def find_pivots(data: pd.DataFrame, left: int = 5, right: int = 5) -> list:
    highs = data["High"].values
    lows  = data["Low"].values
    n     = len(highs)
    pivots = []

    for i in range(left, n - right):
        is_high = all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and \
                  all(highs[i] >= highs[i + j] for j in range(1, right + 1))
        is_low  = all(lows[i]  <= lows[i - j]  for j in range(1, left + 1)) and \
                  all(lows[i]  <= lows[i + j]   for j in range(1, right + 1))
        if is_high:
            pivots.append(Pivot(i, float(highs[i]), 'high'))
        elif is_low:
            pivots.append(Pivot(i, float(lows[i]),  'low'))

    # Remove consecutive same-kind pivots (keep most extreme)
    cleaned = []
    for p in pivots:
        if cleaned and cleaned[-1].kind == p.kind:
            if p.kind == 'high' and p.price > cleaned[-1].price:
                cleaned[-1] = p
            elif p.kind == 'low' and p.price < cleaned[-1].price:
                cleaned[-1] = p
        else:
            cleaned.append(p)

    return cleaned

# -- Fibonacci Levels ----------------------------------------------------------
FIB_RETRACE = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXTEND  = [1.0, 1.272, 1.414, 1.618, 2.0, 2.618]

def fib_levels(swing_low: float, swing_high: float) -> dict:
    """Returns dict of {'label': price} for retracements and extensions."""
    diff   = swing_high - swing_low
    levels = {}
    for f in FIB_RETRACE:
        levels[f"{f*100:.1f}%"] = swing_high - diff * f
    for f in FIB_EXTEND:
        levels[f"{f:.3f}x ext"] = swing_low + diff * f
    return levels

# -- Elliott Wave (Simplified) -------------------------------------------------
def elliott_wave(data: pd.DataFrame, pivots: list) -> Optional[ElliottWave]:
    """Attempts to identify a 5-wave impulse from recent pivots."""
    if len(pivots) < 4:
        return None

    recent = pivots[-6:]

    def try_bull(pts):
        if len(pts) < 4:
            return None
        kinds = [p.kind for p in pts]
        expected = ['low','high','low','high','low','high'][:len(pts)]
        if kinds != expected:
            return None
        w1_range = pts[1].price - pts[0].price
        if w1_range <= 0:
            return None
        w2_retrace = (pts[1].price - pts[2].price) / w1_range
        if w2_retrace > 1.0:
            return None
        if len(pts) >= 5:
            w3_range = pts[3].price - pts[2].price
            w4_low   = pts[4].price if len(pts) > 4 else None
            if w4_low and w4_low <= pts[1].price:
                return None
        labels = ['0','i','ii','iii','iv','v'][:len(pts)]
        w1_ext = fib_levels(pts[0].price, pts[1].price)
        base_low  = pts[2].price if len(pts) > 2 else pts[0].price
        base_high = pts[3].price if len(pts) > 3 else pts[1].price
        ext = {
            "1.272": base_low + 1.272 * w1_range,
            "1.414": base_low + 1.414 * w1_range,
            "1.618": base_low + 1.618 * w1_range,
            "2.618": base_low + 2.618 * w1_range,
        }
        return ElliottWave(pivots=pts, labels=labels, fib_ext=ext, wave_complete=(len(pts) >= 6))

    def try_bear(pts):
        if len(pts) < 4:
            return None
        kinds = [p.kind for p in pts]
        expected = ['high','low','high','low','high','low'][:len(pts)]
        if kinds != expected:
            return None
        w1_range = pts[0].price - pts[1].price
        if w1_range <= 0:
            return None
        labels = ['0','i','ii','iii','iv','v'][:len(pts)]
        ext = {
            "1.272": pts[0].price - 1.272 * w1_range,
            "1.414": pts[0].price - 1.414 * w1_range,
            "1.618": pts[0].price - 1.618 * w1_range,
            "2.618": pts[0].price - 2.618 * w1_range,
        }
        return ElliottWave(pivots=pts, labels=labels, fib_ext=ext, wave_complete=(len(pts)>=6))

    for size in [6, 5, 4]:
        pts = recent[-size:]
        ew  = try_bull(pts) or try_bear(pts)
        if ew:
            return ew

    return None

# -- Pattern Detector ----------------------------------------------------------
def detect_patterns(data: pd.DataFrame, pivots: list, lookback: int = 60) -> list:
    """Detects chart patterns. Returns list of Pattern objects sorted by confidence."""
    if len(data) < 20 or len(pivots) < 4:
        return []

    detected = []
    close  = data["Close"].values
    high   = data["High"].values
    low    = data["Low"].values
    n      = len(close)
    recent_pivots = [p for p in pivots if p.idx >= n - lookback]

    def pct_diff(a, b):
        return abs(a - b) / ((a + b) / 2) * 100

    # 1. Head & Shoulders
    peaks  = [p for p in recent_pivots if p.kind == 'high']
    troughs= [p for p in recent_pivots if p.kind == 'low']

    if len(peaks) >= 3:
        for i in range(len(peaks)-2):
            l, m, r = peaks[i], peaks[i+1], peaks[i+2]
            if m.price > l.price and m.price > r.price:
                sym = pct_diff(l.price, r.price)
                if sym < 8:
                    conf = max(0.3, 0.9 - sym/20)
                    neckline = min(
                        min(t.price for t in troughs if l.idx < t.idx < m.idx) if any(l.idx < t.idx < m.idx for t in troughs) else m.price,
                        min(t.price for t in troughs if m.idx < t.idx < r.idx) if any(m.idx < t.idx < r.idx for t in troughs) else m.price,
                    )
                    height = m.price - neckline
                    detected.append(Pattern(
                        name="Head & Shoulders", confidence=conf, direction="bearish",
                        start_idx=l.idx, end_idx=r.idx,
                        key_levels=[(neckline,"Neckline"), (neckline-height,"Target")],
                        description=f"Head at {m.price:.0f}, neckline ~{neckline:.0f}"
                    ))

    if len(troughs) >= 3:
        for i in range(len(troughs)-2):
            l, m, r = troughs[i], troughs[i+1], troughs[i+2]
            if m.price < l.price and m.price < r.price:
                sym = pct_diff(l.price, r.price)
                if sym < 8:
                    conf = max(0.3, 0.9 - sym/20)
                    neckline = max(
                        max(p.price for p in peaks if l.idx < p.idx < m.idx) if any(l.idx < p.idx < m.idx for p in peaks) else m.price,
                        max(p.price for p in peaks if m.idx < p.idx < r.idx) if any(m.idx < p.idx < r.idx for p in peaks) else m.price,
                    )
                    height = neckline - m.price
                    detected.append(Pattern(
                        name="Inverse Head & Shoulders", confidence=conf, direction="bullish",
                        start_idx=l.idx, end_idx=r.idx,
                        key_levels=[(neckline,"Neckline"), (neckline+height,"Target")],
                        description=f"Head at {m.price:.0f}, neckline ~{neckline:.0f}"
                    ))

    # 2. Double Top / Double Bottom
    if len(peaks) >= 2:
        l, r = peaks[-2], peaks[-1]
        if pct_diff(l.price, r.price) < 3 and r.idx - l.idx >= 5:
            valley_troughs = [t for t in troughs if l.idx < t.idx < r.idx]
            valley = min(t.price for t in valley_troughs) if valley_troughs else l.price * 0.97
            height = l.price - valley
            detected.append(Pattern(
                name="Double Top", confidence=0.8, direction="bearish",
                start_idx=l.idx, end_idx=r.idx,
                key_levels=[(valley,"Neckline"), (valley-height,"Target")],
                description=f"Tops at ~{l.price:.0f}, support ~{valley:.0f}"
            ))

    if len(troughs) >= 2:
        l, r = troughs[-2], troughs[-1]
        if pct_diff(l.price, r.price) < 3 and r.idx - l.idx >= 5:
            peak_pivots = [p for p in peaks if l.idx < p.idx < r.idx]
            resistance = max(p.price for p in peak_pivots) if peak_pivots else l.price * 1.03
            height = resistance - l.price
            detected.append(Pattern(
                name="Double Bottom", confidence=0.8, direction="bullish",
                start_idx=l.idx, end_idx=r.idx,
                key_levels=[(resistance,"Neckline"), (resistance+height,"Target")],
                description=f"Bottoms at ~{l.price:.0f}, resistance ~{resistance:.0f}"
            ))

    # Deduplicate
    seen   = {}
    for p in detected:
        if p.name not in seen or p.confidence > seen[p.name].confidence:
            seen[p.name] = p

    return sorted(seen.values(), key=lambda x: -x.confidence)

# -- Drawing Helpers -----------------------------------------------------------
def draw_fib_levels(ax, swing_low: float, swing_high: float, x_start: int, x_end: int, n: int, is_bullish: bool = True):
    """Draw Fibonacci retracement levels on a price axis."""
    diff = swing_high - swing_low
    fibs = [
        (0.0,   "#787B86", "0%",    "--"),
        (0.236, "#F7931A", "23.6%", ":"),
        (0.382, "#FF6D00", "38.2%", ":"),
        (0.5,   "#9C27B0", "50%",   ":"),
        (0.618, "#089981", "61.8%", "-"),
        (0.786, "#2962FF", "78.6%", ":"),
        (1.0,   "#787B86", "100%",  "--"),
    ]
    exts = [
        (1.272, "#26A69A", "127.2%"),
        (1.414, "#089981", "141.4%"),
        (1.618, "#089981", "161.8%"),
        (2.618, "#F23645", "261.8%"),
    ]
    for frac, col, lbl, ls in fibs:
        price = swing_high - diff * frac
        ax.axhline(price, color=col, linestyle=ls, linewidth=0.8, alpha=0.7, zorder=2)
        ax.annotate(f"{lbl} ({price:,.1f})  ", xy=(-0.001, price),
            xycoords=("axes fraction","data"), color=col,
            fontsize=6.5, va="center", fontweight="bold", zorder=6)

    for frac, col, lbl in exts:
        price = swing_low + diff * frac
        ax.axhline(price, color=col, linestyle="--", linewidth=0.9, alpha=0.7, zorder=2)
        ax.annotate(f"{lbl} ({price:,.1f})  ", xy=(-0.001, price),
            xycoords=("axes fraction","data"), color=col,
            fontsize=6.5, va="center", ha="right", fontweight="bold", zorder=6)

def draw_ew_labels(ax, ew: ElliottWave, data: pd.DataFrame):
    """Draw Elliott Wave pivot labels as circled Roman numerals."""
    if not ew:
        return
    for pivot, label in zip(ew.pivots, ew.labels):
        x = pivot.idx
        y = pivot.price
        offset = data["Close"].values[-1] * 0.015
        y_txt  = y + offset if pivot.kind == 'high' else y - offset
        ax.annotate(
            label, xy=(x, y), xytext=(x, y_txt),
            fontsize=8, color=TV_BLUE, fontweight="bold", ha="center",
            va="bottom" if pivot.kind == 'low' else "top",
            bbox=dict(boxstyle="circle,pad=0.25", facecolor="white",
                edgecolor=TV_BLUE, linewidth=1.2, alpha=0.9),
            zorder=10,
        )
    xs = [p.idx for p in ew.pivots]
    ys = [p.price for p in ew.pivots]
    ax.plot(xs, ys, color=TV_BLUE, linewidth=1.2, linestyle="-", alpha=0.6, zorder=9)

def draw_pattern(ax, pattern: Pattern, data: pd.DataFrame, alpha: float = 0.5):
    """Highlight a detected pattern on the price axis."""
    if not pattern:
        return
    close = data["Close"].values
    si, ei = max(0, pattern.start_idx), min(len(close)-1, pattern.end_idx)
    price_range = close[si:ei+1] if ei > si else close[si:si+2]
    if len(price_range) == 0:
        return
    y_min = float(np.min(data["Low"].values[si:ei+1]))
    y_max = float(np.max(data["High"].values[si:ei+1]))

    edge_col = TV_GREEN if pattern.direction=="bullish" else (TV_RED if pattern.direction=="bearish" else TV_GRAY)
    ax.axvspan(si, ei, alpha=0.07, color=edge_col, zorder=1)

    for price, lbl in pattern.key_levels[:2]:
        ax.axhline(price, color=edge_col, linestyle="--", linewidth=0.8, alpha=0.6, zorder=3)

    mid_x = (si + ei) / 2
    y_lbl = y_max * 1.003 if pattern.direction in ("bullish","neutral") else y_min * 0.997
    ax.text(mid_x, y_lbl, pattern.name,
        color=edge_col, fontsize=7, ha="center", va="bottom" if pattern.direction!="bearish" else "top",
        fontweight="bold", alpha=0.85, zorder=8,
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor=edge_col, alpha=0.9, linewidth=0.8))

# === MAIN EXECUTION ===
try:
    import yfinance as yf
    import mplfinance as mpf
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

OUT_DIR  = "output"
os.makedirs(OUT_DIR, exist_ok=True)

import time as _time

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

# Fix #7: Use nifty500_collector symbol pool (250 stocks) instead of 22 hardcoded.
# Falls back to original 22 if import fails.
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from nifty500_collector import SECTOR_STOCKS as _SECTOR_STOCKS
    _all_syms = sorted({s for stocks in _SECTOR_STOCKS.values() for s in stocks})
    UNIVERSE = [(f"{s}.NS", s.title().replace("."," ")) for s in _all_syms]
    print(f"SCAN: Universe loaded from nifty500_collector: {len(UNIVERSE)} stocks")
except Exception as _e:
    print(f"SCAN: nifty500_collector unavailable ({_e}), using default 22-stock list")
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
    ]

def last_cross(arr, window):
    for i in range(1, min(window+1, len(arr))):
        idx = len(arr)-i
        if np.isnan(arr[idx]) or np.isnan(arr[idx-1]): continue
        if arr[idx]>0 and arr[idx-1]<=0: return i, +1
        if arr[idx]<0 and arr[idx-1]>=0: return i, -1
    return None, 0

def score_symbol(sym, name):
    try:
        df = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df)<55: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open","High","Low","Close","Volume"])
        if len(df)<55: return None
        c = df["Close"]
        e9  = c.ewm(span=9,  adjust=False).mean()
        e21 = c.ewm(span=21, adjust=False).mean()
        s20 = c.rolling(20).mean()
        s50 = c.rolling(50).mean()
        ema_diff = e9.values - e21.values
        dma_diff = (s20 - s50).values
        eb, ed = last_cross(ema_diff, 3)
        db, dd = last_cross(dma_diff, 5)
        score = 0; reasons = []
        if ed==+1: score+=5; reasons.append(f"EMA 9/21 Bullish Cross {eb}d ago")
        elif ed==-1: score-=5; reasons.append(f"EMA 9/21 Bearish Cross {eb}d ago")
        if dd==+1: score+=4; reasons.append(f"SMA 20/50 Bullish Cross {db}d ago")
        elif dd==-1: score-=4; reasons.append(f"SMA 20/50 Bearish Cross {db}d ago")
        e9l=float(e9.iloc[-1]); e21l=float(e21.iloc[-1])
        score += 1 if e9l>e21l else -1
        reason = reasons[0] if reasons else ("EMA bullish" if e9l>e21l else "EMA bearish")
        return {"sym":sym,"name":name,"score":score,"ed":ed,"dd":dd,"reason":reason,"close":float(c.iloc[-1])}
    except:
        return None

# Fix #10: Accept optional period arg — /chart INFY 3mo or /chart INFY 1y
VALID_PERIODS = {"1mo","3mo","6mo","1y","2y"}
CHART_PERIOD  = "6mo"   # default
if len(sys.argv)>=3:
    forced_sym=sys.argv[1]; forced_name=sys.argv[2]
    if len(sys.argv)>=4 and sys.argv[3] in VALID_PERIODS:
        CHART_PERIOD = sys.argv[3]
    winner={"sym":forced_sym,"name":forced_name,"score":0,"reason":"Manual pick"}
    print(f"SCAN: skipped (manual), period={CHART_PERIOD}")
else:
    candidates=[]; checked=0
    import time
    for i in range(0, len(UNIVERSE), 10):
        batch = UNIVERSE[i:i+10]
        for sym, name in batch:
            r = score_symbol(sym, name); checked+=1
            if r and (r["ed"]!=0 or r["dd"]!=0): candidates.append(r)
        time.sleep(0.4)
        if len(candidates)>=12: break
    if not candidates:
        for sym, name in UNIVERSE:
            r = score_symbol(sym, name); checked+=1
            if r: candidates.append(r)
    print(f"SCAN: checked {checked}, candidates {len(candidates)}")
    if not candidates:
        print("[ERROR] No candidates", file=sys.stderr); sys.exit(1)
    candidates.sort(key=lambda r: (-(1 if r["ed"]!=0 or r["dd"]!=0 else 0), -abs(r["score"]), -r["score"]))
    winner = candidates[0]
    print(f"WINNER: {winner['sym']}|{winner['name']}|{winner['reason']}|{winner['score']:+d}")
    forced_sym=winner["sym"]; forced_name=winner["name"]

symbol=forced_sym; company_name=forced_name

# Fix #1: Per-symbol timestamped filename — no race condition between concurrent requests
_sym_safe = symbol.replace(".NS","").replace(".BO","").upper()
OUT_FILE = os.path.join(OUT_DIR, f"chart_{_sym_safe}_{int(_time.time())}.png")

data = yf.download(symbol, period=CHART_PERIOD, interval="1d", progress=False, auto_adjust=True)
if data.empty:
    print(f"[ERROR] No data for {symbol}", file=sys.stderr); sys.exit(1)
if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
data = data.dropna(subset=["Open","High","Low","Close","Volume"])

close_s=data["Close"]; vol_s=data["Volume"]
close=close_s.values; n=len(close)

ema9=close_s.ewm(span=9,adjust=False).mean(); ema21=close_s.ewm(span=21,adjust=False).mean()
sma20=close_s.rolling(20).mean(); sma50=close_s.rolling(50).mean()
ema12=close_s.ewm(span=12,adjust=False).mean(); ema26=close_s.ewm(span=26,adjust=False).mean()
macd=ema12-ema26; macd_sig=macd.ewm(span=9,adjust=False).mean(); hist=macd-macd_sig
vol_ma20=vol_s.rolling(20).mean()

def calc_rsi(prices, period=14):
    d=np.diff(prices); g=np.where(d>0,d,0.0); l=np.where(d<0,-d,0.0)
    ag=np.convolve(g,np.ones(period)/period,"full")[:len(g)]
    al=np.convolve(l,np.ones(period)/period,"full")[:len(l)]
    rs=np.where(al!=0,ag/al,100.0)
    return np.concatenate([[np.nan]*period,(100-100/(1+rs))[period-1:]])

rsi_s=pd.Series(calc_rsi(close)[:n],index=data.index)

def build_cross(fast, slow):
    diff=fast.values-slow.values; b=[np.nan]; r=[np.nan]
    for i in range(1,len(diff)):
        if diff[i]>0 and diff[i-1]<=0: b.append(float(data["Low"].iloc[i])*0.997); r.append(np.nan)
        elif diff[i]<0 and diff[i-1]>=0: r.append(float(data["High"].iloc[i])*1.003); b.append(np.nan)
        else: b.append(np.nan); r.append(np.nan)
    return pd.Series(b,index=data.index), pd.Series(r,index=data.index)

eb_s,er_s=build_cross(ema9,ema21); db_s,dr_s=build_cross(sma20,sma50)

recent=data.tail(20); support=float(recent["Low"].min()); resistance=float(recent["High"].max())
last_close=float(close[-1]); prev_close=float(close[-2]) if n>=2 else last_close
change_pct=(last_close-prev_close)/prev_close*100

e9l=float(ema9.iloc[-1]); e21l=float(ema21.iloc[-1])
rsi_last=float(rsi_s.dropna().iloc[-1]); hist_last=float(hist.dropna().iloc[-1])
lv=float(vol_s.iloc[-1]); av=float(vol_ma20.dropna().iloc[-1]) if vol_ma20.dropna().shape[0]>0 else lv
vr=lv/av if av>0 else 1.0

checks=[]
checks.append(("EMA 9 > EMA 21" if e9l>e21l else "EMA 9 < EMA 21",+1 if e9l>e21l else -1,"#089981" if e9l>e21l else "#F23645"))
s20l=float(sma20.dropna().iloc[-1]) if sma20.dropna().shape[0]>0 else e9l
s50l=float(sma50.dropna().iloc[-1]) if sma50.dropna().shape[0]>0 else e21l
checks.append(("SMA 20 > SMA 50" if s20l>s50l else "SMA 20 < SMA 50",+1 if s20l>s50l else -1,"#089981" if s20l>s50l else "#F23645"))
checks.append((f"RSI {rsi_last:.0f} Bullish" if rsi_last>55 else (f"RSI {rsi_last:.0f} Bearish" if rsi_last<45 else f"RSI {rsi_last:.0f} Neutral"),+1 if rsi_last>55 else (-1 if rsi_last<45 else 0),"#089981" if rsi_last>55 else ("#F23645" if rsi_last<45 else "#787B86")))
checks.append(("MACD Hist +" if hist_last>0 else "MACD Hist -",+1 if hist_last>0 else -1,"#089981" if hist_last>0 else "#F23645"))
checks.append(("Price > EMA 21" if last_close>e21l else "Price < EMA 21",+1 if last_close>e21l else -1,"#089981" if last_close>e21l else "#F23645"))

score=sum(s for _,s,_ in checks)
if   score>=4: signal_text,sig_color="STRONG BUY","#089981"
elif score>=2: signal_text,sig_color="BUY","#26A69A"
elif score<=-4:signal_text,sig_color="STRONG SELL","#F23645"
elif score<=-2:signal_text,sig_color="SELL","#EF5350"
else:          signal_text,sig_color="NEUTRAL","#787B86"
score_str=f"{score:+d}/5"; is_bull=score>=0

# Fix #9: Compute ATR(14) for display in advisory panel
_high_v = data["High"].values; _low_v = data["Low"].values; _close_v = data["Close"].values
_tr = np.maximum(_high_v[1:]-_low_v[1:], np.maximum(np.abs(_high_v[1:]-_close_v[:-1]), np.abs(_low_v[1:]-_close_v[:-1])))
atr_val = round(float(np.mean(_tr[-14:])), 2) if len(_tr) >= 14 else round(float(np.mean(_tr)), 2)

if is_bull:
    sl_val=min(float(data["Low"].tail(5).min()),e21l)*0.997; sl_pct=(last_close-sl_val)/last_close*100
    t1_val=last_close+1.5*(last_close-sl_val); t2_val=last_close+2.5*(last_close-sl_val)
    t1_pct=(t1_val-last_close)/last_close*100; t2_pct=(t2_val-last_close)/last_close*100; sl_dp=-sl_pct
else:
    sl_val=max(float(data["High"].tail(5).max()),e21l)*1.003; sl_pct=(sl_val-last_close)/last_close*100
    t1_val=last_close-1.5*(sl_val-last_close); t2_val=last_close-2.5*(sl_val-last_close)
    t1_pct=(last_close-t1_val)/last_close*100; t2_pct=(last_close-t2_val)/last_close*100; sl_dp=sl_pct

mc=mpf.make_marketcolors(up="#089981",down="#F23645",wick={"up":"#089981","down":"#F23645"},
    edge={"up":"#089981","down":"#F23645"},volume={"up":"#C3EFEB","down":"#FBCDD0"})
tv=mpf.make_mpf_style(marketcolors=mc,facecolor="#FFFFFF",edgecolor="#E0E3EB",figcolor="#FFFFFF",
    gridcolor="#F0F3FA",gridstyle="-",rc={"axes.labelcolor":"#131722","xtick.color":"#787B86",
    "ytick.color":"#787B86","text.color":"#131722","font.family":"DejaVu Sans","font.size":9,
    "axes.spines.top":False,"axes.spines.right":False})

ticker=symbol.replace(".NS","").replace(".BO",""); 
from datetime import datetime
today=datetime.now().strftime("%d %b %Y")
title=f"{ticker}  |  {company_name}  |  Rs.{last_close:,.1f}  {'UP' if change_pct>=0 else 'DN'} {change_pct:+.2f}%  |  {today}"
hc=["#089981" if v>=0 else "#F23645" for v in hist.fillna(0)]

apds=[
    mpf.make_addplot(ema9,color="#F7931A",width=1.6),mpf.make_addplot(ema21,color="#2962FF",width=1.6),
    mpf.make_addplot(sma20,color="#9C27B0",width=1.1,linestyle="--"),mpf.make_addplot(sma50,color="#FF6D00",width=1.1,linestyle="--"),
    mpf.make_addplot([support]*n,color="#089981",linestyle=":",width=0.9),mpf.make_addplot([resistance]*n,color="#F23645",linestyle=":",width=0.9),
    mpf.make_addplot(macd,panel=2,color="#2962FF",width=1.1,ylabel="MACD"),mpf.make_addplot(macd_sig,panel=2,color="#FF6D00",width=1.1),mpf.make_addplot([0]*n,panel=2,color="#787B86",linestyle="--",width=0.7),
    mpf.make_addplot(hist,panel=2,type="bar",color=hc,alpha=0.55),
    mpf.make_addplot(rsi_s,panel=3,color="#9C27B0",width=1.2,ylabel="RSI"),
    mpf.make_addplot([70]*n,panel=3,color="#F2364566",linestyle="--",width=0.7),
    mpf.make_addplot([30]*n,panel=3,color="#08998166",linestyle="--",width=0.7),
    mpf.make_addplot([50]*n,panel=3,color="#78788055",linestyle=":",width=0.6),
]

try:
    fig, axes = mpf.plot(data, type="candle", style=tv, addplot=apds, title="\n"+title,
        figratio=(16,11), figscale=1.3, volume=True, panel_ratios=(5,1,2,2), returnfig=True, tight_layout=False)
    
    ax0=axes[0]
    # mplfinance panel layout: axes[0]=price, axes[1]=volume, axes[2]=MACD, axes[3]=RSI
    # axes list may include twin-axes so filter by panel ylabel
    _all_axes = fig.get_axes()
    ax2 = _all_axes[2] if len(_all_axes) > 2 else None   # MACD
    ax3 = _all_axes[3] if len(_all_axes) > 3 else None   # RSI

    # Fix #4: RSI zone shading — red above 70, green below 30
    if ax3 is not None:
        _rsi_vals = rsi_s.fillna(50).values
        _xs = range(len(_rsi_vals))
        ax3.fill_between(_xs, _rsi_vals, 70, where=(_rsi_vals > 70), color="#F23645", alpha=0.15, zorder=2)
        ax3.fill_between(_xs, _rsi_vals, 30, where=(_rsi_vals < 30), color="#089981", alpha=0.15, zorder=2)

    # Fix #3: Volume spike callout — orange vertical line when vol > 2x 20d avg
    _vol_vals  = vol_s.values
    _vma_vals  = vol_ma20.fillna(method="bfill").values
    _ax_vol    = _all_axes[1] if len(_all_axes) > 1 else None
    if _ax_vol is not None:
        for _vi, (_v, _vm) in enumerate(zip(_vol_vals, _vma_vals)):
            if _vm > 0 and _v > 2.0 * _vm:
                _ax_vol.axvline(_vi, color="#FF6D00", lw=1.2, alpha=0.65, zorder=4)
                ax0.axvline(_vi, color="#FF6D00", lw=0.7, alpha=0.30, zorder=3)
    pivots=find_pivots(data,left=5,right=5); ew=elliott_wave(data,pivots)
    patterns=detect_patterns(data,pivots,lookback=80); top_pat=patterns[0] if patterns else None
    al=sorted(pivots,key=lambda p:p.price); ah=sorted(pivots,key=lambda p:-p.price)
    fib_lo=al[0].price if al else float(data["Low"].min()); fib_hi=ah[0].price if ah else float(data["High"].max())
    draw_fib_levels(ax0,fib_lo,fib_hi,0,n-1,n)
    if ew: draw_ew_labels(ax0,ew,data)
    if top_pat: draw_pattern(ax0,top_pat,data)

    ax0.annotate(f" S: {support:,.0f}",xy=(1.001,support),xycoords=("axes fraction","data"),color="#089981",fontsize=7.5,va="center",fontweight="bold")
    ax0.annotate(f" R: {resistance:,.0f}",xy=(1.001,resistance),xycoords=("axes fraction","data"),color="#F23645",fontsize=7.5,va="center",fontweight="bold")
    leg=[mlines.Line2D([],[],color="#F7931A",lw=2,label="EMA 9"),mlines.Line2D([],[],color="#2962FF",lw=2,label="EMA 21"),
        mlines.Line2D([],[],color="#9C27B0",lw=1.2,ls="--",label="SMA 20"),mlines.Line2D([],[],color="#FF6D00",lw=1.2,ls="--",label="SMA 50"),
        plt.scatter([],[],marker="^",color="#2962FF",s=55,label="EMA Bull"),plt.scatter([],[],marker="v",color="#F23645",s=55,label="EMA Bear"),
        plt.scatter([],[],marker="*",color="#FF6D00",s=70,label="SMA Bull"),plt.scatter([],[],marker="*",color="#F23645",s=70,label="SMA Bear")]
    ax0.legend(handles=leg,loc="upper left",fontsize=7,facecolor="white",edgecolor="#E0E3EB",framealpha=0.95,labelcolor="#131722",ncol=2)

    fw,fh=fig.get_size_inches(); extra=4.2; fig.set_size_inches(fw+extra,fh)
    ratio=fw/(fw+extra)
    for ax_i in fig.get_axes():
        p=ax_i.get_position(); ax_i.set_position([p.x0*ratio,p.y0,p.width*ratio,p.height])

    sa=fig.add_axes([ratio+0.005,0.06,1.0-ratio-0.015,0.87])
    sa.set_xlim(0,1); sa.set_ylim(0,1); sa.axis("off")
    bg_map={"STRONG BUY":"#E8F8F3","BUY":"#F0FBF8","NEUTRAL":"#F5F5F7","SELL":"#FFF0F0","STRONG SELL":"#FFE8E8"}
    sa.add_patch(FancyBboxPatch((0.03,0.02),0.94,0.96,boxstyle="round,pad=0.015",lw=2,edgecolor=sig_color,facecolor=bg_map.get(signal_text,"#F5F5F7"),transform=sa.transAxes,zorder=1))
    hmap={"STRONG BUY":"++ STRONG BUY","BUY":"+ BUY","NEUTRAL":"~ NEUTRAL","SELL":"- SELL","STRONG SELL":"-- STRONG SELL"}
    sa.text(0.5,0.985,"BEST PICK TODAY",transform=sa.transAxes,fontsize=7.5,color="#787B86",ha="center",va="top",style="italic",zorder=5)
    sa.text(0.5,0.950,hmap.get(signal_text,signal_text),transform=sa.transAxes,fontsize=12,fontweight="bold",color=sig_color,ha="center",va="top",zorder=5)
    sa.text(0.5,0.903,f"Score: {score_str}",transform=sa.transAxes,fontsize=9.5,color=sig_color,ha="center",va="top",zorder=5)
    sa.plot([0.05,0.95],[0.877,0.877],color="#CCCCCC",lw=1.0,transform=sa.transAxes,zorder=5)
    win_reason=winner.get("reason","Best crossover")
    sa.text(0.5,0.860,win_reason,transform=sa.transAxes,fontsize=8,color=sig_color,ha="center",va="top",fontweight="bold",zorder=5)
    sa.plot([0.05,0.95],[0.836,0.836],color="#CCCCCC",lw=0.8,transform=sa.transAxes,zorder=5)
    T="+"; X="x"; D="-"
    row_top=0.808; row_gap=0.108
    for i,(label,s,col) in enumerate(checks):
        y=row_top-i*row_gap; ic=T if s>0 else (X if s<0 else D)
        sa.text(0.06,y,ic,transform=sa.transAxes,fontsize=11,fontweight="bold",color=col,va="top",zorder=5)
        sa.text(0.19,y,label,transform=sa.transAxes,fontsize=8.2,color=col,va="top",zorder=5)
    dy=row_top-len(checks)*row_gap+0.05
    sa.plot([0.05,0.95],[dy,dy],color="#CCCCCC",lw=1.0,transform=sa.transAxes,zorder=5)
    tl_top=dy-0.015; tl_gap=0.088
    # Fix #8: Risk:Reward ratio
    _risk = abs(last_close - sl_val)
    _rw1  = abs(t1_val - last_close)
    _rw2  = abs(t2_val - last_close)
    _rr1  = round(_rw1 / _risk, 1) if _risk > 0 else 0
    _rr2  = round(_rw2 / _risk, 1) if _risk > 0 else 0
    # Fix #9: ATR row above entry
    _atr_y = tl_top + tl_gap
    sa.text(0.06, _atr_y, "ATR(14)", transform=sa.transAxes, fontsize=8.5, color="#787B86", va="top", zorder=5)
    sa.text(0.56, _atr_y, f"Rs.{atr_val:,.1f}", transform=sa.transAxes, fontsize=8.5, color="#787B86", va="top", ha="right", zorder=5, fontfamily="monospace")
    trows=[("Entry",last_close,None,None,"#131722",True),("Stop Loss",sl_val,sl_dp,None,"#F23645",False),("Target 1",t1_val,t1_pct,_rr1,"#089981",False),("Target 2",t2_val,t2_pct,_rr2,"#089981",False)]
    for i,(lbl,price,pct,rr,col,bold) in enumerate(trows):
        y=tl_top-i*tl_gap; fw2="bold" if bold else "normal"
        sa.text(0.06,y,lbl,transform=sa.transAxes,fontsize=8.5,color="#131722",va="top",fontweight=fw2,zorder=5)
        sa.text(0.56,y,f"Rs.{price:,.1f}",transform=sa.transAxes,fontsize=8.5,color=col,va="top",fontweight=fw2,ha="right",zorder=5,fontfamily="monospace")
        if pct is not None and rr is not None:
            sa.text(0.97,y,f"({pct:+.1f}% | R:R 1:{rr})",transform=sa.transAxes,fontsize=7.2,color=col,va="top",ha="right",zorder=5)
        elif pct is not None:
            sa.text(0.97,y,f"({pct:+.1f}%)",transform=sa.transAxes,fontsize=8,color=col,va="top",ha="right",zorder=5)

    fig.text(0.38,0.003,"AI-generated. Not SEBI registered. Not financial advice.",ha="center",va="bottom",color="#B2B5BE",fontsize=6.5)
    fig.text(0.99,0.003,"AutoAiAdvisory (SK)",ha="right",va="bottom",color="#B2B5BE",fontsize=6.5,fontweight="bold",style="italic")
    
    # CRITICAL: Save with explicit figure method
    fig.savefig(OUT_FILE, dpi=150, bbox_inches="tight", facecolor="white", format='png')
    plt.close(fig)
    
    print(f"OUTPUT: {OUT_FILE}")
    print(f"META: {symbol}|{company_name}|{signal_text}|{score_str}|{last_close:.2f}|{sl_val:.2f}|{t1_val:.2f}|{t2_val:.2f}")

except Exception as e:
    print(f"[ERROR] Chart generation failed: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
