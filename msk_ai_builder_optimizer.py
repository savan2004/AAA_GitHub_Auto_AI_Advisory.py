"""Optional, offline minimum-variance sizing. No price fetch or expected-return forecast."""
from datetime import date
from decimal import Decimal, ROUND_CEILING
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
    """Use a fixed invested sleeve, stock limits and a meaningful whole-share minimum.

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
    # A selected name must carry a meaningful position rather than a token
    # share. Each minimum is 25% of equal sleeve weight, rounded UP to a
    # whole share. Exact integer costs also determine feasibility.
    floor_costs = []
    for item in selected:
        minimum = budget / Decimal(len(selected) * 4)
        shares = max(1, int((minimum / item.close_rupees).to_integral_value(rounding=ROUND_CEILING)))
        floor_costs.append(item.close_rupees * shares)
    if sum(floor_costs, Decimal(0)) > budget or any(v > stock_cap for v in floor_costs):
        raise ValueError("Infeasible diversified whole-share minimums")
    lower = [float(cost / budget) for cost in floor_costs]
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
    # Convert continuous weights to whole shares exactly. Start with the
    # required minimums already reserved; solver tolerances cannot violate
    # them. Reinvest residual cash only when another share reduces tracking
    # error to the continuous target, with deterministic symbol tie-breaking.
    targets = [min(stock_cap, max(floor, budget * Decimal(str(float(weight)))))
               for floor, weight in zip(floor_costs, fractions)]
    costs = list(floor_costs)
    remaining = budget - sum(costs, Decimal(0))
    for i, item in enumerate(selected):
        extra = max(Decimal(0), min(targets[i] - costs[i], remaining, stock_cap - costs[i]))
        add = int(extra // item.close_rupees) * item.close_rupees
        costs[i] += add
        remaining -= add
    # Each target has at most one beneficial rounding-up step. The squared
    # rupee-weight tracking error has the same minimizer as squared weights.
    choices = []
    for i, item in enumerate(selected):
        price = item.close_rupees
        before = targets[i] - costs[i]
        gain = before * before - (before - price) * (before - price)
        if gain > 0:
            choices.append((gain / price, item.symbol, i))
    for _, _, i in sorted(choices, key=lambda x: (-x[0], x[1])):
        price = selected[i].close_rupees
        if price <= remaining and costs[i] + price <= stock_cap:
            costs[i] += price
            remaining -= price
    if (remaining < 0 or any(cost < floor or cost > stock_cap
                             for cost, floor in zip(costs, floor_costs))):
        raise ValueError("Invalid integer allocation")
    return {item.symbol: cost for item, cost in zip(selected, costs)}
