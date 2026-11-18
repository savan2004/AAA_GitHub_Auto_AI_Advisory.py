"""Private Doctor input decoding/classification. No providers, files or logs."""
import csv
import io
import math
import re
import zipfile
from decimal import Decimal, InvalidOperation

MAX_BYTES = 5 * 1024 * 1024
MAX_STOCKS = 5
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 64


class InputRejected(ValueError):
    pass


def _unnumber(line):
    """Strip a list marker, never a numeric BSE scrip code."""
    return re.sub(r"^\s*\d{1,3}[.)]\s+", "", line, count=1)


def _cells(line):
    line = _unnumber(line)
    if any(mark in line for mark in (",", ";", "\t")):
        mark = "," if "," in line else ";" if ";" in line else "\t"
        return next(csv.reader([line], delimiter=mark, strict=True))
    return re.split(r"\s+", line.strip())


def looks_like_holdings_text(text):
    """Protect valid and malformed private-looking rows from general search."""
    if not isinstance(text, str):
        return False
    for line in text[:MAX_BYTES].splitlines():
        try:
            try:
                cells = _cells(line)
            except csv.Error:
                # Malformed CSV is still private-looking, not a search query.
                cells = [cell.strip('"') for cell in re.split(r"[,;\t ]+", _unnumber(line).strip())]
            if len(cells) < 2:
                continue
            if " ".join(cell.upper() for cell in cells) in {
                    "NIFTY 50", "NIFTY 100", "NIFTY 200", "NIFTY 500", "SENSEX 30"}:
                continue
            symbol = cells[0].strip().upper().removeprefix("NSE:").removeprefix("BSE:")
            symbol = symbol.removesuffix(".NS").removesuffix(".BO")
            if not re.fullmatch(r"[A-Z0-9&-]{2,20}", symbol):
                continue
            # Either numeric column is enough to recognise malformed holdings.
            for cell in cells[1:3]:
                try:
                    float(cell)
                    return True
                except (ValueError, TypeError, OverflowError):
                    pass
        except (ValueError, TypeError, csv.Error, StopIteration):
            continue
    return False


def _csv_rows(payload):
    text = payload.decode("utf-8-sig")
    if "\x00" in text:
        raise InputRejected()
    rows = []
    for line in text.splitlines():
        if line.strip():
            rows.append(_cells(line))
            if len(rows) > MAX_STOCKS + 1:
                raise InputRejected()
    return rows


def _xlsx_rows(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        parts = archive.infolist()
        names = [item.filename for item in parts]
        if (len(parts) > 250 or len(names) != len(set(names)) or
                sum(item.file_size for item in parts) > MAX_EXPANDED_BYTES or
                any(item.file_size > 10 * 1024 * 1024 or item.flag_bits & 1
                    for item in parts) or
                any(name.startswith(("/", "\\")) or ".." in name.split("/")
                    or name.lower().endswith("vbaproject.bin") for name in names)):
            raise InputRejected()
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True,
                                 data_only=True, keep_links=False)
    try:
        if len(book.worksheets) != 1:
            raise InputRejected()
        sheet = book.worksheets[0]
        sheet.reset_dimensions()
        rows = []
        for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
            if number > MAX_ROWS or len(row) > MAX_COLUMNS:
                raise InputRejected()
            cells = list(row)
            while cells and cells[-1] is None:
                cells.pop()
            if any(str(value if value is not None else "").strip() for value in cells):
                rows.append(cells)
                if len(rows) > MAX_STOCKS + 1:
                    raise InputRejected()
        return rows
    finally:
        book.close()


def _positive(value):
    if isinstance(value, bool):
        raise InputRejected()
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise InputRejected() from None
    if not number.is_finite() or not 0 < number <= Decimal("1e9"):
        raise InputRejected()
    result = float(number)
    if not math.isfinite(result) or result <= 0 or Decimal(str(result)) != number:
        raise InputRejected()
    return result


def decode_holdings(filename, payload):
    """Return exactly 1–5 unique canonical Indian symbols, quantities and costs."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BYTES:
        raise InputRejected()
    name = str(filename or "").lower()
    try:
        if name.endswith(".csv"):
            rows = _csv_rows(payload)
        elif name.endswith(".xlsx"):
            rows = _xlsx_rows(payload)
        else:
            raise InputRejected()
        if rows and len(rows[0]) >= 3:
            header = [re.sub(r"[^a-z0-9]", "", str(v or "").lower())
                      for v in rows[0][:3]]
            if (header[0] in ("symbol", "ticker", "stock", "tradingsymbol") and
                    header[1] in ("qty", "quantity", "shares") and
                    header[2] in ("buyprice", "averageprice", "avgprice", "price", "buy")):
                if len(rows[0]) != 3:
                    raise InputRejected()
                rows = rows[1:]
        if not 1 <= len(rows) <= MAX_STOCKS:
            raise InputRejected()
        result, seen = [], set()
        for row in rows:
            if len(row) != 3:
                raise InputRejected()
            raw_symbol = str(row[0] or "").strip().upper()
            bse = raw_symbol.startswith("BSE:") or raw_symbol.endswith(".BO")
            if ((raw_symbol.startswith("NSE:") and raw_symbol.endswith(".BO")) or
                    (raw_symbol.startswith("BSE:") and raw_symbol.endswith(".NS"))):
                raise InputRejected()
            symbol = raw_symbol.removeprefix("NSE:").removeprefix("BSE:")
            symbol = symbol.removesuffix(".NS").removesuffix(".BO")
            if not re.fullmatch(r"[A-Z0-9&-]{2,20}", symbol) or symbol in seen:
                raise InputRejected()
            item = {"symbol": symbol, "qty": _positive(row[1]), "buy": _positive(row[2])}
            if bse:
                item["exchange"] = "BSE"
            result.append(item)
            seen.add(symbol)
        return result
    except InputRejected:
        raise
    except Exception:
        # Third-party XML/ZIP/CSV errors never escape with customer content.
        raise InputRejected() from None
