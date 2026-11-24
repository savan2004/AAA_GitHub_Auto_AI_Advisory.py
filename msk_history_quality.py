"""Shared daily OHLCV validation; never constructs missing prices.
Session freshness and provider authorization remain caller responsibilities.
"""
import pandas as pd

OHLCV = ["Open", "High", "Low", "Close", "Volume"]

def clean_daily_history(df):
    if not isinstance(df, pd.DataFrame) or df.empty or not all(c in df.columns for c in OHLCV):
        return None
    out = df[OHLCV].copy()
    if not out.columns.is_unique:
        return None
    for col in OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([float("inf"), float("-inf")], float("nan")).dropna(subset=OHLCV)
    valid = ((out["Open"] > 0) & (out["High"] > 0) & (out["Low"] > 0) &
             (out["Close"] > 0) & (out["Volume"] >= 0) &
             (out["High"] >= out[["Open", "Close", "Low"]].max(axis=1)) &
             (out["Low"] <= out[["Open", "Close", "High"]].min(axis=1)))
    out = out.loc[valid]
    if out.empty or not isinstance(out.index, pd.DatetimeIndex):
        return None
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out.loc[~out.index.isna()].sort_index()
    if out.empty:
        return None
    # Exact duplicate candles may collapse; conflicting duplicates invalidate the series.
    if out.index.has_duplicates:
        for _, group in out.loc[out.index.duplicated(keep=False)].groupby(level=0):
            if len(group.drop_duplicates()) != 1:
                return None
        out = out.loc[~out.index.duplicated(keep="first")]
    return out
