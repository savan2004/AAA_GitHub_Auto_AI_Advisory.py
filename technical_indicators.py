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
from msk_indicator_math import ema as _ema_series, macd as _macd_series, atr_series, directional_series, bollinger
from config import RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD, ADX_PERIOD


# ── RSI (Wilder's smoothing — matches TradingView) ────────────────────────────
def calc_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float:
    """Shared Wilder RSI; missing history is NaN, not a fabricated neutral value."""
    values = rsi_series(close, period)
    return round(float(values.iloc[-1]), 1) if len(values) else float("nan")


def rsi_series(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    from msk_core_data_guard import wilder_rsi_series
    return wilder_rsi_series(close, period)


# ── EMA ───────────────────────────────────────────────────────────────────────
def calc_ema(close: pd.Series, span: int) -> float:
    """Exponential Moving Average — returns scalar (latest value)."""
    return round(float(ema_series(close, span).iloc[-1]), 2)


def ema_series(close: pd.Series, span: int) -> pd.Series:
    return _ema_series(close, span)


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
    macd_line, signal_line, histogram = _macd_series(close, fast, slow, signal)
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
    atr = atr_series(df["High"], df["Low"], df["Close"], period)
    return round(float(atr.iloc[-1]), 2)


# ── ADX + DI ──────────────────────────────────────────────────────────────────
def calc_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> tuple:
    """
    Average Directional Index (ADX), +DI, -DI.
    Returns (adx, plus_di, minus_di) — all scalars.
    ADX > 25 = trending market. +DI > -DI = bullish.
    """
    _, adx_s, plus_di, minus_di = directional_series(df["High"], df["Low"], df["Close"], period)
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
    mid, upper, lower = bollinger(close, window, num_sd)
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
    if not np.isfinite(rsi): return "UNAVAILABLE"
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
    ema20 = float(ema_series(close, 20).iloc[-1])
    ema50 = float(ema_series(close, 50).iloc[-1])
    if ltp > ema20 > ema50: return "BULLISH"
    if ltp < ema20 < ema50: return "BEARISH"
    return "NEUTRAL"


def swing_signal(rsi: float, trend: str, chg: float) -> str:
    """Unified screener signal — consistent across all views."""
    if not np.isfinite(rsi): return "⏳ WAIT — RSI unavailable"
    if rsi < 35:                                          return "⚡ OVERSOLD — bounce watch"
    if rsi > 72:                                          return "⚠️ OVERBOUGHT — pullback risk"
    if trend == "BULLISH" and rsi > 50 and chg > 0:      return "✅ UPTREND — strong momentum"
    if trend == "BEARISH" and rsi < 50:                   return "🔻 DOWNTREND — avoid"
    if trend in ("BULLISH","NEUTRAL") and 45 < rsi < 65 and chg > 0: return "✅ BUY ZONE"
    return "⏳ WAIT — no clear signal"
