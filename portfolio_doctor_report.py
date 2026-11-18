"""Basic private Doctor PDF renderer. RAM only; no provider or database access."""
import io
import re
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from portfolio_doctor_input import MAX_BYTES, MAX_STOCKS
IST = timezone(timedelta(hours=5, minutes=30))


class PdfRejected(ValueError):
    pass


def _report_records(holdings):
    if not 1 <= len(holdings) <= MAX_STOCKS:
        raise PdfRejected()
    rows, seen, dates = [], set(), set()
    try:
        for source in holdings:
            row = dict(source)
            symbol = row["symbol"]
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9&-]{2,20}", symbol) or symbol in seen:
                raise PdfRejected()
            seen.add(symbol)
            qty, buy, close = (Decimal(str(row[key])) for key in ("qty", "buy", "close"))
            if any(not value.is_finite() or value <= 0 for value in (qty, buy, close)):
                raise PdfRejected()
            price_date = date.fromisoformat(str(row.get("date", "")))
            if price_date > datetime.now(IST).date():
                raise PdfRejected()
            row["date"] = price_date.isoformat()
            dates.add(price_date)
            for field in ("ema20", "high20", "low20"):
                value = float(row[field])
                if not math.isfinite(value) or value <= 0:
                    raise PdfRejected()
            if row["high20"] < row["low20"]:
                raise PdfRejected()
            for field in ("ema50", "rsi14"):
                if row[field] is not None:
                    value = float(row[field])
                    if not math.isfinite(value):
                        raise PdfRejected()
                    if field == "ema50" and value <= 0:
                        raise PdfRejected()
                    if field == "rsi14" and not 0 <= value <= 100:
                        raise PdfRejected()
            # Recompute displayed monetary values rather than rounding a binary
            # intermediate product or trusting a caller-supplied P/L.
            row.update(qty=qty, buy=buy, close=close,
                       invested=qty * buy, value=qty * close,
                       pnl=qty * (close - buy),
                       pnl_pct=Decimal(100) * (close / buy - 1))
            rows.append(row)
        if len(dates) != 1:
            raise PdfRejected()
        total = sum((row["value"] for row in rows), Decimal(0))
        for row in rows:
            row["weight"] = Decimal(100) * row["value"] / total
        return rows
    except (KeyError, TypeError, ValueError, InvalidOperation, ArithmeticError):
        raise PdfRejected() from None


def build_pdf_bytes(holdings, *, peers=None):
    """Validate each holding, then render the styled PDF entirely in memory."""
    rows = _report_records(holdings)
    from portfolio_doctor_visual import render_pdf
    return render_pdf(rows, peers=peers, max_bytes=MAX_BYTES)
