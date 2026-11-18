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
import time
from contextlib import contextmanager
import zipfile
from datetime import date, datetime, time as daytime, timedelta, timezone

MAX_BYTES = 5 * 1024 * 1024
MAX_STOCKS = 5
IST = timezone(timedelta(hours=5, minutes=30))
CODES = {"BAD_INPUT", "NO_PRICES", "STALE_PRICES", "PDF_FAILED", "TIMEOUT",
         "WORKER_FAILED", "NO_CAPACITY"}

class PortfolioDoctorError(RuntimeError):
    def __init__(self, code="WORKER_FAILED"):
        super().__init__(code)
        self.code = code if code in CODES else "WORKER_FAILED"


from portfolio_doctor_input import (
    InputRejected, decode_holdings, looks_like_holdings_text,
)
from portfolio_doctor_calculations import (
    _ema, _rsi, position_values, portfolio_weights,
)


def parse_holdings(filename, payload):
    """Compatibility entry point with one privacy-safe parser error contract."""
    try:
        return decode_holdings(filename, payload)
    except InputRejected:
        raise PortfolioDoctorError("BAD_INPUT") from None


# Reviewed 18-Nov-2026 against NSE CMTR71775 and the official holiday page.
# Election closure on 15-Jan is included. Unknown years fail closed.
# https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf
# https://www.nseindia.com/resources/exchange-communication-holidays
_NSE_HOLIDAYS_2026 = frozenset(date.fromisoformat(value) for value in (
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26",
    "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
))
# NSE CMTR76941: special Sunday, post-close ends 16:00; buffer to 16:30.
# https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf
_NSE_SPECIAL_2026 = {date(2026, 11, 8): daytime(16, 30)}


def doctor_trading_session(day):
    if day.year != 2026:
        raise PortfolioDoctorError("STALE_PRICES")
    return day in _NSE_SPECIAL_2026 or (
        day.weekday() < 5 and day not in _NSE_HOLIDAYS_2026)


def previous_doctor_session(day):
    day -= timedelta(days=1)
    for _ in range(14):
        if doctor_trading_session(day):
            return day
        day -= timedelta(days=1)
    raise PortfolioDoctorError("STALE_PRICES")


def expected_doctor_session(now=None):
    stamp = now or datetime.now(IST)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    stamp = stamp.astimezone(IST)
    day = stamp.date()
    if (doctor_trading_session(day) and
            stamp.time() >= _NSE_SPECIAL_2026.get(day, daytime(16))):
        return day
    return previous_doctor_session(day)


def verified_completed_daily_frame(frame, *, now=None):
    """Require the latest completed session from the reviewed NSE calendar."""
    if frame is None or getattr(frame, "empty", True):
        raise PortfolioDoctorError("NO_PRICES")
    from market_breadth import completed_prices
    expected = expected_doctor_session(now)
    clean = completed_prices(frame, now=now)
    clean = clean.loc[clean.index.date <= expected]
    if clean.empty or "Close" not in clean or not clean.index.is_monotonic_increasing:
        raise PortfolioDoctorError("NO_PRICES")
    try:
        last_date = clean.index[-1].date()
    except (TypeError, IndexError, AttributeError):
        raise PortfolioDoctorError("NO_PRICES")
    if last_date != expected:
        raise PortfolioDoctorError("STALE_PRICES")
    return clean


def recover_fresh_daily_for_doctor(symbol, *, now=None, fetchers=None):
    """Repair a stale primary quote using independent full OHLCV histories.

    Only inside the disposable, log-suppressed Doctor worker. Each alternate
    series must independently pass the completed-session and >=20-close gates.
    Never splice prices, synthesize a candle, or use a quote as daily OHLCV.
    Explicitly avoid Stooq because corporate-action adjustment may differ.
    """
    from market_data import _clean_frame
    if fetchers is None:
        # Only Yahoo daily history shares the primary adjustment convention.
        # Raw NSE full histories can cross a split/bonus without restatement.
        # NSE recovery is confined to the separately overlap-verified bhavcopy
        # path above; never use a raw alternate series for RSI/EMA.
        # Bypass the shared provider cache.
        from data_engine import _yahoo_v8_hist
        from market_data import to_yahoo
        fetchers = (
            lambda: _yahoo_v8_hist(to_yahoo(symbol), period="1y"),
        )
    for fetch in fetchers:
        try:
            frame = _clean_frame(fetch())
            if frame is None:
                continue
            fresh = verified_completed_daily_frame(frame, now=now)
            closes = fresh["Close"].dropna()
            if len(closes) >= 20:
                return fresh
        except Exception:
            # No provider exception, ticker, prices or holdings in MSK logs.
            continue
    return None


def price_holdings(holdings, *, include_frames=False):
    """Use verified OHLC only, not an assumed live quote or fabricated tick."""
    from market_data import bulk_history
    symbols = [h["symbol"] for h in holdings]
    frames = bulk_history(symbols, "1y", chunk_size=5, retries=0,
                          max_fallbacks=0, download_timeout=12)
    now = datetime.now(IST)
    today = now.date()
    # The shared bulk provider retries only MISSING tickers. It treats a valid
    # but OLD Yahoo frame as success; the Doctor must retry those stale frames.
    failures = {}
    stale_symbols = []
    for symbol in symbols:
        try:
            frames[symbol] = verified_completed_daily_frame(frames.get(symbol), now=now)
        except PortfolioDoctorError as exc:
            failures[symbol] = exc.code
            stale_symbols.append(symbol)

    price_sources = {sym: "Verified completed daily OHLCV (provider may vary)"
                     for sym in symbols}
    if stale_symbols:
        # Source 2: NSE public EOD bhavcopy (known to publish even when Yahoo
        # daily history lags). Append only after official prior-session OHLC
        # agrees with the original full historical series.
        try:
            from portfolio_doctor_bhavcopy import recover_from_public_bhavcopy
            official = recover_from_public_bhavcopy(frames, stale_symbols, now=now)
            for sym, fresh in official.items():
                frames[sym] = fresh
                failures.pop(sym, None)
                price_sources[sym] = (
                    "NSE EOD bhavcopy + verified prior-session OHLCV history")
        except Exception:
            # Unavailable official files are not replaced with invented bars.
            pass
        stale_symbols = [sym for sym in stale_symbols if sym in failures]

    if stale_symbols:
        # All recovery is confined to a single disposable child process.
        # Bounded parallelism prevents five sequential 15+12s provider calls
        # from using up the entire 150-second report deadline.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(3, len(stale_symbols))) as pool:
            futures = [(sym, pool.submit(recover_fresh_daily_for_doctor, sym, now=now))
                       for sym in stale_symbols]
            for sym, fut in futures:
                recovered = fut.result()
                if recovered is not None:
                    frames[sym] = recovered
                    failures.pop(sym, None)
                    price_sources[sym] = "Verified alternate daily OHLCV"
    if failures:
        code = ("STALE_PRICES" if "STALE_PRICES" in failures.values()
                else "NO_PRICES")
        raise PortfolioDoctorError(code)

    out = []
    for holding in holdings:
        frame = frames[holding["symbol"]]
        try:
            valid = frame["Close"].dropna()
            values = [float(v) for v in valid.tolist()]
            if len(values) < 20 or any(not math.isfinite(v) or v <= 0 for v in values):
                raise PortfolioDoctorError("NO_PRICES")
            last_at = valid.index[-1]
            price_date = last_at.date()
            age = (today - price_date).days
            if age < 0:
                raise PortfolioDoctorError("STALE_PRICES")
            close = values[-1]
            amounts = position_values(holding, close)
            out.append({**holding, "close": close, "date": price_date.isoformat(),
                        "price_source": price_sources[holding["symbol"]],
                        **amounts,
                        "ema20": _ema(values, 20), "ema50": _ema(values, 50),
                        "rsi14": _rsi(values),
                        "high20": max(values[-20:]), "low20": min(values[-20:])})
        except PortfolioDoctorError:
            raise
        except (TypeError, ValueError, IndexError, OverflowError, AttributeError) as exc:
            raise PortfolioDoctorError("NO_PRICES") from exc
    try:
        weights = portfolio_weights(out)
    except (ValueError, TypeError, ArithmeticError):
        raise PortfolioDoctorError("NO_PRICES") from None
    for row, weight in zip(out, weights):
        row["weight"] = weight
    return (out, frames) if include_frames else out


def build_pdf_bytes(holdings):
    """Compatibility entry point; rendering failures contain no holding data."""
    try:
        from portfolio_doctor_report import build_pdf_bytes as render
        return render(holdings)
    except Exception:
        raise PortfolioDoctorError("PDF_FAILED") from None


def worker_environment(source=None):
    """Public-market worker receives runtime settings, never app credentials."""
    source = os.environ if source is None else source
    permitted = frozenset((
        "PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE",
        "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH", "VIRTUAL_ENV",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy",
        "XDG_CACHE_HOME", "CACHE_DIR", "MPLCONFIGDIR",
        "MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED",
        "MSK_PORTFOLIO_DOCTOR_ADVANCED_FUNDAMENTALS",
    ))
    result = {key: value for key, value in source.items() if key in permitted}
    result.update(PYTHONDONTWRITEBYTECODE="1", MSK_RESEARCH_ONLY="1")
    return result


def run_isolated_memory(filename, payload, timeout=150):
    """No customer file/DB writes: stdin bytes -> killable process -> PDF bytes."""
    if not (isinstance(payload, bytes) and 0 < len(payload) <= MAX_BYTES and
            str(filename).lower().endswith((".csv", ".xlsx"))):
        raise PortfolioDoctorError("BAD_INPUT")
    env = worker_environment()
    worker_name = "input.xlsx" if str(filename).lower().endswith(".xlsx") else "input.csv"
    command = [sys.executable, os.path.abspath(__file__), "--worker", worker_name]
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
        max_pages = 10 if env.get("MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED") == "1" else 5
        if not 1 <= len(pdf.pages) <= max_pages:
            raise PortfolioDoctorError("PDF_FAILED")
    except PortfolioDoctorError:
        raise
    except Exception as exc:
        raise PortfolioDoctorError("PDF_FAILED") from exc
    return raw



def enforce_worker_read_only():
    """Deny Python filesystem writes, database opens and child processes.

    Installed only in the short-lived worker before private input is read.
    Network reads remain necessary for public providers. This is a guard on
    audited Python APIs, not an operating-system sandbox.
    """
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    denied = frozenset(("sqlite3.connect", "os.mkdir", "os.remove", "os.rename",
                        "os.rmdir", "os.truncate", "os.link", "os.symlink",
                        "subprocess.Popen", "os.system"))
    def audit(event, args):
        if event in denied:
            raise PermissionError("DOCTOR_READ_ONLY")
        if event == "open":
            _, mode, flags = args
            if ((isinstance(mode, str) and any(c in mode for c in "wax+")) or
                    (isinstance(flags, int) and flags & write_flags)):
                raise PermissionError("DOCTOR_READ_ONLY")
    sys.addaudithook(audit)


def disable_worker_provider_disk_caches():
    """Only in the disposable worker, before reading private request input.

    yfinance's default SQLite timezone cache records requested ticker symbols.
    Dummy caches avoid opening those databases at all. Fail closed if the
    installed dependency no longer exposes the audited no-cache interface.
    """
    try:
        from yfinance import cache
        cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
        cache._CookieCacheManager._Cookie_cache = cache._CookieCacheDummy()
        if (cache.get_tz_cache().lookup("MSK_PRIVACY_CHECK") is not None or
                cache.get_cookie_cache().lookup("basic") is not None):
            raise RuntimeError("cache_not_disabled")
    except Exception:
        raise PortfolioDoctorError("WORKER_FAILED") from None


class OptionalReviewTimeout(BaseException):
    """Escape optional provider code that catches ordinary exceptions."""


@contextmanager
def optional_review_deadline(seconds):
    import signal
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        raise OptionalReviewTimeout()
    def expired(_signum, _frame):
        raise OptionalReviewTimeout()
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def build_optional_pdf(recs, frames, *, seconds=20):
    with optional_review_deadline(seconds):
        from portfolio_doctor_advanced import assemble_review
        from portfolio_doctor_advanced_pdf import render_advanced_pdf
        benchmark = None
        try:
            from market_data import index_history
            benchmark = verified_completed_daily_frame(
                index_history("^NSEI", "1y", retries=0, timeout=12))
        except Exception:
            benchmark = None
        review = assemble_review(
            recs, frames, benchmark=benchmark, advanced_fundamentals=(
                os.getenv("MSK_PORTFOLIO_DOCTOR_ADVANCED_FUNDAMENTALS", "0") == "1"))
        return render_advanced_pdf(review)


def worker_main(filename):
    # Private worker only: suppress inherited library logs that could reveal
    # which stocks a customer currently owns. Parent logs safe error codes.
    import logging
    logging.disable(logging.CRITICAL)
    worker_started = time.monotonic()
    try:
        enforce_worker_read_only()
        disable_worker_provider_disk_caches()
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        holdings = parse_holdings(filename, raw)
        advanced = os.getenv("MSK_PORTFOLIO_DOCTOR_ADVANCED_ENABLED", "0") == "1"
        if advanced:
            recs, frames = price_holdings(holdings, include_frames=True)
            try:
                # Reserve time for the basic PDF before the parent's hard kill.
                budget = min(20, 120 - (time.monotonic() - worker_started))
                result = build_optional_pdf(recs, frames, seconds=budget)
            except (Exception, OptionalReviewTimeout):
                # Optional research cannot consume the basic report's budget.
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
