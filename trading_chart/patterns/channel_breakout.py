"""Channel breakout: two parallel regression bands with adequate edge touches."""
import numpy as np
from .common import Context, breakout, fit, hit


def detect(ctx: Context, i: int):
    c = ctx.cfg
    s = i - c.channel_pattern_bars
    if s < 5 or ctx.atr(i) <= 0:
        return None
    highs = ctx.array("high")[s:i]
    lows = ctx.array("low")[s:i]
    hs, upper, hr = fit(highs)
    ls, lower, lr = fit(lows)
    band = float(np.median(upper-lower))
    if band <= ctx.atr(i) or max(hr, lr) < c.slope_fit_min_r2:
        return None
    if abs(hs-ls)*len(highs) > c.channel_parallel_tolerance * band:
        return None
    width = max(ctx.atr(i)*c.channel_touch_atr, .1*band)
    touch_high = sum(abs(highs-upper) <= width)
    touch_low = sum(abs(lows-lower) <= width)
    if min(touch_high,touch_low) < c.min_pivot_touches:
        return None
    top = float(upper[-1]+hs)
    bottom = float(lower[-1]+ls)
    for direction, boundary in (("BUY", top), ("SELL", bottom)):
        if breakout(ctx, i, boundary, direction, volume=c.channel_breakout_volume):
            return hit("Channel Breakout", s, ctx, i, direction, (hr+lr)/2,
                       min(1.,min(touch_high,touch_low)/4))
    return None
