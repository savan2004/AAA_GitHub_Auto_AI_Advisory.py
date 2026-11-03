"""Deterministic EMA, Wilder ATR/ADX and prior-20-bar relative volume."""
import numpy as np
import pandas as pd

from .config import ChartConfig, DEFAULT_CONFIG


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with span and min_periods warm-up."""
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder moving average, seeded by the arithmetic mean of first n values."""
    arr = series.to_numpy(dtype=float)
    out = np.full(arr.shape, np.nan, dtype=float)
    valid = np.flatnonzero(np.isfinite(arr))
    if len(valid) < n:
        return pd.Series(out, index=series.index)
    # Indicators begin at initial valid point. Any gap is left uncomputed rather
    # than manufacturing an observed period.
    if not np.array_equal(valid[:n], np.arange(valid[0], valid[0] + n)):
        return pd.Series(out, index=series.index)
    start = int(valid[n - 1])
    out[start] = float(np.mean(arr[valid[:n]]))
    for i in range(start + 1, len(arr)):
        if np.isfinite(arr[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (n - 1) + arr[i]) / n
    return pd.Series(out, index=series.index)


def calculate(df: pd.DataFrame, cfg: ChartConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Add EMA, ATR, +DI, -DI, ADX and prior-volume relative-volume metrics."""
    d = df.copy()
    c, h, l, v = (d[k].astype(float) for k in ("close", "high", "low", "volume"))
    d["ema20"] = ema(c, cfg.ema_fast)
    d["ema50"] = ema(c, cfg.ema_slow)
    previous = c.shift(1)
    tr = pd.concat([h-l, (h-previous).abs(), (l-previous).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    d["tr"] = tr
    d["atr"] = wilder(tr, cfg.atr_period)
    up = h.diff()
    down = -l.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.), index=d.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.), index=d.index)
    plus.iloc[0] = np.nan
    minus.iloc[0] = np.nan
    smoothed_tr = wilder(tr, cfg.adx_period)
    d["plus_di"] = 100 * wilder(plus, cfg.adx_period) / smoothed_tr.replace(0, np.nan)
    d["minus_di"] = 100 * wilder(minus, cfg.adx_period) / smoothed_tr.replace(0, np.nan)
    denom = (d.plus_di + d.minus_di).replace(0, np.nan)
    dx = (100 * (d.plus_di - d.minus_di).abs() / denom).fillna(0.)
    # ADX requires a second full Wilder lookback after directional indicators.
    dx.iloc[:cfg.adx_period] = np.nan
    d["adx"] = wilder(dx, cfg.adx_period)
    d["avg_volume_prior20"] = v.shift(1).rolling(cfg.volume_period, min_periods=cfg.volume_period).mean()
    d["vol_multiple"] = v / d.avg_volume_prior20.replace(0, np.nan)
    return d


def adx_label(value: float, cfg: ChartConfig = DEFAULT_CONFIG) -> str:
    """20 is Trend building, 25 Strong, 35 Strong; >35 Very strong."""
    if not np.isfinite(value):
        return "Unavailable"
    if value < cfg.adx_weak_below:
        return "Weak"
    if value < cfg.adx_building_to:
        return "Trend building"
    if value <= cfg.adx_strong_to:
        return "Strong"
    return "Very strong"


def volume_label(value: float, cfg: ChartConfig = DEFAULT_CONFIG) -> str:
    """For <1.5x, show numeric multiple without Normal annotation."""
    if not np.isfinite(value):
        return "Unavailable"
    if value < cfg.volume_above_normal:
        return ""
    if value < cfg.volume_high:
        return "Above normal"
    if value < cfg.volume_explosive:
        return "High"
    return "Explosive"
