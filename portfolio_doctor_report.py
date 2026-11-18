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


def build_pdf_bytes(holdings):
    """Render every holding in memory with exact displayed money and wrapping."""
    holdings = _report_records(holdings)
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    width, height = A4
    margin = 42
    y = height - 52

    def line(text, size=9, bold=False, gap=16):
        nonlocal y
        from reportlab.lib.utils import simpleSplit
        face = "Helvetica-Bold" if bold else "Helvetica"
        for part in simpleSplit(str(text), face, size, width - 2 * margin) or [""]:
            if y < 85:
                pdf.showPage()
                y = height - 52
            pdf.setFont(face, size)
            pdf.drawString(margin, y, part)
            y -= gap

    def divider():
        nonlocal y
        pdf.line(margin, y + 5, width - margin, y + 5)
        y -= 12

    total_cost = sum(h["invested"] for h in holdings)
    total_value = sum(h["value"] for h in holdings)
    diff = total_value - total_cost
    now = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    line("MSK | PORTFOLIO DOCTOR", 17, True, 27)
    line("Private on-demand educational report | " + now, 10, False, 21)
    line("Source: dated daily OHLCV via Yahoo/provider fallback. NOT a live quote.", 9)
    line("This service does not save holdings or PDFs in an MSK database.", 9, False, 20)
    divider()
    line("PORTFOLIO SUMMARY", 11, True, 22)
    line("Invested: Rs {:,.2f}   Last-close value: Rs {:,.2f}".format(total_cost, total_value), 10)
    line("Unrealised P/L: Rs {:+,.2f} ({:+.2f}%)".format(diff, 100 * diff / total_cost), 10, True, 23)
    divider()
    line("HOLDING-WISE VERIFIED RESULTS", 11, True, 23)
    for h in holdings:
        line("{} | Qty {:g} | Average purchase Rs {:,.2f}".format(
            h["symbol"], h["qty"], h["buy"]), 10, True, 17)
        line("Last close Rs {:,.2f} ({}) | Value Rs {:,.2f}".format(
            h["close"], h["date"], h["value"]))
        if h.get("price_source", "").startswith("NSE EOD"):
            line("Source: NSE official daily file; historical OHLC cross-checked.", 8)
        line("P/L Rs {:+,.2f} ({:+.2f}%) | Portfolio weight {:.1f}%".format(
            h["pnl"], h["pnl_pct"], h["weight"]))
        trend = ("Above EMA20 and EMA50" if h["ema50"] and h["close"] > h["ema20"] > h["ema50"]
                 else "Below EMA20 and EMA50" if h["ema50"] and h["close"] < h["ema20"] < h["ema50"]
                 else "Mixed / insufficient trend confirmation")
        rsi = "N/A" if h["rsi14"] is None else "{:.1f}".format(h["rsi14"])
        line("EMA20 Rs {:.2f} | EMA50 {} | RSI14 {} | {}".format(
            h["ema20"], "Rs {:.2f}".format(h["ema50"]) if h["ema50"] else "N/A",
            rsi, trend), 8, False, 15)
        line("20-close high Rs {:.2f} | low Rs {:.2f}".format(h["high20"], h["low20"]), 8, False, 21)
    divider()
    line("RISK CHECKS", 11, True, 21)
    flags = []
    for h in holdings:
        if h["weight"] > 40:
            flags.append("{} concentration {:.1f}% (over 40% of current portfolio value)".format(
                h["symbol"], h["weight"]))
        if h["rsi14"] is not None and h["rsi14"] > 70:
            flags.append("{} RSI above 70; extended/overbought conditions".format(h["symbol"]))
        if h["ema50"] and h["close"] < h["ema50"]:
            flags.append("{} close below its 50-session exponential moving average".format(h["symbol"]))
    for msg in (flags or ["No threshold flags from this limited price-only check."]):
        line("- " + msg, 9, False, 17)
    y -= 6
    divider()
    line("LIMITS & DISCLAIMER", 9, True)
    line("Last close is not a tradeable live price. Prices can change after the last session.", 8)
    line("Indicators are calculated from historical closes, not audited fundamentals.", 8)
    line("Cost and P/L use your entered current share count and average purchase cost.", 8)
    line("Use broker-adjusted quantities/costs after splits or bonuses; these are not inferred.", 8)
    line("Telegram processes your messages and PDF under its own storage policies.", 8)
    line("No BUY/SELL signal, guaranteed return or order is provided.", 8)
    line("Educational information only. Not SEBI-registered investment advice.", 8)
    pdf.showPage()
    pdf.save()
    data = stream.getvalue()
    if not (1000 <= len(data) <= MAX_BYTES and data.startswith(b"%PDF-")):
        raise PdfRejected()
    return data


