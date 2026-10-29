"""Risk-aware technical scoring for every available NSE index constituent.

A WATCHLIST is not a buy recommendation. All price levels are conditional,
derived from completed OHLCV candles, not live quotes or proven forecasts.
"""
import math
from datetime import datetime

import pandas as pd
from investment_screener import IST, _clean

_PROFILE_MAX_LOSS = {"conservative": 8., "moderate": 11., "aggressive": 15.}


def technical_candidate(sym, raw, profile, now=None):
    """Score *all* valid histories, including sideways companies and WAIT setups."""
    now = now or datetime.now(IST)
    bars = _clean(raw, now, 80)
    if bars is None or len(bars) < 80 or profile not in _PROFILE_MAX_LOSS:
        return None
    try:
        close, high, low, vol = (bars[x].astype(float) for x in ("Close", "High", "Low", "Volume"))
        last = float(close.iloc[-1])
        ma20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ma50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        prev = close.shift(1)
        true_range = pd.concat((high-low, (high-prev).abs(), (low-prev).abs()),axis=1).max(axis=1)
        atr = float(true_range.ewm(alpha=1/14, adjust=False, min_periods=14).mean().iloc[-1])
        move = close.diff()
        up = move.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
        down = (-move.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
        rsi = 100 if down == 0 and up > 0 else (50 if down == 0 else 100 - 100/(1+up/down))
        upmove, downmove = high.diff(), -low.diff()
        posdm = upmove.where((upmove>downmove)&(upmove>0),0)
        negdm = downmove.where((downmove>upmove)&(downmove>0),0)
        smooth = lambda s: s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        smtr = smooth(true_range).replace(0, float("nan"))
        dip = 100*smooth(posdm)/smtr
        dim = 100*smooth(negdm)/smtr
        dx = 100*(dip-dim).abs()/(dip+dim).replace(0,float("nan"))
        adx = float(smooth(dx).iloc[-1])
        pct20 = 100*(last/float(close.iloc[-21])-1)
        volx = float(vol.iloc[-1]/vol.iloc[-21:-1].mean())
        turnover = float((vol*close).tail(20).median())
        atr_pct = 100*atr/last
        swing_high = float(high.iloc[-21:-1].max())
        support = float(low.tail(20).min())
        breakout = last > swing_high and volx >= 1
        uptrend = last > ma20 > ma50
        if not all(math.isfinite(v) for v in (last,ma20,ma50,atr,rsi,adx,pct20,volx,turnover,atr_pct,support)) or last <= 0:
            return None
        if turnover < 2e7 or atr <= 0 or atr_pct <= 0 or last < 10:
            return None
        score = max(0, min(100,round(
            38 + (17 if last > ma20 else -10) + (14 if ma20 > ma50 else -12)
            + max(-10,min(13,pct20 * 1.2))
            + max(-8,min(10,(rsi-50)*.45))
            + (6 if adx >= 18 else 0)
            + (7 if breakout else 0)
            - max(0,atr_pct-4)*1.5
        )))
        trigger = max(last, swing_high*1.002)
        risk = max(trigger - support + .1*atr, 1.7*atr)
        stop = trigger - risk
        if stop <= 0 or trigger <= stop or trigger <= 0:
            return None
        loss_pct = risk/trigger*100
        maxloss = _PROFILE_MAX_LOSS[profile]
        status = "CONDITIONAL BUY" if (uptrend and breakout and rsi >= 54 and adx >= 18 and loss_pct <= maxloss) else "WAIT"
        risk_label = ("HIGH" if loss_pct > maxloss or atr_pct > maxloss*.7
                      else "MEDIUM" if loss_pct > maxloss*.55 or atr_pct > maxloss*.45
                      else "LOWER")
        return {
            "sym":str(sym), "score":score, "tech_score":score,
            "ltp":round(last,2), "chg":round((last/float(close.iloc[-2])-1)*100,2),
            "rsi":round(float(rsi),1), "adx":round(adx,1), "volx":round(volx,2),
            "atr_pct":round(atr_pct,2), "risk_pct":round(loss_pct,2),
            "entry":round(trigger,2), "sl":round(stop,2),
            "t1":round(trigger + 2*risk,2), "t2":round(trigger + 3*risk,2),
            "rr1":2., "rr2":3., "signal":status, "risk_label":risk_label,
            "asof":bars.index[-1].date().isoformat(),
        }
    except (KeyError,IndexError,ValueError,TypeError,ZeroDivisionError,OverflowError):
        return None
