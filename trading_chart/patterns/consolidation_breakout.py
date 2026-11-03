"""Narrow 12-bar range with ATR compression to <0.5 historic ATR, then break."""
import numpy as np
from .common import Context, breakout, hit


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s=i-c.consolidation_bars
    prev=s-30
    if prev < 20 or ctx.atr(i) <= 0:
        return None
    atr=ctx.array("tr")
    past=np.nanmean(atr[prev:s])
    recent=np.nanmean(atr[s:i])
    if not np.isfinite(past) or not np.isfinite(recent) or past <= 0 or recent >= c.consolidation_atr_fraction*past:
        return None
    hi,lo=ctx.array("high"),ctx.array("low")
    high=float(np.max(hi[s:i]))
    low=float(np.min(lo[s:i]))
    if high-low > 2.5*past:
        return None
    for direction,boundary in (("BUY",high),("SELL",low)):
        if breakout(ctx,i,boundary,direction):
            return hit("Consolidation Breakout",s,ctx,i,direction,
                       1.-recent/max(past,1e-8),1.-(high-low)/(2.5*past),base=63.)
    return None
