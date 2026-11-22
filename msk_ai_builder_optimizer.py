"""Optional, offline minimum-variance sizing. No price fetch or expected-return forecast."""
from datetime import date
from decimal import Decimal
import math


def validated_returns(history, symbols, expected_session):
    """Check a bounded, aligned operator-approved adjusted-return artifact.

    These checks validate shape and dates, not authenticity. The publisher
    must independently reconcile corporate actions and official sessions.
    """
    if not isinstance(history, dict) or history.get("status") != "APPROVED_ADJUSTED_DAILY":
        raise ValueError("Approved adjusted history required")
    days = history["sessions"]
    rows = history["returns_by_symbol"]
    if (not isinstance(days, list) or not 126 <= len(days) <= 252
            or not isinstance(rows, dict) or set(rows) != set(symbols)):
        raise ValueError("Invalid history coverage")
    dates = [date.fromisoformat(day) for day in days]
    if (dates[-1] != expected_session or (dates[-1] - dates[0]).days < 150
            or any(not 1 <= (b - a).days <= 7 for a, b in zip(dates, dates[1:]))):
        raise ValueError("Stale or unaligned history")
    values = {}
    for symbol in symbols:
        row = rows[symbol]
        if not isinstance(row, list) or len(row) != len(dates):
            raise ValueError("Incomplete return series")
        if any(type(v) not in (int, float, Decimal) for v in row):
            raise ValueError("Invalid return observation")
        series = [float(v) for v in row]
        if (any(not math.isfinite(v) or not -1 < v <= 1 for v in series)
                or max(series) - min(series) < 1e-8):
            raise ValueError("Invalid or constant return series")
        values[symbol] = series
    import pandas as pd
    return pd.DataFrame(values, index=dates)


def minimum_variance_amounts(selected, budget, stock_cap, returns):
    """Use a fixed invested sleeve, stock limits and a one-share minimum.

    The covariance uses annualized sample returns and 20% diagonal shrinkage.
    No forecast return is supplied to the optimizer. Refuse missing or failed
    optimization rather than silently switching to equal weighting.
    """
    import numpy as np
    from pypfopt import EfficientFrontier
    if not selected or budget <= 0:
        return {}
    symbols = [item.symbol for item in selected]
    if not all(s in returns.columns for s in symbols):
        raise ValueError("Missing approved returns")
    cov = returns[symbols].cov().to_numpy() * 252
    cov = .8 * cov + .2 * np.diag(np.diag(cov))
    if not np.isfinite(cov).all() or np.min(np.linalg.eigvalsh(cov)) <= 0:
        raise ValueError("Invalid covariance")
    limit = min(1., float(stock_cap / budget))
    lower = [float(item.close_rupees / budget) for item in selected]
    if sum(lower) > 1 + 1e-10 or any(v > limit for v in lower):
        raise ValueError("Infeasible whole-share bounds")
    if len(selected) == 1:
        fractions = np.array([1.])
    else:
        import pandas as pd
        matrix = pd.DataFrame(cov, index=symbols, columns=symbols)
        ef = EfficientFrontier(None, matrix, weight_bounds=list(zip(lower, [limit]*len(lower))),
                               solver="CLARABEL")
        ef.min_volatility()
        fractions = np.asarray(ef.weights, dtype=float)
    if (not np.isfinite(fractions).all() or abs(float(fractions.sum()) - 1) > 1e-6
            or any(w < lo - 1e-7 or w > limit + 1e-7 for w, lo in zip(fractions, lower))):
        raise ValueError("Invalid solver result")
    # Decimal and conservative rounding ensure the optimizer cannot exceed
    # either the stock cap or the invested sleeve. Never round up a budget.
    result = {}
    remaining = budget
    for i, (item, weight) in enumerate(zip(selected, fractions)):
        reserve = sum((row.close_rupees for row in selected[i+1:]), Decimal(0))
        target = min(stock_cap, remaining - reserve,
                     max(item.close_rupees, budget * Decimal(str(float(weight)))))
        if target < item.close_rupees:
            raise ValueError("Whole-share allocation infeasible")
        result[item.symbol] = target
        remaining -= target
    return result
