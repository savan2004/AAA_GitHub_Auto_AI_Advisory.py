"""Internal public-only handoff between sequential scan and render phases.

Use an anonymous temporary file inherited across exec, not pickle, shared
cache, environment-held history or an additional resident child process.
The renderer revalidates the exact selected raw OHLCV and current session.
"""
import json
import math
import os
import re
import stat
import sys
import tempfile

from msk_chart_release import REQUIRED, validated_daily_history

FLAG = "--msk-render-fd"
MAX_BYTES = 100_000
SETUPS = frozenset((
    "Breakout confirmed: 20-session high + volume",
    "Trend change: bullish EMA cross + weekly alignment",
    "Trend confirmation: higher highs/lows + weekly trend",
))


def encode_handoff(winner, session):
    frame = validated_daily_history(winner.get("_daily_history"), session)
    if frame is None or not 55 <= len(frame) <= 400:
        raise ValueError("Invalid selected history")
    payload = dict(session=str(session), sym=winner["sym"], name=winner["name"],
                   score=int(winner["score"]), close=float(winner["close"]),
                   reason=winner["reason"], scope_note=winner["scope_note"],
                   setup_confirmed=winner.get("setup_confirmed") is True,
                   verified_trade_plan=winner.get("verified_trade_plan") is True,
                   bars=[[stamp.date().isoformat(), *map(float,row)]
                         for stamp,row in zip(frame.index, frame.loc[:,list(REQUIRED)].to_numpy())])
    raw = json.dumps(payload, allow_nan=False, separators=(",",":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Render handoff exceeds size limit")
    return raw


def decode_handoff(raw, session):
    import pandas as pd
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Invalid render handoff size")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("session") != str(session):
        raise ValueError("Stale render handoff")
    sym, name = payload.get("sym"), payload.get("name")
    score, reason = payload.get("score"), payload.get("reason")
    if (not isinstance(sym,str) or not re.fullmatch(r"[A-Z0-9&._-]{1,25}\.NS",sym)
            or not isinstance(name,str) or not 2 <= len(name) <= 100
            or any(ch in name for ch in "|\n\r")
            or type(score) is not int or not 12 <= score <= 20
            or not isinstance(reason,str) or reason not in SETUPS
            or payload.get("setup_confirmed") is not True
            or type(payload.get("verified_trade_plan")) is not bool):
        raise ValueError("Invalid selected chart metadata")
    match = re.fullmatch(
        r"Sample: (\d+)/(\d+) qualifying NSE setups from (\d+); "
        r"(full-universe scan|partial-universe scan); Setup: (.+); EOD (\d{4}-\d{2}-\d{2})",
        payload.get("scope_note",""))
    if not match:
        raise ValueError("Invalid scan coverage metadata")
    valid,sampled,whole = map(int,match.groups()[:3])
    if (not 0 < valid <= sampled <= whole <= 100
            or (match.group(4) == "full-universe scan" and sampled != whole)
            or match.group(5) != reason or match.group(6) != str(session)):
        raise ValueError("Invalid scan coverage or session")
    bars = payload.get("bars")
    if not isinstance(bars,list) or not 55 <= len(bars) <= 400:
        raise ValueError("Invalid candle count")
    frame = pd.DataFrame(bars,columns=["Date",*REQUIRED])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date"),errors="raise"))
    if (not frame.index.is_unique or not frame.index.is_monotonic_increasing
            or frame.index[-1] != pd.Timestamp(session)):
        raise ValueError("Unordered or misdated render history")
    checked = validated_daily_history(frame,str(session))
    close = float(payload["close"])
    if (checked is None or len(checked) != len(frame) or not math.isfinite(close)
            or close != float(checked.Close.iloc[-1])):
        raise ValueError("Invalid or changed selected history")
    return dict(sym=sym,name=name,score=score,close=close,reason=reason,
                scope_note=payload["scope_note"],setup_confirmed=True,
                verified_trade_plan=payload["verified_trade_plan"],_daily_history=checked)


def replace_with_renderer(script,winner,session):
    """Linux/POSIX exec releases all provider memory before rendering."""
    raw = encode_handoff(winner,session)
    with tempfile.TemporaryFile(mode="w+b") as stream:
        stream.write(raw)
        stream.flush()
        stream.seek(0)
        fd = stream.fileno()
        os.set_inheritable(fd,True)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable,[sys.executable,"-u",script,FLAG,str(fd)])


def read_inherited_handoff(fd,session):
    if not isinstance(fd,int) or fd < 3:
        raise ValueError("Invalid internal render descriptor")
    with os.fdopen(fd,"rb",closefd=True) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError("Invalid internal render file")
        return decode_handoff(stream.read(MAX_BYTES+1),session)
