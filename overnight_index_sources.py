"""Keyless end-of-day price fallback for missing NSE symbols.

TejHQ's public OHLCV endpoint serves exchange bhavcopy-derived RAW daily data.
No symbol substitution, market-data entitlement, adjusted-price or "live"
claims. Its public API is not a commercial market-data redistribution license.
"""
from __future__ import annotations

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
            if (min(values[x] for x in ("Open","High","Low","Close"))<=0 or
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
