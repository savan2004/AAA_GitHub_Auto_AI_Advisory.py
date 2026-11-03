"""Bull flag: impulsive pole, shallow orderly downward channel, high-volume exit."""
import numpy as np
from .common import Context, breakout, fit, hit


def detect(ctx: Context, i: int):
    c = ctx.cfg
    start = i - c.flag_bars
    pole = start - c.pole_bars
    if pole < 1 or ctx.atr(i) <= 0:
        return None
    df = ctx.df
    close = df.close.to_numpy()
    high = df.high.to_numpy()
    low = df.low.to_numpy()
    move = (close[start-1] - close[pole]) / close[pole]
    if move < c.flag_pole_min_move:
        return None
    ph, fl = high[start:i], low[start:i]
    high_slope, _, rh = fit(ph)
    low_slope, _, rl = fit(fl)
    if high_slope >= 0 or low_slope >= 0 or min(rh, rl) < c.slope_fit_min_r2:
        return None
    # Channel contraction must not retrace >55% of preceding impulse.
    retracement = (close[start-1] - min(fl)) / max(close[start-1] - close[pole], 1e-8)
    if not 0 <= retracement <= c.flag_max_retracement:
        return None
    if not breakout(ctx, i, float(np.max(ph)), "BUY"):
        return None
    return hit("Bull Flag", start, ctx, i, "BUY", (rh+rl)/2,
               1 - retracement/c.flag_max_retracement)
