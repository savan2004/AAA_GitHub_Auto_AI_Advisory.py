"""Public-market integration probe with fixed synthetic holdings only.
Runs the actual disposable parser/provider/calculation/PDF worker. No Telegram.
Never accepts customer input and never saves the generated PDF.
"""
import io
import os
import re
from decimal import Decimal
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from portfolio_doctor_live import run_isolated_memory, expected_doctor_session
from portfolio_doctor_bhavcopy import fetch_nse_bhavcopy_rows


def main():
    cases = (
        (b"INFY 2 1000", "2,000.00", ("INFY",)),
        (b"INFY 1 1000\nTCS 2 1000\nRELIANCE 3 1000\nHDFCBANK 4 1000\nITC 5 1000",
         "15,000.00", ("INFY", "TCS", "RELIANCE", "HDFCBANK", "ITC")),
    )
    # Synthetic test sandbox only: no uploaded/customer data is written here.
    with tempfile.TemporaryDirectory(prefix="doctor-public-probe-") as folder:
        env = {key: folder for key in ("HOME", "XDG_CACHE_HOME", "CACHE_DIR", "MPLCONFIGDIR")}
        env.update(MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED="0",
                   PYTHONDONTWRITEBYTECODE="1")
        for payload, invested, symbols in cases:
            start = time.monotonic()
            with patch.dict(os.environ, env):
                data = run_isolated_memory("synthetic.csv", payload, timeout=150)
            pages = PdfReader(io.BytesIO(data), strict=True).pages
            text = "\n".join(page.extract_text() or "" for page in pages)
            assert all(symbol in text for symbol in symbols), "incomplete PDF holdings"
            assert "Invested: Rs " + invested in text, "incorrect invested total"

            assert "Unrealised P/L" in text and "RSI14" in text, "missing financial content"
            money = lambda value: Decimal(value.replace(",", ""))
            expected = expected_doctor_session()
            official = fetch_nse_bhavcopy_rows(expected, symbols)
            assert set(official) == set(symbols), "official closing-price cross-check unavailable"
            closes = re.findall(r"Last close Rs ([0-9,.]+) \(([^)]+)\) \| Value Rs ([0-9,.]+)", text)
            positions = [line.split() for line in payload.decode().splitlines()]
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
            print("MSK_DOCTOR_REAL_WORKER PASS holdings={} pages={} seconds={:.2f} cache_files=0".format(
                len(symbols), len(pages), time.monotonic() - start))
            del data, text, payload
    print("MSK_DOCTOR_REAL_WORKER telegram_delivery=NOT_TESTED")


if __name__ == "__main__":
    main()
