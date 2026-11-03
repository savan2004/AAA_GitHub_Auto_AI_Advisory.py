"""Causal quantitative chart analysis with explicit signal gating."""
import math
from typing import Any

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, ChartConfig
from .indicators import calculate, adx_label, volume_label
from .patterns import Context, select_pattern
from .patterns.common import pivots


REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate OHLCV, timestamp chronology and complete finite rows; never impute."""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Data must be a pandas DataFrame")
    missing = set(REQUIRED)-set(data.columns)
    if missing:
        raise ValueError(f"OHLCV missing fields: {sorted(missing)}")
    d = data.loc[:, list(REQUIRED)].copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="coerce")
    if d.timestamp.isna().any() or d.timestamp.duplicated().any():
        raise ValueError("Invalid or duplicate timestamps")
    for name in REQUIRED[1:]:
        d[name] = pd.to_numeric(d[name], errors="coerce")
    if not np.isfinite(d[list(REQUIRED[1:])].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite OHLCV field")
    if not d.timestamp.is_monotonic_increasing:
        raise ValueError("Timestamps must increase without lookahead")
    if ((d[list(REQUIRED[1:])] <= 0).any().loc[["open", "high", "low", "close"]]).any():
        raise ValueError("Prices must be strictly positive")
    if (d.volume < 0).any():
        raise ValueError("Volume must not be negative")
    if ((d.high < d[["open", "close"]].max(axis=1)) |
        (d.low > d[["open", "close"]].min(axis=1)) |
        (d.high < d.low)).any():
        raise ValueError("Invalid candle high/low geometry")
    return d.reset_index(drop=True)


def swing_state(df: pd.DataFrame, i: int, cfg: ChartConfig) -> tuple[bool, bool]:
    """Rising confirmed swing highs / falling confirmed swing lows."""
    start = max(0, i-75)
    hi = pivots(df.high.to_numpy(),start,i+1,cfg.swing_order,"high")
    lo = pivots(df.low.to_numpy(),start,i+1,cfg.swing_order,"low")
    higher = len(hi) >= 2 and df.high.iloc[hi[-1]] > df.high.iloc[hi[-2]]
    lower = len(lo) >= 2 and df.low.iloc[lo[-1]] < df.low.iloc[lo[-2]]
    return bool(higher), bool(lower)


def classify_trend(df: pd.DataFrame, i: int, cfg: ChartConfig) -> str:
    """EMA relationship + confirmed swings; flat low-ADX defaults to Sideways."""
    a,b = float(df.ema20.iloc[i]), float(df.ema50.iloc[i])
    if not np.isfinite(a) or not np.isfinite(b):
        return "Sideways"
    higher, lower = swing_state(df,i,cfg)
    past = float(df.ema50.iloc[max(0,i-cfg.sideways_ema_slope_bars)])
    slope = (b/past - 1) if np.isfinite(past) and past > 0 else 0.
    adx = float(df.adx.iloc[i])
    if abs(slope) < cfg.sideways_ema_slope_fraction and np.isfinite(adx) and adx < cfg.adx_weak_below:
        return "Sideways"
    if a > b and higher:
        return "Uptrend"
    if a < b and lower:
        return "Downtrend"
    return "Sideways"


def _cross_recent(df: pd.DataFrame, end: int, direction: str, cfg: ChartConfig) -> bool:
    """Cross within configured 10 closed bars; no future data."""
    start = max(cfg.ema_slow, end-cfg.recent_ema_cross_bars+1)
    fast,slow = df.ema20,df.ema50
    for j in range(start,end+1):
        x,y = fast.iloc[j],slow.iloc[j]
        u,v = fast.iloc[j-1],slow.iloc[j-1]
        if not all(np.isfinite(z) for z in (x,y,u,v)):
            continue
        if direction=="BUY" and u <= v and x > y:
            return True
        if direction=="SELL" and u >= v and x < y:
            return True
    return False


def _turning(df: pd.DataFrame, index: int, direction: str) -> bool:
    f,s = float(df.ema20.iloc[index]),float(df.ema50.iloc[index])
    past=float(df.ema20.iloc[index-3]) if index>=3 else np.nan
    if not np.isfinite(past) or not np.isfinite(f) or not np.isfinite(s):
        return False
    price=float(df.close.iloc[index])
    return (f > past and price > f and f >= s*.997) if direction=="BUY" else (f < past and price < f and f <= s*1.003)


def signal_at(df: pd.DataFrame, i: int, ctx: Context, trend: str, pattern, cfg: ChartConfig):
    """High requires confirmed EMA-cross + breakout + ADX>25 + volume>=2x.

    Medium requires directional trend/turning, ADX in [20,25], volume [1.5,2)
    and either a pattern with cleanliness [60,75) or EMA cross without a pattern.
    Everything else is WAIT/Weak. SELL mirrors BUY.
    """
    adx=float(df.adx.iloc[i])
    vol=float(df.vol_multiple.iloc[i])
    if not np.isfinite(adx) or not np.isfinite(vol):
        return "WAIT", "Weak"
    for action, expected in (("BUY","Uptrend"),("SELL","Downtrend")):
        crosses=_cross_recent(df,i,action,cfg)
        breakout_now=bool(pattern and pattern.direction==action and pattern.breakout and pattern.trigger_index==i)
        if trend==expected and pattern is not None and pattern.direction==action and pattern.confidence>=cfg.high_cleanliness and adx>cfg.adx_building_to and vol>=cfg.volume_high and crosses and breakout_now:
            return action,"High"
        medium_geometry = (pattern is not None and pattern.direction==action and
                           cfg.min_cleanliness<=pattern.confidence<cfg.high_cleanliness)
        no_pattern_cross = (pattern is None and crosses)
        if ((trend==expected or (trend=="Sideways" and _turning(df,i,action))) and
            (medium_geometry or no_pattern_cross) and
            cfg.adx_weak_below<=adx<=cfg.adx_building_to and
            cfg.volume_above_normal<=vol<cfg.volume_high):
            return action,"Medium"
    return "WAIT", "Weak"


def analyze(df: pd.DataFrame, cfg: ChartConfig=DEFAULT_CONFIG):
    """Return validated indicator dataframe and JSON-serializable latest signal."""
    d=validate_data(df)
    n=len(d)
    if n < cfg.min_bars:
        return d, dict(trend="Insufficient data",pattern="No clean pattern",
                       adx=None,adx_label="Insufficient data",vol_multiple=None,
                       vol_label="Insufficient data",signal="WAIT",quality="Weak",
                       markers=[],pattern_anchors=[],status="Insufficient data",
                       bars=n,required_bars=cfg.min_bars)
    d=calculate(d,cfg)
    i=n-1
    adx=float(d.adx.iloc[i]); vol=float(d.vol_multiple.iloc[i])
    trend=classify_trend(d,i,cfg)
    ctx=Context(d,cfg)
    pattern=select_pattern(ctx,i)
    # Label can show the most recent confirmed pattern without treating it as
    # a trigger today; the current signal still requires an active breakout.
    if pattern is None:
        for previous in range(i-1,max(cfg.min_bars-1,i-cfg.max_pattern_age_bars)-1,-1):
            old=select_pattern(ctx,previous)
            if old:
                pattern=old
                break
    signal,quality=signal_at(d,i,ctx,trend,pattern,cfg)
    markers=[]
    if signal in ("BUY","SELL"):
        markers=[dict(index=i,time=d.timestamp.iloc[i].isoformat(),type=signal,
                      price=round(float(d.low.iloc[i] if signal=="BUY" else d.high.iloc[i]),4))]
    anchors=[]
    if pattern:
        anchors=[dict(name=pattern.name,index=pattern.anchor_index,
                      time=d.timestamp.iloc[pattern.anchor_index].isoformat(),
                      trigger_index=pattern.trigger_index,
                      confidence=pattern.confidence,direction=pattern.direction)]
    return d, dict(trend=trend,pattern=pattern.name if pattern else "No clean pattern",
                   cleanliness=pattern.confidence if pattern else None,
                   adx=round(adx,2) if np.isfinite(adx) else None,
                   adx_label=adx_label(adx,cfg),
                   vol_multiple=round(vol,2) if np.isfinite(vol) else None,
                   vol_label=volume_label(vol,cfg),
                   signal=signal,quality=quality,markers=markers,
                   pattern_anchors=anchors,status="OK",bars=n,
                   latest_timestamp=d.timestamp.iloc[i].isoformat(),
                   latest_close=round(float(d.close.iloc[i]),4))
