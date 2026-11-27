"""MSK Chart V8 input and display gates. No provider calls or trading actions."""
import math
import numpy as np
import pandas as pd

VERSION = "Chart V8"
REQUIRED = ("Open", "High", "Low", "Close", "Volume")
MIN_RR = 1.5


def validated_daily_history(frame, session, *, diagnostics=None):
    """Normalize one completed-session history; reject conflicting or bad bars.

    Leading all-missing rows in a bulk provider frame are not candles. Missing
    interior observations, invalid OHLCV and conflicting duplicate days fail.
    Future/still-forming rows are excluded before validating price values.
    Numeric tolerance covers only binary floating point adjustment artifacts,
    never a material difference, and never changes an observed open/close.
    """
    def reject(reason):
        if diagnostics is not None:
            diagnostics[reason] = diagnostics.get(reason, 0) + 1
        return None

    try:
        if frame is None or frame.empty:
            return reject("empty_history")
        if not frame.columns.is_unique or not all(c in frame.columns for c in REQUIRED):
            return reject("missing_or_duplicate_columns")
        data = frame.loc[:, list(REQUIRED)].copy()
        index = pd.DatetimeIndex(pd.to_datetime(data.index, errors="raise"))
        if index.hasnans:
            return reject("invalid_dates")
        if index.tz is not None:
            index = index.tz_convert("Asia/Kolkata").tz_localize(None)
        data.index = index.normalize()
        data = data.loc[data.index <= pd.Timestamp(session)].sort_index(kind="stable")
        if data.empty:
            return reject("no_completed_dates")
        for col in REQUIRED:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        nonempty = np.flatnonzero(~data.isna().all(axis=1).to_numpy())
        if not len(nonempty):
            return reject("all_missing")
        data = data.iloc[nonempty[0]:]
        if str(data.index[-1].date()) != str(session):
            return reject("stale_last_date")
        if data.iloc[-1].isna().any():
            return reject("missing_expected_session")
        if data.isna().all(axis=1).any():
            return reject("interior_empty_row")
        if not np.isfinite(data.to_numpy(dtype=float)).all():
            return reject("nonfinite_or_partial_bar")
        if not data.index.is_unique:
            for _, same in data.loc[data.index.duplicated(keep=False)].groupby(level=0):
                if not (same.to_numpy() == same.iloc[0].to_numpy()).all():
                    return reject("conflicting_duplicate")
            data = data.loc[~data.index.duplicated(keep="first")]
        if len(data) < 55:
            return reject("insufficient_history")
        if not ((data.Low > 0) & (data.High > 0) & (data.Open > 0)
                & (data.Close > 0) & (data.Volume >= 0)).all():
            return reject("nonpositive_price_or_negative_volume")
        # Yahoo adjusted OHLC can differ from a mathematically equal open or
        # close by a handful of ULPs. Reconcile only machine-scale error using
        # the observed extrema, not interpolation or synthetic candles.
        observed_high = data[["High", "Open", "Close"]].max(axis=1)
        observed_low = data[["Low", "Open", "Close"]].min(axis=1)
        scale = np.maximum(observed_high.to_numpy(), 1.0)
        tolerance = 1e-12 * scale
        if ((observed_high.to_numpy() - data.High.to_numpy() > tolerance).any()
                or (data.Low.to_numpy() - observed_low.to_numpy() > tolerance).any()
                or (data.Low.to_numpy() - data.High.to_numpy() > tolerance).any()):
            return reject("inconsistent_ohlc")
        data["High"] = observed_high
        data["Low"] = observed_low
        return data
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return reject("invalid_frame")


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


def chart_plan(data, signal, atr, *, diagnostics=None):
    """Use shared chart structure and conservatively rounded displayed prices."""
    from msk_target_levels import calculate_targets, target_quality
    from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

    def reject(reason):
        if diagnostics is not None:
            diagnostics["reason"] = reason
        return None

    if signal == "WAIT":
        return reject("no_directional_signal")
    if signal not in ("BUY", "STRONG BUY", "SELL", "STRONG SELL"):
        return reject("invalid_signal")
    side = "LONG" if signal in ("BUY", "STRONG BUY") else "SHORT"
    try:
        atr = float(atr)
        close = float(data.Close.iloc[-1])
        if not math.isfinite(atr) or atr <= 0 or not math.isfinite(close) or close <= 0:
            return reject("invalid_atr_or_reference")
        plan = calculate_targets(data.High.to_numpy(), data.Low.to_numpy(), len(data)-1,
                                 entry=close, atr=atr, side=side)
        if not plan:
            return reject("missing_structure_or_stop_too_tight")
        if not target_quality(dict(plan, entry=close, atr=atr, side=side), min_rr=MIN_RR):
            return reject("nearest_structure_below_minimum_rr")
        # Never round a stop inward or a target beyond its original buffered
        # barrier. Rounding can reduce reward/risk; revalidate displayed values.
        direction = ROUND_FLOOR if side == "LONG" else ROUND_CEILING
        def conservative(value):
            return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=direction))
        values = [round(close, 2), conservative(plan["sl"]), conservative(plan["t1"]),
                  conservative(plan["t2"]) if plan["t2"] is not None else 0.0]
        # A second structure level that collapses at display precision is absent,
        # not a reason to discard an otherwise valid first target.
        if values[3] and (values[3]-values[2])*(1 if side == "LONG" else -1) <= 0:
            values[3] = 0.0
        levels = rounded_levels(signal, *values)
        if levels is None:
            return reject("rounded_levels_unavailable")
        if abs(values[0]-values[1]) < 0.25*atr-1e-9:
            return reject("rounded_stop_too_tight")
        if diagnostics is not None:
            diagnostics["reason"] = "structure_and_rr_valid"
        return levels
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, ArithmeticError):
        return reject("rounded_or_invalid_levels")
