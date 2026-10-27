"""Ascending triangle: at least two resistance pivots, rising swing lows, break."""
import numpy as np
from .common import Context, breakout, fit, hit, pivots


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s = i-c.triangle_bars
    if s < 5 or ctx.atr(i) <= 0:
        return None
    hi, lo = ctx.array("high"), ctx.array("low")
    top = pivots(hi,s,i,c.swing_order,"high")
    bottoms = pivots(lo,s,i,c.swing_order,"low")
    if min(len(top),len(bottoms)) < c.min_pivot_touches:
        return None
    resist = float(np.median(hi[top]))
    near = [p for p in top if abs(hi[p]-resist) <= c.triangle_flat_atr*ctx.atr(i)]
    if len(near) < c.min_pivot_touches or near[-1]-near[0] < c.double_min_separation:
        return None
    bot_vals = lo[bottoms]
    slope, _, r2 = fit(bot_vals)
    if slope <= 0 or bot_vals[-1] <= bot_vals[0] + ctx.atr(i)*.3 or r2 < c.slope_fit_min_r2:
        return None
    if not breakout(ctx, i, resist, "BUY"):
        return None
    return hit("Ascending Triangle", s, ctx, i,"BUY",r2,
               1.-min(1.,float(np.std(hi[near]))/max(ctx.atr(i),1e-8)))
