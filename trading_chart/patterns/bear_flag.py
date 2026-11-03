"""Bear flag: impulsive selloff, shallow rising consolidation, breakdown."""
import numpy as np
from .common import Context, breakout, fit, hit


def detect(ctx: Context, i: int):
    c = ctx.cfg
    start = i - c.flag_bars
    pole = start - c.pole_bars
    if pole < 1 or ctx.atr(i) <= 0:
        return None
    df = ctx.df
    close, high, low = (df[k].to_numpy() for k in ("close", "high", "low"))
    drop = (close[pole] - close[start-1]) / close[pole]
    if drop < c.flag_pole_min_move:
        return None
    ph, fl = high[start:i], low[start:i]
    high_slope, _, rh = fit(ph)
    low_slope, _, rl = fit(fl)
    if high_slope <= 0 or low_slope <= 0 or min(rh, rl) < c.slope_fit_min_r2:
        return None
    retracement = (max(ph) - close[start-1]) / max(close[pole]-close[start-1], 1e-8)
    if not 0 <= retracement <= c.flag_max_retracement:
        return None
    if not breakout(ctx, i, float(np.min(fl)), "SELL"):
        return None
    return hit("Bear Flag", start, ctx, i, "SELL", (rh+rl)/2,
               1 - retracement/c.flag_max_retracement)
