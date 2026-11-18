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
    """Wilder RSI14: seed from 14 changes, then smooth every later change."""
    period = 14
    if len(values) < period + 1:
        return None
    moves = [float(b) - float(a) for a, b in zip(values, values[1:])]
    gains = sum(max(v, 0.0) for v in moves[:period]) / period
    losses = sum(max(-v, 0.0) for v in moves[:period]) / period
    for move in moves[period:]:
        gains = (gains * (period - 1) + max(move, 0.0)) / period
        losses = (losses * (period - 1) + max(-move, 0.0)) / period
    if gains == losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)



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
    values = [Decimal(str(row["qty"])) * Decimal(str(row["close"])) for row in records]
    total = sum(values, Decimal(0))
    if not values or not total.is_finite() or total <= 0:
        raise ValueError("incomplete_portfolio")
    return [float(Decimal(100) * value / total) for value in values]
