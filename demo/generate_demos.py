"""Deterministic demonstrations; synthetic prices, never implied market evidence."""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trading_chart import render_chart


def ohlcv(closes, volumes=None, start="2026-01-01", spread=1.1):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    opens = np.r_[closes[0]-.2, closes[:-1]]
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    return pd.DataFrame({"timestamp":pd.date_range(start, periods=n, freq="B",tz="UTC"),
                         "open":opens,"high":highs,"low":lows,"close":closes,
                         "volume":np.array(volumes if volumes is not None else np.full(n,100_000.0), dtype=float)})


def dataset(kind: str, n=220):
    rng=np.random.default_rng(101 if kind=="uptrend" else 202 if kind=="sideways" else 303)
    t=np.arange(n)
    if kind=="uptrend":
        close=90 + t*.30 + np.sin(t*.25)*4.8 + rng.normal(0,.22,n)
        vols=120000*(1+.12*np.sin(t*.13)); vols[-1]*=2.4
    elif kind=="downtrend":
        close=210 - t*.34 + np.sin(t*.25)*4.8 + rng.normal(0,.22,n)
        vols=120000*(1+.10*np.sin(t*.13)); vols[-1]*=2.5
    elif kind=="sideways":
        close=145+np.sin(t*.37)*.3 + rng.normal(0,1.15,n)
        vols=120000*rng.uniform(.90,1.08,n)
    else:
        raise ValueError(kind)
    return ohlcv(close,vols)


def bull_flag():
    rng=np.random.default_rng(44)
    baseline=120+np.arange(122)*.03+np.sin(np.arange(122)*.33)*.75
    pole=np.linspace(baseline[-1]+.8,baseline[-1]+18,9)
    decline=pole[-1]-np.linspace(.4,4.0,9)
    prices=np.r_[baseline,pole,decline, pole[-1]+4.6]
    v=np.full(len(prices),100_000.);v[-1]=370_000.
    return ohlcv(prices,v,spread=.65)


def generate(out_dir=None):
    out=Path(out_dir) if out_dir is not None else ROOT/"msk_chart_examples"
    out.mkdir(parents=True,exist_ok=True)
    res={}
    for kind in ("uptrend","sideways","downtrend"):
        html,payload=render_chart(dataset(kind))
        (out/f"{kind}.html").write_text(html,encoding="utf-8")
        (out/f"{kind}.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
        res[kind]=payload
        print(kind, payload["trend"], payload["pattern"],payload["adx"], payload["signal"])
    flag_html,flag_data=render_chart(bull_flag())
    (out/"bull_flag.html").write_text(flag_html,encoding="utf-8")
    (out/"bull_flag.json").write_text(json.dumps(flag_data,indent=2),encoding="utf-8")
    res["bull_flag"]=flag_data
    print("bull_flag", flag_data["pattern"],flag_data.get("cleanliness"))
    return res


if __name__=="__main__":
    generate()
