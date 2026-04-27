"""
technical_indicators.py — Centralised Technical Indicator Library v1.0

Copilot Fix #7: RSI, EMA, MACD, ATR, ADX, ASI, Bollinger Bands were
duplicated across main.py, swing_trades.py, data_engine.py.
All indicators now live here — single source of truth.

All functions:
  - Accept pd.Series or pd.DataFrame
  - Return float (scalar) or pd.Series
  - Use Wilder's smoothing for RSI/ATR/ADX (matches TradingView)
  - Have docstrings with formula references
"""

import pandas as pd
import numpy as np
from config import RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD, ADX_PERIOD


# ── RSI (Wilder's smoothing — matches TradingView) ────────────────────────────
def calc_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float:
    """
    Relative Strength Index using Wilder's EMA (com = period-1).
    Requires 2×period bars for stable output.
    Returns 50.0 if insufficient data.
    """
    if len(close) < period * 2:
        return 50.0
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta.clip(upper=0))
    avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = avg_loss.replace(0, 1e-10)
    rsi_s    = 100 - (100 / (1 + avg_gain / avg_loss))
    return round(float(rsi_s.iloc[-1]), 1)


def rsi_series(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Full RSI series (for charts/signals over time)."""
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta.clip(upper=0))
    avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + avg_gain / avg_loss))


# ── EMA ───────────────────────────────────────────────────────────────────────
def calc_ema(close: pd.Series, span: int) -> float:
    """Exponential Moving Average — returns scalar (latest value)."""
    return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 2)


def ema_series(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


# ── SMA ───────────────────────────────────────────────────────────────────────
def calc_sma(close: pd.Series, window: int) -> float:
    return round(float(close.rolling(window).mean().iloc[-1]), 2)


# ── MACD ──────────────────────────────────────────────────────────────────────
def calc_macd(close: pd.Series,
              fast: int = MACD_FAST,
              slow: int = MACD_SLOW,
              signal: int = MACD_SIGNAL) -> tuple:
    """
    Returns (macd_line, signal_line, histogram) — all scalars.
    """
    exp_fast   = close.ewm(span=fast,   adjust=False).mean()
    exp_slow   = close.ewm(span=slow,   adjust=False).mean()
    macd_line  = exp_fast - exp_slow
    signal_line= macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return (
        round(float(macd_line.iloc[-1]),   2),
        round(float(signal_line.iloc[-1]), 2),
        round(float(histogram.iloc[-1]),   2),
    )


# ── ATR (Wilder's smoothing) ──────────────────────────────────────────────────
def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """
    Average True Range using Wilder's EMA.
    df must have High, Low, Close columns.
    """
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        (h - l),
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    return round(float(atr.iloc[-1]), 2)


# ── ADX + DI ──────────────────────────────────────────────────────────────────
def calc_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> tuple:
    """
    Average Directional Index (ADX), +DI, -DI.
    Returns (adx, plus_di, minus_di) — all scalars.
    ADX > 25 = trending market. +DI > -DI = bullish.
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    plus_dm  = high.diff()
    minus_dm = low.diff().abs()
    plus_dm  = plus_dm.where((plus_dm > minus_dm)  & (plus_dm > 0),  0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low  - close.shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_s    = tr.ewm(com=period - 1,   min_periods=period, adjust=False).mean()
    plus_di  = 100 * (plus_dm.ewm(com=period - 1,  min_periods=period, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(com=period - 1, min_periods=period, adjust=False).mean() / atr_s)
    dx       = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    adx_s    = dx.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    return (
        round(float(adx_s.iloc[-1]),    1),
        round(float(plus_di.iloc[-1]),  1),
        round(float(minus_di.iloc[-1]), 1),
    )


# ── Bollinger Bands ───────────────────────────────────────────────────────────
def calc_bollinger(close: pd.Series, window: int = 20, num_sd: float = 2.0) -> tuple:
    """
    Returns (mid, upper, lower) — all scalars (latest values).
    """
    mid   = close.rolling(window).mean()
    std   = close.rolling(window).std()
    upper = mid + num_sd * std
    lower = mid - num_sd * std
    return (
        round(float(mid.iloc[-1]),   2),
        round(float(upper.iloc[-1]), 2),
        round(float(lower.iloc[-1]), 2),
    )


# ── ASI (Accumulation Swing Index) ───────────────────────────────────────────
def calc_asi(df: pd.DataFrame) -> float:
    """
    Wilder's Accumulation Swing Index.
    Positive = bullish momentum, negative = bearish.
    """
    if len(df) < 2:
        return 0.0
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    Cp, Op     = C.shift(1), O.shift(1)
    A   = (H - Cp).abs()
    B   = (L - Cp).abs()
    CD  = (H - L).abs()
    D   = (Cp - Op).abs()
    R   = pd.Series(0.0, index=df.index)
    cA  = (A >= B) & (A >= CD)
    cB  = (B >= A) & (B >= CD) & ~cA
    R[cA]       = A[cA]  + 0.5 * B[cA]  + 0.25 * D[cA]
    R[cB]       = B[cB]  + 0.5 * A[cB]  + 0.25 * D[cB]
    R[~(cA|cB)] = CD[~(cA|cB)] + 0.25 * D[~(cA|cB)]
    R    = R.replace(0, 1e-10)
    K    = pd.concat([A, B], axis=1).max(axis=1)
    lm   = (Cp * 0.20).replace(0, 1e-10)
    SI   = 50 * ((C - Cp) + 0.5*(Cp - O) + 0.25*(Cp - Op)) / R * (K / lm)
    return round(float(SI.cumsum().iloc[-1]), 2)


# ── Signal Labels ─────────────────────────────────────────────────────────────
def rsi_label(rsi: float) -> str:
    if rsi > 70: return "OVERBOUGHT"
    if rsi < 30: return "OVERSOLD"
    if rsi > 60: return "STRONG"
    if rsi < 40: return "WEAK"
    return "NEUTRAL"


def trend_label(close: pd.Series) -> str:
    """Bull/Bear/Neutral based on EMA20 vs EMA50 vs price."""
    if len(close) < 50:
        return "NEUTRAL"
    ltp   = float(close.iloc[-1])
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    if ltp > ema20 > ema50: return "BULLISH"
    if ltp < ema20 < ema50: return "BEARISH"
    return "NEUTRAL"


def swing_signal(rsi: float, trend: str, chg: float) -> str:
    """Unified screener signal — consistent across all views."""
    if rsi < 35:                                          return "⚡ OVERSOLD — bounce watch"
    if rsi > 72:                                          return "⚠️ OVERBOUGHT — pullback risk"
    if trend == "BULLISH" and rsi > 50 and chg > 0:      return "✅ UPTREND — strong momentum"
    if trend == "BEARISH" and rsi < 50:                   return "🔻 DOWNTREND — avoid"
    if trend in ("BULLISH","NEUTRAL") and 45 < rsi < 65 and chg > 0: return "✅ BUY ZONE"
    return "⏳ WAIT — no clear signal"
