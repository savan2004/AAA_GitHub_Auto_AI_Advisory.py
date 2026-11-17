"""Public-market integration probe with fixed synthetic holdings only.
Runs the actual disposable parser/provider/calculation/PDF worker. No Telegram.
Never accepts customer input and never saves the generated PDF.
"""
import io
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from portfolio_doctor_live import run_isolated_memory


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
            assert not list(Path(folder).rglob("*")), "provider created persistent cache"
            print("MSK_DOCTOR_REAL_WORKER PASS holdings={} pages={} seconds={:.2f} cache_files=0".format(
                len(symbols), len(pages), time.monotonic() - start))
            del data, text, payload
    print("MSK_DOCTOR_REAL_WORKER telegram_delivery=NOT_TESTED")


if __name__ == "__main__":
    main()
