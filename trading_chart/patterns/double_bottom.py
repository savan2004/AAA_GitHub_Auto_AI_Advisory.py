"""Double bottom: matching support lows, 5+ bars apart, rising low volume."""
import numpy as np
from .common import Context, breakout, hit, pivots


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s = i-c.double_pattern_bars
    if s < 5 or ctx.atr(i) <= 0:
        return None
    lows, highs, volumes = (ctx.array(k) for k in ("low", "high", "volume"))
    ps = pivots(lows,s,i,c.swing_order,"low")
    for second in reversed(ps):
        for first in reversed([x for x in ps if second-x>=c.double_min_separation]):
            ref = (lows[first]+lows[second])/2
            delta=abs(lows[first]-lows[second])/max(ref,1e-8)
            if delta > c.double_tolerance_fraction or volumes[second] <= volumes[first]:
                continue
            neckline=float(np.max(highs[first:second+1]))
            if neckline-ref < ctx.atr(i)*1.1:
                continue
            if breakout(ctx,i,neckline,"BUY"):
                return hit("Double Bottom",first,ctx,i,"BUY",1.-delta/c.double_tolerance_fraction,
                           min(1.,(neckline-ref)/(4*ctx.atr(i))))
    return None
