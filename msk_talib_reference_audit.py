"""Offline indicator comparison, never a market signal or a data verifier."""
import json
import math

import numpy as np
import pandas as pd


def compare_indicators(frame):
    """Compare existing MSK formulas to a pinned TA-Lib reference.

    Input must be sorted, unique, finite OHLC bars, at least 250 rows.
    This diagnostic checks values only; callers must verify exchange session,
    adjustments, and source provenance separately.
    """
    import talib
    import technical_indicators as old

    if not isinstance(frame, pd.DataFrame) or not 250 <= len(frame) <= 2000:
        raise ValueError("Invalid history length")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("Unordered or duplicate history")
    values = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Invalid prices")
    o,h,l,c = values.T.copy()
    if (h < np.maximum(o,c)).any() or (l > np.minimum(o,c)).any():
        raise ValueError("Invalid OHLC geometry")
    macd, signal, hist = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
    upper, mid, lower = talib.BBANDS(c,timeperiod=20,nbdevup=2,nbdevdn=2)
    a,plus,minus = old.calc_adx(frame,14)
    m,s,g = old.calc_macd(frame["Close"],12,26,9)
    bm,bu,bl = old.calc_bollinger(frame["Close"],20,2)
    rows = [
        ("RSI14",old.calc_rsi(frame["Close"],14),talib.RSI(c,14)[-1],.11),
        ("EMA20",old.calc_ema(frame["Close"],20),talib.EMA(c,20)[-1],.011),
        ("SMA50",old.calc_sma(frame["Close"],50),talib.SMA(c,50)[-1],.011),
        ("MACD",m,macd[-1],.011),("MACD_signal",s,signal[-1],.011),
        ("MACD_hist",g,hist[-1],.011),
        ("ATR14",old.calc_atr(frame,14),talib.ATR(h,l,c,14)[-1],.011),
        ("ADX14",a,talib.ADX(h,l,c,14)[-1],.11),
        ("PLUS_DI14",plus,talib.PLUS_DI(h,l,c,14)[-1],.11),
        ("MINUS_DI14",minus,talib.MINUS_DI(h,l,c,14)[-1],.11),
        ("BB_mid",bm,mid[-1],.011),("BB_upper",bu,upper[-1],.011),
        ("BB_lower",bl,lower[-1],.011),
    ]
    result={}
    for name,left,right,tolerance in rows:
        if not math.isfinite(left) or not math.isfinite(right):
            result[name]={"status":"UNAVAILABLE"}
            continue
        difference=abs(float(left)-float(right))
        result[name]={"existing":float(left),"reference":float(right),
                      "absolute_difference":difference,"tolerance":tolerance,
                      "status":"MATCH" if difference<=tolerance else "DIFFERENCE"}
    return result


def fixture(kind):
    """Deterministic synthetic data, explicitly not traded securities."""
    rng=np.random.default_rng(20261122)
    n=300
    if kind=="flat":
        c=np.full(n,100.)
    elif kind=="rising":
        c=100.+np.arange(n)*.3
    elif kind=="falling":
        c=300.-np.arange(n)*.3
    elif kind=="volatile":
        c=200.+np.cumsum(rng.normal(0,2,n))
    else:
        raise ValueError("Unknown fixture")
    o=c.copy()
    h=c+1
    l=c-1
    return pd.DataFrame({"Open":o,"High":h,"Low":l,"Close":c},
                        index=pd.bdate_range("2025-01-01",periods=n))


if __name__=="__main__":
    import talib
    print(json.dumps({"data":"SYNTHETIC_ONLY","reference_version":talib.__version__,
                     "production_signals_changed":False,
                     "comparisons":{k:compare_indicators(fixture(k)) for k in
                                    ("flat","rising","falling","volatile")}},
                     indent=2,allow_nan=False))
