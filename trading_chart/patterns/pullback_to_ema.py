"""EMA20/50 support or resistance test in an established trend, then reversal."""
import numpy as np
from .common import Context, breakout, hit


def detect(ctx: Context, i: int):
    c=ctx.cfg
    if i < c.ema_slow+c.pullback_lookback or ctx.atr(i) <= 0:
        return None
    close,high,low=(ctx.array(k) for k in ("close","high","low"))
    e20,e50=(ctx.array(k) for k in ("ema20","ema50"))
    s=i-c.pullback_lookback
    if not np.all(np.isfinite(e20[s:i+1])) or not np.all(np.isfinite(e50[s:i+1])):
        return None
    for direction in ("BUY","SELL"):
        positive=direction=="BUY"
        if positive and not np.all(e20[s:i]>e50[s:i]):
            continue
        if not positive and not np.all(e20[s:i]<e50[s:i]):
            continue
        touched=[]
        for j in range(s,i):
            if any(abs((low[j] if positive else high[j])-ref[j]) < .25*ctx.atr(j)
                   for ref in (e20,e50)):
                touched.append(j)
        if not touched:
            continue
        border=float(np.max(high[s:i])) if positive else float(np.min(low[s:i]))
        if breakout(ctx,i,border,direction):
            fit=min(1.,abs(e20[i]-e50[i])/(2*ctx.atr(i)))
            return hit("Pullback to EMA",touched[-1],ctx,i,direction,fit,1.,base=61.)
    return None
