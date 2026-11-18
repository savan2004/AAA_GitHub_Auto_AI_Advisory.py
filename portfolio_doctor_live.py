"""Stateless private Portfolio Doctor: current input -> verified daily candles -> PDF bytes.

No persistent database, holdings store, upload file or report file is used.
Input reaches the isolated worker through stdin; PDF returns through stdout.
The process is killed on deadline. Only non-sensitive error codes are logged.
"""
import io
import math
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, time as daytime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CODES = {"BAD_INPUT", "NO_PRICES", "STALE_PRICES", "PDF_FAILED", "TIMEOUT",
         "WORKER_FAILED", "NO_CAPACITY"}

class PortfolioDoctorError(RuntimeError):
    def __init__(self, code="WORKER_FAILED"):
        super().__init__(code)
        self.code = code if code in CODES else "WORKER_FAILED"


from portfolio_doctor_input import (
    MAX_BYTES, MAX_STOCKS, InputRejected, decode_holdings, looks_like_holdings_text,
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
    # NSE equity files are not guaranteed to be published by 16:00 IST.
    # Use the last completed PRIOR session through the ordinary 17:00 EOD
    # publication buffer; special sessions retain reviewed explicit cutoffs.
    if (doctor_trading_session(day) and
            stamp.time() >= _NSE_SPECIAL_2026.get(day, daytime(17))):
        return day
    return previous_doctor_session(day)


def verified_completed_daily_frame(frame, *, now=None):
    """Latest completed session, unique daily rows and finite positive closes."""
    if frame is None or getattr(frame, "empty", True):
        raise PortfolioDoctorError("NO_PRICES")
    from market_breadth import completed_prices
    expected = expected_doctor_session(now)
    try:
        # Do not hide conflicting duplicate/provider intraday rows by sorting
        # and keeping one observation during the completion filter.
        if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
            raise PortfolioDoctorError("NO_PRICES")
        days = list(frame.index.date)
        if len(days) != len(set(days)):
            raise PortfolioDoctorError("NO_PRICES")
        clean = completed_prices(frame, now=now)
        clean = clean.loc[clean.index.date <= expected]
        if clean.empty or "Close" not in clean:
            raise PortfolioDoctorError("NO_PRICES")
        if clean.index[-1].date() != expected:
            raise PortfolioDoctorError("STALE_PRICES")
        closes = [float(value) for value in clean["Close"].tolist()]
        if any(not math.isfinite(value) or value <= 0 for value in closes):
            raise PortfolioDoctorError("NO_PRICES")
        return clean
    except PortfolioDoctorError:
        raise
    except (ValueError, TypeError, AttributeError, IndexError, OverflowError):
        raise PortfolioDoctorError("NO_PRICES") from None


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


def bse_daily_for_doctor(symbol, *, now=None, explicit=False, get=None):
    """Exact BSE identity plus complete INR daily history; never fuzzy matching.

    Bare symbols retain NSE priority. A failed NSE provider alone is not proof
    of BSE identity: public search must return exactly the same BSE ticker and
    no exact NSE listing. All requests are bounded and confined to the worker.
    """
    import requests
    get = get or requests.get
    ticker = symbol + ".BO"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        import pandas as pd
        from market_data import _clean_frame
        if not explicit:
            response = get("https://query2.finance.yahoo.com/v1/finance/search",
                           params={"q": symbol, "quotesCount": 20,
                                   "newsCount": 0, "enableFuzzyQuery": "false"},
                           headers=headers, timeout=6)
            response.raise_for_status()
            quotes = response.json().get("quotes", [])
            if any(str(q.get("symbol", "")).upper() == symbol + ".NS"
                   for q in quotes):
                return None
            matches = [q for q in quotes
                       if str(q.get("symbol", "")).upper() == ticker
                       and q.get("exchange") in ("BSE", "BOM")
                       and q.get("quoteType") == "EQUITY"]
            if len(matches) != 1:
                return None
        from urllib.parse import quote
        response = get("https://query2.finance.yahoo.com/v8/finance/chart/" +
                       quote(ticker, safe=""),
                       params={"interval": "1d", "range": "1y",
                               "includePrePost": "false"},
                       headers=headers, timeout=8)
        response.raise_for_status()
        result = response.json()["chart"]["result"]
        if len(result) != 1:
            return None
        data = result[0]
        meta = data["meta"]
        if (str(meta.get("symbol", "")).upper() != ticker or
                meta.get("exchangeName") not in ("BSE", "BOM") or
                meta.get("instrumentType") != "EQUITY" or
                meta.get("currency") != "INR"):
            return None
        stamps = data["timestamp"]
        quotes = data["indicators"]["quote"][0]
        columns = {"Open": "open", "High": "high", "Low": "low",
                   "Close": "close", "Volume": "volume"}
        if not stamps or any(len(quotes[key]) != len(stamps) for key in columns.values()):
            return None
        index = pd.to_datetime(stamps, unit="s", utc=True).tz_convert(IST).normalize().tz_localize(None)
        frame = pd.DataFrame({key: quotes[value] for key, value in columns.items()},
                             index=index)
        complete = verified_completed_daily_frame(frame, now=now)
        clean = _clean_frame(complete)
        if clean is None or len(clean) != len(complete) or len(clean) < 20:
            return None
        return clean
    except Exception:
        return None


def price_holdings(holdings, *, include_frames=False):
    """Use verified OHLC only, not an assumed live quote or fabricated tick."""
    from market_data import bulk_history
    symbols = [h["symbol"] for h in holdings]
    nse_symbols = [h["symbol"] for h in holdings if h.get("exchange") != "BSE"]
    frames = bulk_history(nse_symbols, "1y", chunk_size=5, retries=0,
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
            official = recover_from_public_bhavcopy(
                frames, [sym for sym in stale_symbols if sym in nse_symbols], now=now)
            for sym, fresh in official.items():
                frames[sym] = fresh
                failures.pop(sym, None)
                price_sources[sym] = (
                    "NSE EOD bhavcopy + verified prior-session OHLCV history")
        except Exception:
            # Unavailable official files are not replaced with invented bars.
            pass
        stale_symbols = [sym for sym in stale_symbols if sym in failures]

    stale_symbols = [sym for sym in stale_symbols if sym in nse_symbols]
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
    # Explicit BSE requests never enter NSE recovery. Bare-symbol BSE fallback
    # requires exact provider identity and does not replace an existing NSE
    # history merely because its latest candle is stale.
    bse_candidates = [h for h in holdings if h["symbol"] in failures and
                      (h.get("exchange") == "BSE" or
                       (h.get("exchange") != "NSE" and (frames.get(h["symbol"]) is None or
                        getattr(frames.get(h["symbol"]), "empty", True))))]
    if bse_candidates:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(3, len(bse_candidates))) as pool:
            pending = [(h, pool.submit(bse_daily_for_doctor, h["symbol"], now=now,
                                      explicit=h.get("exchange") == "BSE"))
                       for h in bse_candidates]
            for holding, future in pending:
                recovered = future.result()
                if recovered is not None:
                    sym = holding["symbol"]
                    frames[sym] = recovered
                    failures.pop(sym, None)
                    price_sources[sym] = "BSE: exact ticker, INR completed daily OHLCV"
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
                        "high20": max(values[-20:]), "low20": min(values[-20:]),
                "return20": round(100 * (close / values[-21] - 1), 2)
                if len(values) >= 21 else None})
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


def build_pdf_bytes(holdings, *, peers=None, risk=None, companies=None):
    """Compatibility entry point; rendering failures contain no holding data."""
    try:
        from portfolio_doctor_report import build_pdf_bytes as render
        return render(holdings, peers=peers, risk=risk, companies=companies)
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
        # Default Doctor: 4 base pages + optional risk page + <=5 verified
        # company dossiers. Advanced mode remains separately bounded to 10.
        max_pages = 10
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


def build_optional_pdf(recs, frames, *, seconds=20, peers=None):
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
        review["peer_watchlist"] = peers
        return render_advanced_pdf(review)


def _safe_peer_watchlist(recs):
    # An unavailable index roster or provider may never prevent a verified
    # holdings-only PDF. The worker already suppresses all sensitive logs.
    try:
        from portfolio_doctor_peers import research_industry_peers
        with optional_review_deadline(25):
            return research_industry_peers(recs)
    except (Exception, OptionalReviewTimeout):
        return None


def _safe_historical_risk(recs, frames):
    """Optional fully-covered history research; never block holdings PDF."""
    try:
        from portfolio_doctor_risk_history import calculate_historical_risk
        with optional_review_deadline(14):
            benchmark = None
            try:
                from market_data import index_history
                benchmark = index_history("^NSEI", "1y", retries=0, timeout=10)
            except Exception:
                pass
            return calculate_historical_risk(recs, frames, benchmark_frame=benchmark)
    except (Exception, OptionalReviewTimeout):
        return None


def _safe_company_dossiers(recs, frames, peers, *, seconds=15):
    """Research cards must never delay or block the verified portfolio PDF."""
    from portfolio_doctor_company_research import prepare_company_pages
    industries = (peers or {}).get("held_industries") or {}
    collected = {}
    started = time.monotonic()
    if seconds >= 2:
        for row in recs:
            if row.get("exchange") == "BSE":
                continue  # Public NSE issuer-page address may not match BSE.
            remaining = seconds - (time.monotonic() - started)
            if remaining < 2:
                break
            try:
                from portfolio_doctor_advanced import collect_fundamentals_no_holdings_cache
                with optional_review_deadline(min(3.0, remaining)):
                    fund = collect_fundamentals_no_holdings_cache(
                        row["symbol"], industries.get(row["symbol"], ""))
                    if fund.get("status") == "PARTIAL":
                        collected[row["symbol"]] = fund
            except (Exception, OptionalReviewTimeout):
                # Missing provider evidence means "not verified", not a guess.
                continue
    try:
        return prepare_company_pages(recs, frames, fundamentals=collected,
                                     industries=industries)
    except Exception:
        return None


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
        if advanced and not any(h.get("exchange") == "BSE" for h in holdings):
            recs, frames = price_holdings(holdings, include_frames=True)
            peers = _safe_peer_watchlist(recs)
            try:
                # Reserve time for the basic PDF before the parent's hard kill.
                budget = min(20, 120 - (time.monotonic() - worker_started))
                if any(row["price_source"].startswith("BSE:") for row in recs):
                    result = build_pdf_bytes(recs, peers=peers)
                else:
                    result = build_optional_pdf(recs, frames, seconds=budget, peers=peers)
            except (Exception, OptionalReviewTimeout):
                # Optional research cannot consume the basic report's budget.
                result = build_pdf_bytes(recs, peers=peers)
        else:
            # Keep the verified in-memory historical frames for the same
            # limited private report. No new holdings database or cache.
            recs, frames = price_holdings(holdings, include_frames=True)
            risk = _safe_historical_risk(recs, frames)
            peers = _safe_peer_watchlist(recs)
            # Reserve space in the hard worker deadline for PDF creation.
            remaining = max(0, min(15, 120 - (time.monotonic() - worker_started)))
            companies = _safe_company_dossiers(recs, frames, peers,
                                                seconds=remaining)
            result = build_pdf_bytes(recs, peers=peers, risk=risk, companies=companies)
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
