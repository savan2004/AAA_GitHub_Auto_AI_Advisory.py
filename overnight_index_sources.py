"""Keyless end-of-day price fallback for missing NSE symbols.

TejHQ's public OHLCV endpoint serves exchange bhavcopy-derived RAW daily data.
No symbol substitution, market-data entitlement, adjusted-price or "live"
claims. Its public API is not a commercial market-data redistribution license.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import pandas as pd
import requests

ENDPOINT="https://api.tejhq.dev/v1/ohlcv/nse/"
SAFE_SYMBOL=re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,29}$")
HEADERS={"Accept":"application/json",
         "User-Agent":"MSKIndexOvernightResearch/1.0 (historical EOD)"}


def fetch_tej_history(symbol:str, session:date, *, http=None):
    """Return valid pandas OHLCV or None, never surrogate/other ticker.

    This is a secondary source, used only when scheduled collection requests
    it after TejHQ publishes completed evening bhavcopy. If unavailable the
    main evidence ledger records missing/stale history, not an invented price.
    """
    if not isinstance(session,date):
        session=date.fromisoformat(str(session))
    symbol=str(symbol).strip().upper()
    if not SAFE_SYMBOL.fullmatch(symbol):
        return None
    client=http or requests
    try:
        start=session-timedelta(days=260)
        response=client.get(ENDPOINT+symbol,
            params={"from":start.isoformat(),"to":session.isoformat()},
            headers=HEADERS,timeout=(5,18))
        if response.status_code!=200:
            return None
        payload=response.json()
        meta=payload.get("meta") or {}
        if (str(meta.get("symbol","")).upper()!=symbol or
            str(meta.get("exchange","")).upper()!="NSE"):
            return None
        data=payload.get("data")
        if not isinstance(data,list) or len(data)<80 or len(data)>280:
            return None
        rows=[]
        for entry in data:
            if not isinstance(entry,dict):return None
            stamp=date.fromisoformat(str(entry["date"]))
            if stamp>session:return None
            values={k:float(entry[k.lower()]) for k in ("Open","High","Low","Close","Volume")}
            if (not all(math.isfinite(v) for v in values.values()) or
                min(values[x] for x in ("Open","High","Low","Close"))<=0 or
                values["Volume"]<0 or
                values["High"]<max(values["Open"],values["Close"],values["Low"]) or
                values["Low"]>min(values["Open"],values["Close"],values["High"])):
                return None
            rows.append((stamp,values))
        if len({stamp for stamp,_ in rows})!=len(rows):
            return None
        rows.sort(key=lambda x:x[0])
        if rows[-1][0]!=session:
            return None
        df=pd.DataFrame([values for _,values in rows],
                        index=pd.to_datetime([d for d,_ in rows]))
        df.index.name="Date"
        return df
    except (ValueError,KeyError,TypeError,requests.RequestException,OverflowError):
        return None


def collect_completed_index_histories(symbols, session, *, batch_size=20,
                                      max_seconds=240, download=None):
    """Same raw, bounded batch and NSE recovery contract as Smart Chart.

    Returns only exact-session valid histories, independent of trading scores.
    Each profile shares one archive cache; try alternate Yahoo hosts only
    for remaining symbols. No Telegram imports, writes or invented candles.
    """
    import time
    from smart_chart_fast_scan import _ticker_frame
    from smart_chart_eod_recovery import ArchiveRecovery, yahoo_host_history
    from msk_chart_release import validated_daily_history
    if download is None:
        import yfinance as yf
        download = yf.download
    session = str(session)
    symbols = tuple(dict.fromkeys(symbols))
    if not symbols:
        return {}
    tickers = [sym+".NS" for sym in symbols]
    recovery = ArchiveRecovery(tickers, session)
    completed = {}
    started = time.monotonic()
    size = max(1,min(20,int(batch_size)))

    def accept(symbol, frame, source):
        good = validated_daily_history(frame, session)
        if good is None:
            return False
        good.attrs["history_source"] = source
        completed[symbol] = good
        return True

    # Match the real 100/100 Smart Chart route: retain incomplete raw
    # provider rows until official recovery has inspected them.
    for offset in range(0,len(symbols),size):
        if time.monotonic()-started >= max_seconds:
            break
        batch = tickers[offset:offset+size]
        try:
            raw = download(tickers=batch, period="1y", interval="1d",
                           auto_adjust=False, group_by="ticker",
                           progress=False, threads=4, timeout=9)
        except Exception as exc:
            print(f"TOP5_BULK_UNAVAILABLE type={type(exc).__name__}",flush=True)
            raw = None
        for ticker in batch:
            symbol = ticker[:-3]
            try:
                frame = _ticker_frame(raw,ticker,len(batch))
                if accept(symbol,frame,"Yahoo raw OHLCV"):
                    continue
                if frame is not None:
                    repaired = recovery(ticker,frame)
                    accept(symbol,repaired,"Yahoo raw OHLCV + NSE final archive")
            except Exception as exc:
                print(f"TOP5_BATCH_RECOVERY_UNAVAILABLE symbol={symbol} type={type(exc).__name__}",flush=True)
        print(f"TOP5_HISTORY_BATCH attempted={min(offset+size,len(symbols))}/{len(symbols)} "
              f"verified={len(completed)}",flush=True)
    # Direct Yahoo v8 uses both hosts and the same official archive cache.
    # Never repeat successful histories and stop starting requests at deadline.
    for symbol in symbols:
        if symbol in completed:
            continue
        if time.monotonic()-started >= max_seconds:
            break
        try:
            frame = yahoo_host_history(symbol+".NS",session,recover=recovery)
            if frame is not None:
                accept(symbol,frame,frame.attrs.get("history_source","Yahoo chart hosts"))
        except Exception as exc:
            print(f"TOP5_ALTERNATE_UNAVAILABLE symbol={symbol} type={type(exc).__name__}",flush=True)
    print(f"TOP5_HISTORY_COVERAGE verified={len(completed)}/{len(symbols)} "
          f"missing={','.join(sym for sym in symbols if sym not in completed)}",flush=True)
    return completed
