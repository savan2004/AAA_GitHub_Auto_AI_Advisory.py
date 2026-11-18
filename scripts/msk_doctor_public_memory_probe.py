"""Actual CSV/XLSX and concurrent Doctor workers using fixed public test positions.
No customer input, Telegram sends or saved PDFs. Each subprocess keeps its own
stdin/stdout; official exchange closes independently check rendered valuations.
"""
import io
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from portfolio_doctor_live import run_isolated_memory, expected_doctor_session
from portfolio_doctor_bhavcopy import fetch_nse_bhavcopy_rows


def _xlsx(positions):
    import openpyxl
    book = openpyxl.Workbook()
    book.active.append(["Symbol", "Qty", "Buy Price"])
    for symbol, qty, buy in positions:
        book.active.append([symbol, float(qty), float(buy)])
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def _case(positions, *, xlsx=False, forbidden=()):
    payload = (_xlsx(positions) if xlsx else
               "\n".join(" ".join(row) for row in positions).encode())
    return ("synthetic.xlsx" if xlsx else "synthetic.csv",
            payload, positions, forbidden)


def _run(case, folder):
    filename, payload, positions, forbidden = case
    symbols = tuple(row[0] for row in positions)
    invested = "{:,.2f}".format(sum(Decimal(qty) * Decimal(buy)
                                    for _, qty, buy in positions))
    start = time.monotonic()
    data = run_isolated_memory(filename, payload, timeout=150)
    pages = PdfReader(io.BytesIO(data), strict=True).pages
    text = "\n".join(page.extract_text() or "" for page in pages)
    assert all(symbol in text for symbol in symbols), "incomplete PDF holdings"
    assert not any(symbol in text for symbol in forbidden), "concurrent portfolio data crossed requests"
    assert "Invested: Rs " + invested in text, "incorrect invested total"

    assert "Unrealised P/L" in text and "RSI14" in text, "missing financial content"
    if b".BO" in payload:
        assert "BSE: exact ticker" in text, "explicit BSE request lost exchange identity"
    money = lambda value: Decimal(value.replace(",", ""))
    expected = expected_doctor_session()
    official = fetch_nse_bhavcopy_rows(expected, symbols)
    assert set(official) == set(symbols), "official closing-price cross-check unavailable"
    closes = re.findall(r"Last close Rs ([0-9,.]+) \(([^)]+)\) \| Value Rs ([0-9,.]+)", text)
    assert len(closes) == len(positions), "missing holding valuations"
    value_total = Decimal("0")
    for (symbol, qty, buy), (close, day, value) in zip(positions, closes):
        assert day == str(expected), "wrong PDF session"
        calc = money(close) * Decimal(qty)
        assert abs(calc - money(value)) <= Decimal("0.05"), "wrong holding value"
        exchange_close = Decimal(str(official[symbol]["Close"]))
        assert abs(money(close) / exchange_close - 1) <= Decimal("0.0075"), "NSE/Yahoo close disagreement"
        value_total += calc
    summary = re.search(r"Last-close value: Rs ([0-9,.]+)", text)
    pnl = re.search(r"Unrealised P/L: Rs ([+0-9,.-]+) \(([+0-9.-]+)%\)", text)
    assert summary and pnl, "missing portfolio financial totals"
    assert abs(money(summary[1]) - value_total) <= Decimal("0.05"), "wrong current total"
    calculated_pnl = value_total - money(invested)
    assert abs(money(pnl[1]) - calculated_pnl) <= Decimal("0.05"), "wrong total P/L"
    assert abs(money(pnl[2]) - calculated_pnl / money(invested) * 100) <= Decimal("0.01"), "wrong P/L percent"
    weights = [money(value) for value in re.findall(r"Portfolio weight ([0-9.]+)%", text)]
    assert len(weights) == len(positions), "missing weights"
    assert abs(sum(weights) - 100) <= Decimal("0.3"), "weights do not total 100"
    for weight, (_, qty, _), (close, _, _) in zip(weights, positions, closes):
        assert abs(weight - money(close) * Decimal(qty) / value_total * 100) <= Decimal("0.051"), "wrong weight"


    assert not list(Path(folder).rglob("*")), "provider created persistent cache"
    print("MSK_DOCTOR_REAL_WORKER PASS format={} holdings={} pages={} seconds={:.2f} cache_files=0".format(
        filename.rsplit(".", 1)[1], len(symbols), len(pages), time.monotonic() - start))
    return len(pages)


def main():
    one = (("INFY", "2", "1000"),)
    five = tuple((symbol, str(i), "1000") for i, symbol in enumerate(
        ("INFY", "TCS", "RELIANCE", "HDFCBANK", "ITC"), 1))
    client = (("SBIN", "100", "840"), ("BHEL", "150", "150"))
    numbered = ("synthetic.csv", b"1. sbin 100 840\n2. Bhel 150 150", client, ())
    sequential = (_case(one), _case(five), _case(five, xlsx=True),
                  _case(client), numbered,
                  ("synthetic.csv", b"SBIN.BO 100 840", (client[0],), ()))
    concurrent = (
        _case(one, forbidden=("TCS",)),
        _case((("TCS", "3", "1000"),), forbidden=("INFY",)),
    )
    # Synthetic test sandbox only. The patch is stable while both child
    # processes run; thread-local payloads and pipe results never share state.
    with tempfile.TemporaryDirectory(prefix="doctor-public-probe-") as folder:
        env = {key: folder for key in ("HOME", "XDG_CACHE_HOME", "CACHE_DIR", "MPLCONFIGDIR")}
        env.update(MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED="0",
                   PYTHONDONTWRITEBYTECODE="1")
        with patch.dict(os.environ, env):
            for case in sequential:
                _run(case, folder)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(_run, case, folder) for case in concurrent]
                assert all(future.result() >= 1 for future in pending)
        assert not list(Path(folder).rglob("*")), "request data persisted"
    print("MSK_DOCTOR_CONCURRENT_WORKERS PASS isolated=2")
    print("MSK_DOCTOR_REAL_WORKER telegram_delivery=NOT_TESTED")


if __name__ == "__main__":
    main()
