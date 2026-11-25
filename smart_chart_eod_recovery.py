"""Chart-only bounded public EOD recovery. No DB, Telegram or customer data.

Reuse the final NSE UDiFF archive contract already used by Portfolio Doctor.
Keep the old Doctor parser unchanged. Never substitute an earlier date or
manufacture an OHLCV candle. Yahoo hosts are alternate transports, not
independent price sources.
"""
import csv
import io
import math
import re
import zipfile
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests

from msk_chart_release import REQUIRED, validated_daily_history

UA = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
ARCHIVE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day}_F_0000.csv.zip"
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
MAX_ZIP = 4_000_000
MAX_CSV = 20_000_000


def _response_bytes(response, limit):
    if response.status_code != 200:
        return None
    chunks = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        chunks.extend(chunk)
        if len(chunks) > limit:
            return None
    return bytes(chunks) if chunks else None


def _number(value):
    x = float(str(value).replace(",", "").strip())
    if not math.isfinite(x):
        raise ValueError("nonfinite")
    return x


def _matches(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=.01)


def final_archive_rows(day, wanted, *, get=None):
    """Fetch one final official ZIP; keep only exact requested EQ symbols."""
    wanted = set(wanted)
    if not wanted or len(wanted) > 550:
        return {}
    get = get or requests.get
    try:
        with get(ARCHIVE.format(day=day.strftime("%Y%m%d")), headers=UA,
                 stream=True, timeout=(4, 8), allow_redirects=False) as response:
            print(f"NSE_FINAL_ARCHIVE session={day} status={response.status_code}", flush=True)
            payload = _response_bytes(response, MAX_ZIP)
        if not payload:
            return {}
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            files = [x for x in z.infolist() if not x.is_dir() and x.filename.lower().endswith(".csv")]
            if len(files) != 1 or not 50_000 <= files[0].file_size <= MAX_CSV:
                return {}
            with z.open(files[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                names = {str(x).strip().upper() for x in (reader.fieldnames or [])}
                required = {"TCKRSYMB", "SCTYSRS", "TRADDT", "OPNPRIC", "HGHPRIC",
                            "LWPRIC", "CLSPRIC", "PRVSCLSGPRIC", "TTLTRADGVOL"}
                if not required.issubset(names) or len(names) != len(reader.fieldnames or []):
                    return {}
                rows, bad, seen = {}, set(), set()
                eq_total = 0
                for raw_row in reader:
                    row = {str(k).strip().upper(): str(v or "").strip() for k, v in raw_row.items()}
                    if row.get("SCTYSRS") != "EQ":
                        continue
                    eq_total += 1
                    # The date is checked on every equity record, not just the chosen stock.
                    if row.get("TRADDT") != day.isoformat():
                        return {}
                    sym = row.get("TCKRSYMB", "").upper() + ".NS"
                    if sym not in wanted:
                        continue
                    if sym in seen:
                        bad.add(sym)
                        continue
                    seen.add(sym)
                    try:
                        fields = {key: _number(row[col]) for key, col in
                                  zip((*REQUIRED, "PreviousClose"), ("OPNPRIC", "HGHPRIC", "LWPRIC",
                                      "CLSPRIC", "TTLTRADGVOL", "PRVSCLSGPRIC"))}
                        if (any(fields[k] <= 0 for k in ("Open","High","Low","Close","PreviousClose"))
                                or fields["Volume"] < 0 or fields["High"] < max(fields["Open"],fields["Close"],fields["Low"])
                                or fields["Low"] > min(fields["Open"],fields["Close"],fields["High"])):
                            bad.add(sym)
                            continue
                        rows[sym] = fields
                    except (ValueError, KeyError, TypeError, OverflowError):
                        bad.add(sym)
                if eq_total < 400:
                    return {}
                return {sym: row for sym, row in rows.items() if sym not in bad}
    except (requests.RequestException, OSError, ValueError, TypeError, KeyError,
            zipfile.BadZipFile, RuntimeError, csv.Error, UnicodeError) as exc:
        print(f"NSE_FINAL_ARCHIVE session={day} unavailable={type(exc).__name__}", flush=True)
        return {}


def prior_history(frame, expected, previous):
    """Remove only the unfilled target-date placeholder and verified holiday slots."""
    from portfolio_doctor_live import doctor_trading_session
    try:
        if frame is None or frame.empty or not frame.columns.is_unique:
            return None
        data = frame.loc[:, list(REQUIRED)].copy()
        idx = pd.DatetimeIndex(pd.to_datetime(data.index, errors="raise"))
        if idx.hasnans:
            return None
        if idx.tz is not None:
            idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
        data.index = idx.normalize()
        data = data.loc[data.index <= pd.Timestamp(expected)].sort_index()
        if not data.index.is_unique:
            return None
        target = data.loc[data.index == pd.Timestamp(expected)]
        # A fully populated candle is never overwritten, even if rejected.
        if len(target) and not target.iloc[0].isna().all():
            return None
        data = data.loc[data.index <= pd.Timestamp(previous)]
        empty = data.isna().all(axis=1)
        holidays = [not doctor_trading_session(d.date()) for d in data.index]
        data = data.loc[~(empty & holidays)]
        return validated_daily_history(data, previous.isoformat())
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None


def stitch_final_day(primary, previous, expected, prior, latest):
    """Verify prior OHLCV, exact date and continuity before appending official day."""
    if primary is None or not prior or not latest:
        return None
    try:
        last = primary.iloc[-1]
        if str(primary.index[-1].date()) != previous.isoformat():
            return None
        if any(not _matches(last[k], prior[k]) for k in ("Open","High","Low","Close")):
            return None
        if float(last["Volume"]) != float(prior["Volume"]):
            return None
        if not _matches(prior["Close"], latest["PreviousClose"]):
            return None
        if abs(latest["Open"]/latest["PreviousClose"] - 1) > .40:
            return None
        row = pd.DataFrame([{k:latest[k] for k in REQUIRED}], index=[pd.Timestamp(expected)])
        return validated_daily_history(pd.concat([primary, row]), expected.isoformat())
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError):
        return None


class ArchiveRecovery:
    """Lazy two-file fetch for the entire selected universe; no per-stock downloads."""
    def __init__(self, symbols, session, *, fetch=final_archive_rows):
        self.symbols = frozenset(symbols)
        self.expected = date.fromisoformat(str(session))
        self.fetch = fetch
        self.loaded = False
        self.current = self.previous = {}
        self.recovered = 0

    def __call__(self, symbol, frame):
        from portfolio_doctor_live import previous_doctor_session, doctor_trading_session
        if symbol not in self.symbols or not doctor_trading_session(self.expected):
            return None
        previous = previous_doctor_session(self.expected)
        history = prior_history(frame, self.expected, previous)
        if history is None:
            return None
        if not self.loaded:
            self.loaded = True
            self.current = self.fetch(self.expected, self.symbols)
            if self.current:
                self.previous = self.fetch(previous, self.symbols)
            print(f"NSE_FINAL_ARCHIVE_COVERAGE latest={len(self.current)} prior={len(self.previous)}", flush=True)
        result = stitch_final_day(history, previous, self.expected, self.previous.get(symbol), self.current.get(symbol))
        if result is not None:
            self.recovered += 1
        return result


def yahoo_host_history(symbol, session, *, get=None, recover=None):
    """Bounded query1/query2 daily JSON recovery; never convert intraday to EOD."""
    if not re.fullmatch(r"[A-Z0-9&._-]{1,25}\.NS", symbol):
        return None
    get = get or requests.get
    day = pd.Timestamp(str(session), tz="Asia/Kolkata")
    params = {"period1":int((day-pd.Timedelta(days=370)).timestamp()),
              "period2":int((day+pd.Timedelta(days=1)).timestamp()),
              "interval":"1d","includePrePost":"false"}
    for host in YAHOO_HOSTS:
        try:
            with get(f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}",
                     params=params, headers=UA, stream=True, timeout=(3,5),
                     allow_redirects=False) as response:
                payload = _response_bytes(response, 2_000_000)
                print(f"YAHOO_CHART host={host} symbol={symbol} status={response.status_code}", flush=True)
            if not payload:
                continue
            import json
            result = json.loads(payload)["chart"]
            if result.get("error") or len(result.get("result") or []) != 1:
                continue
            item = result["result"][0]
            meta = item["meta"]
            if (meta.get("symbol") != symbol or meta.get("currency") != "INR"
                    or meta.get("exchangeTimezoneName") not in ("Asia/Kolkata","Asia/Calcutta")
                    or meta.get("dataGranularity") != "1d"):
                continue
            bars = item["indicators"]["quote"]
            if len(bars) != 1 or len(item["timestamp"]) > 400:
                continue
            idx = pd.to_datetime(item["timestamp"], unit="s", utc=True).tz_convert("Asia/Kolkata")
            frame = pd.DataFrame({k:bars[0][k.lower()] for k in REQUIRED},index=idx)
            complete = validated_daily_history(frame, session)
            if complete is not None:
                complete.attrs["history_source"] = host
                return complete
            if recover:
                repaired = recover(symbol, frame)
                if repaired is not None:
                    repaired.attrs["history_source"] = host + " + NSE final archive"
                    return repaired
        except (requests.RequestException, ValueError, TypeError, KeyError,
                IndexError, AttributeError, OverflowError, OSError) as exc:
            print(f"YAHOO_CHART host={host} symbol={symbol} unavailable={type(exc).__name__}", flush=True)
    return None
