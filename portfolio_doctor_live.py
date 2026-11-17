"""Stateless private Portfolio Doctor: current input -> verified daily candles -> PDF bytes.

No persistent database, holdings store, upload file or report file is used.
Input reaches the isolated worker through stdin; PDF returns through stdout.
The process is killed on deadline. Only non-sensitive error codes are logged.
"""
import csv
import io
import math
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone

MAX_BYTES = 5 * 1024 * 1024
MAX_STOCKS = 5
IST = timezone(timedelta(hours=5, minutes=30))
CODES = {"BAD_INPUT", "NO_PRICES", "STALE_PRICES", "PDF_FAILED", "TIMEOUT",
         "WORKER_FAILED", "NO_CAPACITY"}

class PortfolioDoctorError(RuntimeError):
    def __init__(self, code="WORKER_FAILED"):
        super().__init__(code)
        self.code = code if code in CODES else "WORKER_FAILED"


def parse_holdings(filename, payload):
    """Strict 1..5 symbol, quantity and purchase-price records; no storage."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BYTES:
        raise PortfolioDoctorError("BAD_INPUT")
    name = (filename or "").lower()
    try:
        if name.endswith(".xlsx"):
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                parts = z.infolist()
                if len(parts) > 250 or sum(p.file_size for p in parts) > 20 * 1024 * 1024:
                    raise PortfolioDoctorError("BAD_INPUT")
                if any(p.file_size > 10 * 1024 * 1024 for p in parts):
                    raise PortfolioDoctorError("BAD_INPUT")
            import openpyxl
            book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
            try:
                rows = list(book.worksheets[0].iter_rows(max_row=8, max_col=16, values_only=True))
            finally:
                book.close()
        elif name.endswith(".csv"):
            text = payload.decode("utf-8-sig")
            if "\x00" in text:
                raise PortfolioDoctorError("BAD_INPUT")
            rows = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                if any(mark in line for mark in (",", ";", "\t")):
                    # Preserve each cell rather than splitting ticker characters like X.
                    row = next(csv.reader([line], delimiter=("," if "," in line else ";" if ";" in line else "\t")))
                else:
                    row = re.split(r"\s+", line.strip())
                rows.append(row)
                if len(rows) > 7:
                    raise PortfolioDoctorError("BAD_INPUT")
        else:
            raise PortfolioDoctorError("BAD_INPUT")
        rows = [r for r in rows if r and any(str(c or "").strip() for c in r)]
        if rows and len(rows[0]) >= 3:
            header = [re.sub(r"[^a-z0-9]", "", str(v or "").lower()) for v in rows[0][:3]]
            if (header[0] in ("symbol", "ticker", "stock", "tradingsymbol") and
                    header[1] in ("qty", "quantity", "shares") and
                    header[2] in ("buyprice", "averageprice", "avgprice", "price", "buy")):
                rows = rows[1:]
        if not 1 <= len(rows) <= MAX_STOCKS:
            raise PortfolioDoctorError("BAD_INPUT")
        result, seen = [], set()
        for r in rows:
            if len(r) != 3:
                raise PortfolioDoctorError("BAD_INPUT")
            sym = str(r[0] or "").strip().upper().removesuffix(".NS")
            if not re.fullmatch(r"[A-Z0-9&-]{2,20}", sym) or sym in seen:
                raise PortfolioDoctorError("BAD_INPUT")
            qty, buy = float(r[1]), float(r[2])
            if (not math.isfinite(qty) or not math.isfinite(buy) or
                    qty <= 0 or buy <= 0 or qty > 1e9 or buy > 1e9):
                raise PortfolioDoctorError("BAD_INPUT")
            seen.add(sym)
            result.append({"symbol": sym, "qty": qty, "buy": buy})
        return result
    except PortfolioDoctorError:
        raise
    except (UnicodeError, ValueError, TypeError, IndexError, OSError, KeyError,
            zipfile.BadZipFile, ImportError, StopIteration) as exc:
        raise PortfolioDoctorError("BAD_INPUT") from exc


def _ema(values, period):
    if len(values) < period:
        return None
    factor = 2.0 / (period + 1.0)
    value = float(values[0])
    for p in values[1:]:
        value = factor * float(p) + (1.0 - factor) * value
    return value


def _rsi(values):
    if len(values) < 16:
        return None
    moves = [float(b) - float(a) for a, b in zip(values[-16:-1], values[-15:])]
    gains = sum(max(v, 0.0) for v in moves) / len(moves)
    losses = sum(max(-v, 0.0) for v in moves) / len(moves)
    if gains == losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def price_holdings(holdings, *, include_frames=False):
    """Use verified OHLC only, not an assumed live quote or fabricated tick."""
    from market_data import bulk_history
    symbols = [h["symbol"] for h in holdings]
    frames = bulk_history(symbols, "1y", chunk_size=5, retries=0,
                          max_fallbacks=5, download_timeout=12)
    today = datetime.now(IST).date()
    out = []
    for holding in holdings:
        frame = frames.get(holding["symbol"])
        if frame is None or frame.empty or "Close" not in frame:
            raise PortfolioDoctorError("NO_PRICES")
        try:
            valid = frame["Close"].dropna()
            values = [float(v) for v in valid.tolist()]
            if len(values) < 20 or any(not math.isfinite(v) or v <= 0 for v in values):
                raise PortfolioDoctorError("NO_PRICES")
            last_at = valid.index[-1]
            price_date = last_at.date()
            age = (today - price_date).days
            if not (0 <= age <= 3):
                raise PortfolioDoctorError("STALE_PRICES")
            close = values[-1]
            invested = holding["qty"] * holding["buy"]
            value = holding["qty"] * close
            out.append({**holding, "close": close, "date": price_date.isoformat(),
                        "invested": invested, "value": value, "pnl": value - invested,
                        "pnl_pct": 100.0 * (close / holding["buy"] - 1.0),
                        "ema20": _ema(values, 20), "ema50": _ema(values, 50),
                        "rsi14": _rsi(values),
                        "high20": max(values[-20:]), "low20": min(values[-20:])})
        except PortfolioDoctorError:
            raise
        except (TypeError, ValueError, IndexError, OverflowError, AttributeError) as exc:
            raise PortfolioDoctorError("NO_PRICES") from exc
    total = sum(h["value"] for h in out)
    if not total or not math.isfinite(total):
        raise PortfolioDoctorError("NO_PRICES")
    for h in out:
        h["weight"] = 100.0 * h["value"] / total
    return (out, frames) if include_frames else out


def build_pdf_bytes(holdings):
    """ReportLab canvas writes directly into RAM, never a filename."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    width, height = A4
    margin = 42
    y = height - 52

    def line(text, size=9, bold=False, gap=16):
        nonlocal y
        if y < 85:
            pdf.showPage()
            y = height - 52
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(margin, y, str(text)[:107])
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
    line("No BUY/SELL signal, guaranteed return or order is provided.", 8)
    line("Educational information only. Not SEBI-registered investment advice.", 8)
    pdf.showPage()
    pdf.save()
    data = stream.getvalue()
    if not (1000 <= len(data) <= MAX_BYTES and data.startswith(b"%PDF-")):
        raise PortfolioDoctorError("PDF_FAILED")
    return data


def run_isolated_memory(filename, payload, timeout=150):
    """No customer file/DB writes: stdin bytes -> killable process -> PDF bytes."""
    if not (isinstance(payload, bytes) and 0 < len(payload) <= MAX_BYTES and
            str(filename).lower().endswith((".csv", ".xlsx"))):
        raise PortfolioDoctorError("BAD_INPUT")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, os.path.abspath(__file__), "--worker", filename]
    try:
        child = subprocess.run(command, input=payload, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=timeout,
                               check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise PortfolioDoctorError("TIMEOUT") from exc
    except OSError as exc:
        raise PortfolioDoctorError("WORKER_FAILED") from exc
    if child.returncode != 0:
        # Ignore child library logs. Only a whitelisted last-line code is used.
        code = "WORKER_FAILED"
        for text_line in child.stderr[-4096:].decode("utf-8", errors="replace").splitlines():
            if text_line.startswith("MSK_DOCTOR_CODE="):
                candidate = text_line.partition("=")[2].strip()
                if candidate in CODES:
                    code = candidate
        raise PortfolioDoctorError(code)
    raw = child.stdout
    if not (1000 <= len(raw) <= MAX_BYTES and raw.startswith(b"%PDF-")):
        raise PortfolioDoctorError("PDF_FAILED")
    try:
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(raw), strict=True)
        if not 1 <= len(pdf.pages) <= 5:
            raise PortfolioDoctorError("PDF_FAILED")
    except PortfolioDoctorError:
        raise
    except Exception as exc:
        raise PortfolioDoctorError("PDF_FAILED") from exc
    return raw


def worker_main(filename):
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        holdings = parse_holdings(filename, raw)
        advanced = os.getenv("MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED", "0") == "1"
        if advanced:
            recs, frames = price_holdings(holdings, include_frames=True)
            try:
                from portfolio_doctor_advanced import assemble_review
                from portfolio_doctor_advanced_pdf import render_advanced_pdf
                review = assemble_review(
                    recs, frames, advanced_fundamentals=(
                        os.getenv("MSK_PORTFOLIO_DOCTOR_ADVANCED_FUNDAMENTALS", "0") == "1"))
                result = render_advanced_pdf(review)
            except Exception:
                # The verified basic report must remain available when optional
                # financial sources, charts or advanced layout fail.
                result = build_pdf_bytes(recs)
        else:
            recs = price_holdings(holdings)
            result = build_pdf_bytes(recs)
        sys.stdout.buffer.write(result)
    except PortfolioDoctorError as exc:
        sys.stderr.write("\nMSK_DOCTOR_CODE=" + exc.code + "\n")
        raise SystemExit(1)
    except Exception:
        # Never expose holdings, paths or provider exception text.
        sys.stderr.write("\nMSK_DOCTOR_CODE=WORKER_FAILED\n")
        raise SystemExit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    worker_main(sys.argv[2])
