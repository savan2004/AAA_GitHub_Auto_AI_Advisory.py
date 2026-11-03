"""Descending triangle: at least two flat support pivots, lower swing highs."""
import numpy as np
from .common import Context, breakout, fit, hit, pivots


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s = i-c.triangle_bars
    if s < 5 or ctx.atr(i) <= 0:
        return None
    hi, lo = ctx.array("high"), ctx.array("low")
    bottoms = pivots(lo,s,i,c.swing_order,"low")
    tops = pivots(hi,s,i,c.swing_order,"high")
    if min(len(tops),len(bottoms)) < c.min_pivot_touches:
        return None
    support = float(np.median(lo[bottoms]))
    near = [p for p in bottoms if abs(lo[p]-support) <= c.triangle_flat_atr*ctx.atr(i)]
    if len(near) < c.min_pivot_touches or near[-1]-near[0] < c.double_min_separation:
        return None
    vals = hi[tops]
    slope, _, r2 = fit(vals)
    if slope >= 0 or vals[0] <= vals[-1] + ctx.atr(i)*.3 or r2 < c.slope_fit_min_r2:
        return None
    if not breakout(ctx, i, support, "SELL"):
        return None
    return hit("Descending Triangle", s, ctx, i,"SELL",r2,
               1.-min(1.,float(np.std(lo[near]))/max(ctx.atr(i),1e-8)))
