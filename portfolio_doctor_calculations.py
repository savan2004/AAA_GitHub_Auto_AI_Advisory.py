"""Pure Doctor indicators and valuation. No I/O, logging or persistent state."""
import math
from decimal import Decimal, InvalidOperation

def _ema(values, period):
    if len(values) < period:
        return None
    factor = 2.0 / (period + 1.0)
    value = float(values[0])
    for p in values[1:]:
        value = factor * float(p) + (1.0 - factor) * value
    return value


def _rsi(values):
    """Use the same central Wilder RSI as swing and intraday."""
    from msk_core_data_guard import wilder_rsi_values
    result = wilder_rsi_values(values)
    return result[-1] if result else None


def position_values(holding, close):
    """Decimal arithmetic internally; public records retain float compatibility."""
    try:
        qty, buy, price = (Decimal(str(value)) for value in
                           (holding["qty"], holding["buy"], close))
        if any(not value.is_finite() or value <= 0 for value in (qty, buy, price)):
            raise ValueError("invalid_numeric_evidence")
        invested, value = qty * buy, qty * price
        result = {"invested": float(invested), "value": float(value),
                  "pnl": float(value - invested),
                  "pnl_pct": float((price / buy - 1) * 100)}
        if any(not math.isfinite(value) for value in result.values()):
            raise ValueError("invalid_numeric_evidence")
        return result
    except (InvalidOperation, KeyError, TypeError, OverflowError):
        raise ValueError("invalid_numeric_evidence") from None


def portfolio_weights(records):
    try:
        values = []
        for row in records:
            qty, close = Decimal(str(row["qty"])), Decimal(str(row["close"]))
            if any(not v.is_finite() or v <= 0 for v in (qty, close)):
                raise ValueError("incomplete_portfolio")
            values.append(qty*close)
    except (InvalidOperation, KeyError, TypeError, OverflowError):
        raise ValueError("incomplete_portfolio") from None
    total = sum(values, Decimal(0))
    if not values or not total.is_finite() or total <= 0:
        raise ValueError("incomplete_portfolio")
    return [float(Decimal(100) * value / total) for value in values]
