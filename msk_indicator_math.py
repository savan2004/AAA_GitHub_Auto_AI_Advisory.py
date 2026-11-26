"""Shared technical indicator series, independent of bot configuration.
EMA preserves first-observation seed for legacy strategies. ATR/ADX use the
existing recursive Wilder convention; insufficient data is NaN. Bollinger
uses population standard deviation. No I/O or financial recommendation.
"""
import numpy as np
import pandas as pd

def ema(close, period, *, min_periods=0):
    return close.ewm(span=period, adjust=False, min_periods=min_periods).mean()

def macd(close, fast=12, slow=26, signal=9):
    line = ema(close, fast)-ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line-sig

def wilder(series, period=14):
    return series.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()

def true_range(high, low, close):
    return pd.concat([high-low,(high-close.shift()).abs(),
                      (low-close.shift()).abs()],axis=1).max(axis=1)

def atr_series(high, low, close, period=14):
    return wilder(true_range(high,low,close),period)

def directional_series(high, low, close, period=14):
    atr = atr_series(high,low,close,period)
    up, down = high.diff(), -low.diff()
    pdm = up.where((up>down)&(up>0),0.0)
    mdm = down.where((down>up)&(down>0),0.0)
    pdi = 100*wilder(pdm,period)/atr.replace(0,np.nan)
    mdi = 100*wilder(mdm,period)/atr.replace(0,np.nan)
    total = pdi+mdi
    dx = (100*(pdi-mdi).abs()/total.replace(0,np.nan)).mask(total==0,0.0)
    return atr,wilder(dx,period),pdi,mdi

def bollinger(close, window=20, num_sd=2.0):
    mid=close.rolling(window).mean()
    std=close.rolling(window).std(ddof=0)
    return mid,mid+num_sd*std,mid-num_sd*std
