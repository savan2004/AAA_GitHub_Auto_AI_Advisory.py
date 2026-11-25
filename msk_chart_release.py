"""MSK Chart V8 input and display gates. No provider calls or trading actions."""
import math
import numpy as np
import pandas as pd

VERSION = "Chart V8"
REQUIRED = ("Open", "High", "Low", "Close", "Volume")
MIN_RR = 1.5


def validated_daily_history(frame, session):
    """Normalize one completed-session history; reject conflicting or bad bars.

    Leading all-missing rows in a bulk provider frame are not candles. Missing
    interior observations, invalid OHLCV and conflicting duplicate days fail.
    Future/still-forming rows are excluded before validating price values.
    """
    try:
        if frame is None or frame.empty or not frame.columns.is_unique:
            return None
        data = frame.loc[:, list(REQUIRED)].copy()
        index = pd.DatetimeIndex(pd.to_datetime(data.index, errors="raise"))
        if index.hasnans:
            return None
        if index.tz is not None:
            index = index.tz_convert("Asia/Kolkata").tz_localize(None)
        data.index = index.normalize()
        data = data.loc[data.index <= pd.Timestamp(session)].sort_index(kind="stable")
        if data.empty:
            return None
        for col in REQUIRED:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        # Only skip leading empty slots from an outer-joined bulk download.
        nonempty = np.flatnonzero(~data.isna().all(axis=1).to_numpy())
        if not len(nonempty):
            return None
        data = data.iloc[nonempty[0]:]
        if not np.isfinite(data.to_numpy(dtype=float)).all():
            return None
        if not data.index.is_unique:
            for _, same in data.loc[data.index.duplicated(keep=False)].groupby(level=0):
                if not (same.to_numpy() == same.iloc[0].to_numpy()).all():
                    return None
            data = data.loc[~data.index.duplicated(keep="first")]
        good = ((data.Low > 0) & (data.Low <= data.Open) & (data.Open <= data.High)
                & (data.Low <= data.Close) & (data.Close <= data.High)
                & (data.Volume >= 0))
        if not good.all() or len(data) < 55 or str(data.index[-1].date()) != str(session):
            return None
        return data
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def wilder_rsi(prices, period=14):
    """Wilder RSI with initial simple mean; flat markets are neutral (50)."""
    prices = np.asarray(prices, dtype=float)
    if prices.ndim != 1 or period < 1 or not np.isfinite(prices).all():
        raise ValueError("Invalid RSI input")
    result = np.full(len(prices), np.nan)
    if len(prices) <= period:
        return result
    changes = np.diff(prices)
    gains = np.maximum(changes, 0)
    losses = np.maximum(-changes, 0)
    gain, loss = gains[:period].mean(), losses[:period].mean()
    for i in range(period, len(prices)):
        if i > period:
            gain = (gain*(period-1)+gains[i-1])/period
            loss = (loss*(period-1)+losses[i-1])/period
        result[i] = 50.0 if gain == loss == 0 else 100*gain/(gain+loss)
    return result


def rounded_levels(signal, close, sl, t1, t2):
    """Validate published two-decimal prices. Zero is wire-only absence."""
    if signal not in ("WAIT", "BUY", "STRONG BUY", "SELL", "STRONG SELL"):
        raise ValueError("Invalid chart signal")
    close, sl, t1, t2 = map(float, (close, sl, t1, t2))
    if not all(math.isfinite(x) for x in (close, sl, t1, t2)) or close <= 0:
        raise ValueError("Invalid chart prices")
    if sl == t1 == t2 == 0:
        return None
    if signal == "WAIT" or min(sl, t1) <= 0 or t2 < 0:
        raise ValueError("Unqualified chart levels")
    sign = 1 if signal in ("BUY", "STRONG BUY") else -1
    risk = (close-sl)*sign
    if risk <= 0 or (t1-close)*sign < MIN_RR*risk-1e-9:
        raise ValueError("Invalid chart direction or reward/risk")
    if t2 and (t2-t1)*sign <= 0:
        raise ValueError("Invalid second chart target")
    return (sl, t1, t2 or None, abs(t1-close)/risk,
            abs(t2-close)/risk if t2 else None)


def chart_plan(data, signal, atr):
    """Use shared chart structure, not a fixed rupee/ATR/reward target."""
    from msk_target_levels import calculate_targets, target_quality
    if signal == "WAIT":
        return None
    side = "LONG" if signal in ("BUY", "STRONG BUY") else "SHORT"
    close = float(data.Close.iloc[-1])
    plan = calculate_targets(data.High.to_numpy(), data.Low.to_numpy(), len(data)-1,
                             entry=close, atr=atr, side=side)
    if not plan or not target_quality(dict(plan, entry=close, atr=atr, side=side), min_rr=MIN_RR):
        return None
    # The rounded prices are what Telegram readers actually see. Do not let
    # rounding create a zero risk, skip a resistance, or inflate a ratio.
    try:
        values = [round(close, 2), round(plan["sl"], 2), round(plan["t1"], 2),
                  round(plan["t2"], 2) if plan["t2"] is not None else 0.0]
        levels = rounded_levels(signal, *values)
        return levels
    except ValueError:
        return None
