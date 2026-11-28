"""Independent scalar indicator oracle, CI only, no TA-Lib.

Uses Python arithmetic/statistics, not pandas EWM, MSK math helpers or
production calculation functions, to construct reference values. Production
is imported only by the comparison harness. Mature tails are compared to
canonical SMA-seeded EMA/Wilder conventions with explicit rounding bounds.
"""
import json
import math
from statistics import fmean, pstdev


def smooth(values, period, alpha):
    if len(values) < period or period < 1:
        raise ValueError("insufficient observations")
    value = fmean(values[:period])
    result = [value]
    for x in values[period:]:
        value += alpha * (x - value)
        result.append(value)
    return result


def reference_values(high, low, close):
    h,l,c = [list(map(float,x)) for x in (high,low,close)]
    if not len(h)==len(l)==len(c) or len(c)<250:
        raise ValueError("invalid history length")
    if not all(math.isfinite(x) and x>0 for x in h+l+c):
        raise ValueError("invalid price")
    if any(not a<=b<=d for a,b,d in zip(l,c,h)):
        raise ValueError("invalid price geometry")
    p=14
    delta=[b-a for a,b in zip(c,c[1:])]
    gain=smooth([max(x,0) for x in delta],p,1/p)[-1]
    loss=smooth([max(-x,0) for x in delta],p,1/p)[-1]
    rsi=50 if gain+loss==0 else 100*gain/(gain+loss)
    e12=smooth(c,12,2/13)
    e26=smooth(c,26,2/27)
    m=[a-b for a,b in zip(e12[14:],e26)]
    signal=smooth(m,9,2/10)[-1]
    tr=[]; plus=[]; minus=[]
    for i in range(1,len(c)):
        tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        up=h[i]-h[i-1]; down=l[i-1]-l[i]
        plus.append(up if up>down and up>0 else 0.0)
        minus.append(down if down>up and down>0 else 0.0)
    atr=smooth(tr,p,1/p)
    dp=smooth(plus,p,1/p); dm=smooth(minus,p,1/p)
    if any(x<=0 for x in atr):
        raise ValueError("zero true range: directional indicators unavailable")
    pos=[100*a/b for a,b in zip(dp,atr)]
    neg=[100*a/b for a,b in zip(dm,atr)]
    dx=[0.0 if a+b==0 else 100*abs(a-b)/(a+b) for a,b in zip(pos,neg)]
    adx=smooth(dx,p,1/p)[-1]
    mid=fmean(c[-20:]); sd=pstdev(c[-20:])
    return {"RSI14":rsi,"EMA20":smooth(c,20,2/21)[-1],"SMA50":fmean(c[-50:]),
            "MACD":m[-1],"MACD_signal":signal,"MACD_hist":m[-1]-signal,
            "ATR14":atr[-1],"ADX14":adx,"PLUS_DI14":pos[-1],"MINUS_DI14":neg[-1],
            "BB_mid":mid,"BB_upper":mid+2*sd,"BB_lower":mid-2*sd}


def compare_indicators(frame):
    import numpy as np
    import pandas as pd
    import technical_indicators as ti
    if not isinstance(frame,pd.DataFrame) or not 250<=len(frame)<=2000:
        raise ValueError("invalid history length")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("invalid index")
    if not frame.columns.is_unique or not {"Open","High","Low","Close"}.issubset(frame.columns):
        raise ValueError("invalid columns")
    o,h,l,c=frame[["Open","High","Low","Close"]].to_numpy(dtype=float).T
    if not np.isfinite([o,h,l,c]).all() or (o<=0).any() or (o<l).any() or (o>h).any():
        raise ValueError("invalid open or OHLC")
    expected=reference_values(h,l,c)
    a,p,m=ti.calc_adx(frame,14)
    mac,sig,hist=ti.calc_macd(frame.Close,12,26,9)
    mid,upper,lower=ti.calc_bollinger(frame.Close,20,2)
    actual=dict(zip(expected,(ti.calc_rsi(frame.Close,14),ti.calc_ema(frame.Close,20),
        ti.calc_sma(frame.Close,50),mac,sig,hist,ti.calc_atr(frame,14),a,p,m,mid,upper,lower)))
    out={}
    for name,right in expected.items():
        left=float(actual[name])
        # One-half displayed rounding unit + small floating point allowance.
        # Mature 600-bar fixtures make seed-convention effects negligible.
        tolerance=.050001 if name in ("RSI14","ADX14","PLUS_DI14","MINUS_DI14") else .005001
        finite=math.isfinite(left) and math.isfinite(right)
        delta=abs(left-right) if finite else None
        out[name]={"existing":left if math.isfinite(left) else None,
                   "reference":right if math.isfinite(right) else None,
                   "absolute_difference":delta,"tolerance":tolerance,
                   "status":"MATCH" if finite and delta<=tolerance else "DIFFERENCE"}
    return out


KINDS=("flat","rising","falling","volatile","gaps")


def fixture(kind):
    import random
    import pandas as pd
    rng=random.Random(20261128)
    n=600
    if kind=="flat": c=[100.0]*n
    elif kind=="rising": c=[100+i*.3 for i in range(n)]
    elif kind=="falling": c=[400-i*.3 for i in range(n)]
    elif kind in ("volatile","gaps"):
        c=[200.0]
        for i in range(1,n):
            jump=12 if kind=="gaps" and i%31==0 else 0
            c.append(c[-1]+rng.uniform(-2,2)+jump)
    else: raise ValueError("unknown fixture")
    o=[x if kind!="gaps" else x+rng.uniform(-.5,.5) for x in c]
    h=[max(x,y)+1 for x,y in zip(c,o)]
    l=[min(x,y)-1 for x,y in zip(c,o)]
    return pd.DataFrame({"Open":o,"High":h,"Low":l,"Close":c},
                        index=pd.bdate_range("2023-01-01",periods=n))


def main():
    comparisons={k:compare_indicators(fixture(k)) for k in KINDS}
    report={"data":"SYNTHETIC_ONLY","reference":"independent_scalar_sma_seeded_v1",
            "production_signals_changed":False,"comparisons":comparisons}
    print(json.dumps(report,indent=2,allow_nan=False))
    return int(any(v["status"]!="MATCH" for rows in comparisons.values() for v in rows.values()))


if __name__=="__main__":
    raise SystemExit(main())
