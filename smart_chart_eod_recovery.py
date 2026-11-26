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
        # A fully or partially observed price candle is never overwritten.
        # Yahoo bulk downloads may fill volume with zero even though all
        # four prices are missing. A positive observed volume must match
        # the final exchange volume exactly before the day is recovered.
        reported_volume = None
        if len(target):
            row = target.iloc[0]
            if not row[["Open","High","Low","Close"]].isna().all():
                return None
            if not pd.isna(row["Volume"]):
                volume = _number(row["Volume"])
                if volume < 0:
                    return None
                reported_volume = volume if volume > 0 else None
        data = data.loc[data.index <= pd.Timestamp(previous)]
        # Valid older bars do not need a calendar lookup. The existing
        # reviewed calendar covers 2026 only; never discard an unknown-year gap.
        empty = data[["Open","High","Low","Close"]].isna().all(axis=1)
        empty &= data["Volume"].isna() | data["Volume"].eq(0)
        drop = []
        for stamp in data.index[empty]:
            try:
                if not doctor_trading_session(stamp.date()):
                    drop.append(stamp)
            except RuntimeError:
                pass
        data = data.drop(index=drop)
        verified = validated_daily_history(data, previous.isoformat())
        if verified is not None:
            verified.attrs["target_placeholder_volume"] = reported_volume
        return verified
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None


def stitch_final_day(primary, previous, expected, prior, latest, *, diagnostics=None):
    """Verify prior OHLCV, exact date and continuity before appending official day."""
    def reject(reason):
        if diagnostics is not None:
            diagnostics[reason] = diagnostics.get(reason, 0) + 1
        return None
    if primary is None or not prior or not latest:
        return reject("missing_official_rows")
    try:
        last = primary.iloc[-1]
        if str(primary.index[-1].date()) != previous.isoformat():
            return reject("wrong_prior_date")
        differences = [k for k in ("Open","High","Low","Close") if not _matches(last[k], prior[k])]
        if differences:
            return reject("prior_price_mismatch_" + "_".join(differences))
        if float(last["Volume"]) != float(prior["Volume"]):
            return reject("prior_volume_mismatch")
        reported_volume = primary.attrs.get("target_placeholder_volume")
        if reported_volume is not None and reported_volume != float(latest["Volume"]):
            return reject("latest_volume_mismatch")
        if not _matches(prior["Close"], latest["PreviousClose"]):
            return reject("previous_close_continuity")
        if abs(latest["Open"]/latest["PreviousClose"] - 1) > .40:
            return reject("possible_corporate_action")
        row = pd.DataFrame([{k:latest[k] for k in REQUIRED}], index=[pd.Timestamp(expected)])
        result = validated_daily_history(pd.concat([primary, row]), expected.isoformat())
        return result if result is not None else reject("merged_history_invalid")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError):
        return reject("invalid_overlap")


class ArchiveRecovery:
    """Lazy two-file fetch for the entire selected universe; no per-stock downloads."""
    def __init__(self, symbols, session, *, fetch=final_archive_rows):
        self.symbols = frozenset(symbols)
        self.expected = date.fromisoformat(str(session))
        self.fetch = fetch
        self.loaded = False
        self.current = self.previous = {}
        self.recovered = 0
        self.rejection_reasons = {}

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
        reasons = {}
        result = stitch_final_day(history, previous, self.expected, self.previous.get(symbol), self.current.get(symbol), diagnostics=reasons)
        if result is not None:
            self.recovered += 1
            print(f"NSE_FINAL_RECOVERY_OK symbol={symbol} session={self.expected} rows={len(result)} close={result.Close.iloc[-1]:.2f}", flush=True)
        else:
            for reason, count in reasons.items():
                self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + count
            print(f"NSE_FINAL_RECOVERY_REJECT symbol={symbol} reasons={reasons}", flush=True)
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

def preliminary_rank(frame, session):
    """Prior-session shortlist only. Never used as a trade signal."""
    from portfolio_doctor_live import previous_doctor_session
    try:
        day = date.fromisoformat(str(session))
        history = prior_history(frame, day, previous_doctor_session(day))
        if history is None:
            return None
        close = history.Close
        turnover = float((close * history.Volume).tail(20).mean())
        if not math.isfinite(turnover) or turnover < 20_000_000:
            return None
        momentum = float(close.iloc[-1] / close.iloc[-21] - 1)
        trend = float(close.iloc[-1] / close.rolling(50).mean().iloc[-1] - 1)
        return momentum + trend
    except (ValueError, TypeError, KeyError, IndexError, RuntimeError, ZeroDivisionError):
        return None


def individual_chart_history(symbol, session, *, fetch, alternate=None, official=None,
                             recovery=None):
    """Fetch just this ticker, never return a preliminary or stale history."""
    if not re.fullmatch(r"[A-Z0-9&._-]{1,25}\.NS", symbol):
        return None
    if recovery is None:
        recovery = ArchiveRecovery([symbol], session)
    for label, provider in (("Yahoo individual", fetch), ("Yahoo chart hosts", alternate),
                            ("NSE historical API", official)):
        if provider is None:
            continue
        try:
            frame = provider(symbol)
            if frame is None:
                continue
            complete = validated_daily_history(frame, session)
            if complete is None and label == "Yahoo individual":
                complete = recovery(symbol, frame)
                if complete is not None:
                    label += " + NSE final archive"
            if complete is not None:
                complete.attrs["history_source"] = getattr(frame, "attrs", {}).get("history_source", label)
                return complete
        except Exception as exc:
            print(f"CHART_INDIVIDUAL_SOURCE symbol={symbol} source={label} error={type(exc).__name__}", flush=True)
    return None
