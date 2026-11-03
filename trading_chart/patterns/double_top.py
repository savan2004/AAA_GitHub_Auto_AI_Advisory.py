"""Double top: matching resistance highs, 5+ bars apart, declining 2nd volume."""
import numpy as np
from .common import Context, breakout, hit, pivots


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s = i-c.double_pattern_bars
    if s < 5 or ctx.atr(i) <= 0:
        return None
    lows, highs, volumes = (ctx.array(k) for k in ("low", "high", "volume"))
    ps = pivots(highs,s,i,c.swing_order,"high")
    for second in reversed(ps):
        for first in reversed([x for x in ps if second-x>=c.double_min_separation]):
            ref=(highs[first]+highs[second])/2
            delta=abs(highs[first]-highs[second])/max(ref,1e-8)
            if delta>c.double_tolerance_fraction or volumes[second]>=volumes[first]:
                continue
            neckline=float(np.min(lows[first:second+1]))
            if ref-neckline < ctx.atr(i)*1.1:
                continue
            if breakout(ctx,i,neckline,"SELL"):
                return hit("Double Top",first,ctx,i,"SELL",1.-delta/c.double_tolerance_fraction,
                           min(1.,(ref-neckline)/(4*ctx.atr(i))))
    return None
