"""
swing_trades.py — Swing Trade Scanner v6.0
Team Sprint Upgrades:
  1. Weekly EMA alignment check (+2) — swing trades aligned with weekly trend
  2. Sector EMA vs Nifty bias check — shows sector context
  3. ATR-based SL/T1/T2 (1.2×ATR SL, 2×ATR T1, 3.5×ATR T2)
  4. Signal-sorted selection — picks highest-scoring stocks, not round-robin
  5. Min score gate: need ≥6/10 for BUY, ≥5/10 for SHORT
  6. Supertrend(7,3) filter — directional confirmation +1
  7. Rich trade card with sector, weekly trend, ATR-based levels
"""

import os, logging
import numpy as np
from datetime import date
import pandas as pd

logger = logging.getLogger(__name__)

from data_engine import get_hist
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_adx, calc_bollinger,
    ema_series, rsi_series,
)
from config import RSI_PERIOD, ADX_PERIOD, ATR_PERIOD, HIST_PERIOD_SWING, TG_CHUNK_SIZE

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


# ── CANDIDATE UNIVERSE ────────────────────────────────────────────────────────
try:
    from nifty500_collector import SECTOR_STOCKS as _SC
    _seen = set()
    CANDIDATES = []
    CANDIDATE_SECTORS = {}
    for _sector, _syms in _SC.items():
        for _s in _syms:
            if _s not in _seen:
                CANDIDATES.append(f"{_s}.NS")
                CANDIDATE_SECTORS[f"{_s}.NS"] = _sector
                _seen.add(_s)
            if len(CANDIDATES) >= 60:
                break
        if len(CANDIDATES) >= 60:
            break
    logger.info(f"Swing v6: {len(CANDIDATES)} candidates, {len(set(CANDIDATE_SECTORS.values()))} sectors")
except Exception as _e:
    logger.warning(f"Swing: nifty500_collector unavailable: {_e}")
    CANDIDATES = [
        "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
        "ITC.NS","SBIN.NS","BHARTIARTL.NS","KOTAKBANK.NS","LT.NS",
        "WIPRO.NS","HCLTECH.NS","ASIANPAINT.NS","MARUTI.NS","TATAMOTORS.NS",
        "TITAN.NS","SUNPHARMA.NS","ONGC.NS","NTPC.NS","M&M.NS",
        "BAJFINANCE.NS","AXISBANK.NS","TECHM.NS","DRREDDY.NS","DIVISLAB.NS",
        "HINDALCO.NS","JSWSTEEL.NS","TATASTEEL.NS","BPCL.NS","EICHERMOT.NS",
    ]
    CANDIDATE_SECTORS = {}


def safe_history(ticker, period="6mo", interval="1d"):
    sym = ticker.replace(".NS","").replace(".NSE","")
    return get_hist(sym, period=period)


# ── SUPERTREND(7,3) ────────────────────────────────────────────────────────────
def calc_supertrend(df, period=7, multiplier=3):
    """Returns last Supertrend direction: +1 bullish, -1 bearish"""
    try:
        h, l, c = df["High"].values, df["Low"].values, df["Close"].values
        if len(c) < period + 2:
            return 0
        tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
        atr = pd.Series(tr).rolling(period).mean().values
        hl2 = (h[1:] + l[1:]) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        close = c[1:]
        direction = np.ones(len(close))
        for i in range(1, len(close)):
            if close[i] > upper[i-1]:
                direction[i] = 1
            elif close[i] < lower[i-1]:
                direction[i] = -1
            else:
                direction[i] = direction[i-1]
        return int(direction[-1])
    except Exception:
        return 0


# ── WEEKLY TREND CHECK ────────────────────────────────────────────────────────
def get_weekly_trend(sym):
    """
    Returns (direction, label):
      +2 = weekly bullish aligned
      -2 = weekly bearish confirmed
       0 = sideways / no data
    """
    try:
        if not _YF_AVAILABLE:
            return 0, "Weekly: N/A"
        wdf = yf.download(sym, period="1y", interval="1wk",
                          progress=False, auto_adjust=True)
        if isinstance(wdf.columns, pd.MultiIndex):
            wdf.columns = wdf.columns.get_level_values(0)
        wdf = wdf.dropna(subset=["Close"])
        if len(wdf) < 10:
            return 0, "Weekly: Insufficient data"
        wc    = wdf["Close"]
        we9   = wc.ewm(span=9,  adjust=False).mean()
        we21  = wc.ewm(span=21, adjust=False).mean()
        wltp  = float(wc.iloc[-1])
        we9l  = float(we9.iloc[-1])
        we21l = float(we21.iloc[-1])
        if wltp > we9l > we21l:
            return +2, "Weekly BULLISH (EMA9&gt;EMA21) ✓"
        elif wltp < we9l < we21l:
            return -2, "Weekly BEARISH (EMA9&lt;EMA21) ✓"
        else:
            return 0, "Weekly SIDEWAYS"
    except Exception as e:
        logger.debug(f"weekly trend {sym}: {e}")
        return 0, "Weekly: N/A"


# ── SECTOR BIAS ───────────────────────────────────────────────────────────────
def get_sector_bias(sector):
    """
    Compare sector ETF vs Nifty. Quick heuristic using known ETF tickers.
    Returns label string for display.
    """
    SECTOR_ETF = {
        "IT":           "^CNXIT",
        "Banking":      "^NSEBANK",
        "Pharma":       "PHARMA.NS",
        "Auto":         "CNXAUTO.NS",
        "FMCG":         "^CNXFMCG",
        "Metal":        "^CNXMETAL",
        "Energy":       "CNXENERGY.NS",
        "Infrastructure":"CNXINFRA.NS",
    }
    etf = SECTOR_ETF.get(sector)
    if not etf or not _YF_AVAILABLE:
        return f"Sector: {sector}"
    try:
        sd = yf.download(etf, period="1mo", interval="1d", progress=False, auto_adjust=True)
        if sd.empty or len(sd) < 5:
            return f"Sector: {sector}"
        sc    = sd["Close"]
        se9   = sc.ewm(span=9, adjust=False).mean()
        sltp  = float(sc.iloc[-1])
        se9l  = float(se9.iloc[-1])
        icon  = "↑" if sltp > se9l else "↓"
        return f"Sector {sector}: {icon} {'Bullish' if sltp>se9l else 'Bearish'}"
    except Exception:
        return f"Sector: {sector}"


# ── WEIGHTED SWING SCORE (10 checks) ─────────────────────────────────────────
def swing_score(df, side="LONG", sym=None):
    """
    10-check weighted swing scoring. Max ~13 pts.
    Min gate: LONG ≥6, SHORT ≥5.
    """
    if df.empty or len(df) < 50:
        return {"score": 0, "details": [], "ltp": None}

    close   = df["Close"]
    ltp     = float(close.iloc[-1])
    n       = len(close)

    ema50   = float(ema_series(close, min(50,  n-1)).iloc[-1])
    ema200  = float(ema_series(close, min(200, n-1)).iloc[-1])
    bb_mid, bb_upper, bb_lower = calc_bollinger(close, 20, 2)
    adx_last, plus_di, minus_di = calc_adx(df, ADX_PERIOD)
    rsi_val = calc_rsi(close, RSI_PERIOD)
    macd_last, signal_last, hist_last = calc_macd(close)
    vol_avg  = float(df["Volume"].rolling(20).mean().iloc[-1])
    vol_last = float(df["Volume"].iloc[-1])
    recent_high = float(close.rolling(20).max().iloc[-1])
    recent_low  = float(close.rolling(20).min().iloc[-1])

    h, l, c = df["High"], df["Low"], df["Close"]
    tr      = pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    atr_val = float(tr.rolling(14).mean().iloc[-1])

    # RSI momentum (slope)
    rsi_arr   = rsi_series(close, RSI_PERIOD).values
    rsi_slope = float(rsi_arr[-1] - rsi_arr[-3]) if len(rsi_arr) >= 3 else 0

    # MACD histogram slope
    try:
        from technical_indicators import calc_macd as _cm
        _m, _s, _h = _cm(close)
    except Exception:
        _h = hist_last
    hist_vals = []
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_s = ema12 - ema26
        sig_s  = macd_s.ewm(span=9, adjust=False).mean()
        hist_s = (macd_s - sig_s).dropna().values
        hist_vals = hist_s[-3:].tolist() if len(hist_s) >= 3 else []
    except Exception:
        hist_vals = []

    # Supertrend
    st_dir = calc_supertrend(df)

    # Weekly trend (only if sym given)
    wk_score, wk_label = 0, "Weekly: skipped"
    if sym:
        wk_score, wk_label = get_weekly_trend(sym)

    conditions = []
    score      = 0

    if side == "LONG":
        # ── CHECK 1: Trend alignment (EMA structure) ──
        if ltp > ema50:
            score += 1
            conditions.append("Price &gt; EMA 50 ✓")
            if ema50 > ema200:
                score += 1
                conditions.append("EMA50 &gt; EMA200 (Golden cross zone) ✓")

        # ── CHECK 2: MACD slope-aware (+2 expanding, +1 flat-pos) ──
        if len(hist_vals) >= 3 and hist_vals[-1] > 0:
            expanding = hist_vals[-1] > hist_vals[-2] > hist_vals[-3]
            macd_pts  = 2 if expanding else 1
            score += macd_pts
            lbl = f"MACD {'Expanding' if expanding else 'Positive'} +{macd_pts}pts"
            conditions.append(lbl)

        # ── CHECK 3: RSI zone + momentum (+2 if rising, +1 if flat) ──
        if 40 < rsi_val < 70:
            rsi_pts = 2 if rsi_slope > 2 else 1
            score += rsi_pts
            conditions.append(f"RSI {rsi_val:.1f} zone {'↑' if rsi_slope>2 else '→'} +{rsi_pts}pts")

        # ── CHECK 4: ADX strength ≥25 scored (+1) ──
        if adx_last > 25 and plus_di > minus_di:
            score += 1
            conditions.append(f"ADX {adx_last:.1f} &gt;25, +DI&gt;-DI ✓")

        # ── CHECK 5: Volume confirmation ──
        if vol_last > 1.5 * vol_avg:
            score += 1
            conditions.append(f"Volume {vol_last/1e5:.1f}L = {vol_last/vol_avg:.1f}x avg ✓")

        # ── CHECK 6: Breakout zone ──
        if ltp > recent_high * 0.97:
            score += 1
            conditions.append("Near 20d high — breakout zone ✓")

        # ── CHECK 7: BB position ──
        if bb_lower < ltp < bb_mid:
            score += 1
            conditions.append("BB: price in lower-mid zone (good entry) ✓")

        # ── CHECK 8: Supertrend ──
        if st_dir == 1:
            score += 1
            conditions.append("Supertrend(7,3) BULLISH ✓")

        # ── CHECK 9: Weekly trend alignment (+2) ──
        if wk_score > 0:
            score += wk_score
            conditions.append(f"{wk_label} +{wk_score}pts ✓")
        elif wk_score < 0:
            conditions.append(f"{wk_label} ⚠ (against daily)")

        # ── CHECK 10: HH/HL structure ──
        closes = close.values
        if len(closes) >= 20:
            hh = closes[-1] > closes[-10:].max() * 0.98
            if hh:
                score += 1
                conditions.append("Price near recent highs — uptrend structure ✓")

    else:  # SHORT
        if ltp < ema50:
            score += 1
            conditions.append("Price &lt; EMA 50 ✓")
            if ema50 < ema200:
                score += 1
                conditions.append("EMA50 &lt; EMA200 (Death cross zone) ✓")

        if len(hist_vals) >= 3 and hist_vals[-1] < 0:
            expanding = hist_vals[-1] < hist_vals[-2] < hist_vals[-3]
            macd_pts  = 2 if expanding else 1
            score += macd_pts
            conditions.append(f"MACD {'Expanding -' if expanding else 'Negative'} +{macd_pts}pts")

        if 30 < rsi_val < 60:
            rsi_pts = 2 if rsi_slope < -2 else 1
            score += rsi_pts
            conditions.append(f"RSI {rsi_val:.1f} zone {'↓' if rsi_slope<-2 else '→'} +{rsi_pts}pts")

        if adx_last > 25 and minus_di > plus_di:
            score += 1
            conditions.append(f"ADX {adx_last:.1f} &gt;25, -DI&gt;+DI ✓")

        if vol_last > 1.5 * vol_avg:
            score += 1
            conditions.append(f"Volume {vol_last/1e5:.1f}L = {vol_last/vol_avg:.1f}x avg ✓")

        if ltp < recent_low * 1.03:
            score += 1
            conditions.append("Near 20d low — breakdown zone ✓")

        if bb_mid < ltp < bb_upper:
            score += 1
            conditions.append("BB: price in mid-upper zone (short entry) ✓")

        if st_dir == -1:
            score += 1
            conditions.append("Supertrend(7,3) BEARISH ✓")

        if wk_score < 0:
            score += abs(wk_score)
            conditions.append(f"{wk_label} +{abs(wk_score)}pts ✓")
        elif wk_score > 0:
            conditions.append(f"{wk_label} ⚠ (against short)")

        closes = close.values
        if len(closes) >= 20:
            ll = closes[-1] < closes[-10:].min() * 1.02
            if ll:
                score += 1
                conditions.append("Price near recent lows — downtrend structure ✓")

    _bb_range  = bb_upper - bb_lower
    bb_pct_val = round((ltp - bb_lower) / _bb_range, 3) if _bb_range > 0 else 0.5

    return {
        "score": score, "details": conditions, "ltp": ltp,
        "ema50": ema50, "ema200": ema200, "adx": adx_last,
        "rsi": rsi_val, "macd": macd_last, "signal": signal_last,
        "volume": vol_last, "avg_volume": vol_avg,
        "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "recent_high": recent_high, "recent_low": recent_low, "atr_val": atr_val,
        "bb_pct": bb_pct_val, "supertrend": st_dir,
        "weekly_label": wk_label, "weekly_score": wk_score,
    }


def ai_call(prompt, max_tokens=400):
    try:
        from ai_engine import _call_ai
        text, _ = _call_ai(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            system="You are a concise Indian equity swing analyst. Use only the exact numbers given. No speculation.",
        )
        return text or ""
    except Exception as e:
        logger.warning(f"ai_call swing: {e}")
        return ""


def _display_sym(sym):
    return sym.replace(".NS","")


def _trade_card(p, side):
    sym     = _display_sym(p["symbol"])
    ltp     = p["ltp"]
    score   = p["score"]
    atr_val = p.get("atr_val") or ltp * 0.02
    rsi_v   = round(p["rsi"], 1)
    adx_v   = round(p["adx"], 1)
    sector  = CANDIDATE_SECTORS.get(p["symbol"], "")
    wk_lbl  = p.get("weekly_label", "")
    wk_sc   = p.get("weekly_score", 0)

    # ── ATR-based levels (team spec) ──────────────────────────────────────────
    entry_lo = round(ltp * 0.995, 2)
    entry_hi = round(ltp * 1.005, 2)

    if side == "LONG":
        sl   = round(ltp - 1.2 * atr_val, 2)   # 1.2× ATR
        tgt1 = round(ltp + 2.0 * atr_val, 2)   # 2× ATR
        tgt2 = round(ltp + 3.5 * atr_val, 2)   # 3.5× ATR
        icon = "🟢"
    else:
        sl   = round(ltp + 1.2 * atr_val, 2)
        tgt1 = round(ltp - 2.0 * atr_val, 2)
        tgt2 = round(ltp - 3.5 * atr_val, 2)
        icon = "🔴"

    risk   = abs(ltp - sl)
    rw1    = abs(tgt1 - ltp)
    rw2    = abs(tgt2 - ltp)
    rr1    = round(rw1 / risk, 1) if risk > 0 else 0
    rr2    = round(rw2 / risk, 1) if risk > 0 else 0
    sl_pct = round(risk / ltp * 100, 1)
    t1_pct = round(rw1  / ltp * 100, 1)
    t2_pct = round(rw2  / ltp * 100, 1)

    rsi_lbl  = "🔴 OB" if rsi_v>70 else ("🟢 OS" if rsi_v<30 else ("🟡 Neutral" if rsi_v<50 else "🟠 Elevated"))
    conds    = "\n".join(f"   ✅ {c}" for c in p["details"])
    st_icon  = "🟢 Bull" if p.get("supertrend")==1 else ("🔴 Bear" if p.get("supertrend")==-1 else "⚪ Flat")
    wk_icon  = "🟢" if wk_sc>0 else ("🔴" if wk_sc<0 else "⚪")

    lines = [
        f"{icon} <b>{sym}</b>  [{side}]  Score: <b>{score}/13</b>",
    ]
    if sector:
        lines.append(f"   🏭 Sector   : {sector}")
    lines += [
        f"   💰 LTP      : ₹{ltp:,.2f}",
        f"   📊 RSI      : {rsi_v}  {rsi_lbl}",
        f"   📈 ADX      : {adx_v}",
        f"   🌊 Supertrend: {st_icon}",
        f"   📅 Weekly   : {wk_icon} {wk_lbl}",
        f"   ···",
        f"   📥 Entry    : ₹{entry_lo:,.2f} – ₹{entry_hi:,.2f}",
        f"   🎯 Target 1 : ₹{tgt1:,.2f}  (+{t1_pct}%)  R:R 1:{rr1}",
        f"   🎯 Target 2 : ₹{tgt2:,.2f}  (+{t2_pct}%)  R:R 1:{rr2}",
        f"   🛑 Stop Loss: ₹{sl:,.2f}  (-{sl_pct}%)  ATR×1.2",
        f"   ···",
        f"   <b>Signals ({len(p['details'])}):</b>",
        conds,
    ]
    return "\n".join(lines)


def get_swing_trades(mode="conservative"):
    """
    Scan all CANDIDATES, score each, sort by score desc — no round-robin.
    Min gate: LONG ≥7, SHORT ≥6 for conservative. Aggressive: LONG ≥5, SHORT ≥4.
    """
    threshold_long  = 7 if mode == "conservative" else 5
    threshold_short = 6 if mode == "conservative" else 4
    today     = date.today().strftime("%d-%b-%Y")
    all_picks = []

    for sym in CANDIDATES:
        try:
            df = safe_history(sym, period="1y", interval="1d")
            if df.empty or len(df) < 60:
                continue
            for side, thresh in [("LONG", threshold_long), ("SHORT", threshold_short)]:
                result = swing_score(df, side, sym=sym)
                if result["ltp"] and result["score"] >= thresh:
                    result["symbol"] = sym
                    result["side"]   = side
                    all_picks.append(result)
        except Exception as e:
            logger.warning(f"swing {sym}: {e}")

    # Sort by score descending — best picks first (team fix: no round-robin)
    all_picks.sort(key=lambda x: x["score"], reverse=True)
    long_picks  = [p for p in all_picks if p["side"] == "LONG"]
    short_picks = [p for p in all_picks if p["side"] == "SHORT"]

    label = f"Conservative (L≥{threshold_long}/S≥{threshold_short})" if mode=="conservative" \
            else f"Aggressive (L≥{threshold_long}/S≥{threshold_short})"
    lines = [
        f"📈 <b>Swing Trades — {label}</b>",
        f"📅 {today}  |  Universe: {len(CANDIDATES)} stocks  |  ATR-based SL/TP\n",
    ]

    if not long_picks and not short_picks:
        # Watchlist — best approaching stocks
        watch = []
        for sym in CANDIDATES:
            try:
                df = safe_history(sym, period="1y", interval="1d")
                if df.empty or len(df) < 60: continue
                for side in ["LONG","SHORT"]:
                    r = swing_score(df, side)
                    if r["ltp"]:
                        r["symbol"] = sym; r["side"] = side
                        watch.append(r)
            except Exception:
                continue
        watch.sort(key=lambda x: x["score"], reverse=True)
        lines.append(f"⚠️ No setups met threshold today.\n")
        lines.append("📊 <b>Watch List (approaching threshold):</b>")
        for p in watch[:6]:
            lines.append(
                f"  • <b>{_display_sym(p['symbol'])}</b> ({p['side']}) "
                f"Score:{p['score']}/13 | ₹{p['ltp']:.2f} | RSI:{round(p['rsi'],1)}"
            )
        lines.append("\n⚠️ Educational only. Not SEBI-registered advice.")
        return "\n".join(lines)

    if long_picks:
        lines.append("🟢 <b>LONG SETUPS</b>")
        for p in long_picks[:4]:
            lines.append(_trade_card(p, "LONG"))
            lines.append("")

    if short_picks:
        lines.append("🔴 <b>SHORT SETUPS</b>")
        for p in short_picks[:3]:
            lines.append(_trade_card(p, "SHORT"))
            lines.append("")

    lines.append("⚠️ Educational only. Not SEBI-registered advice.")
    return "\n".join(lines)
