"""Offline fixtures (no network). TRIDENT-like numbers mirror the sample report; STRONG is a healthy compounder."""
import copy
from datetime import date
import numpy as np
import pandas as pd

TODAY = date(2026, 10, 16)


def prices(seed, start, drift, vol=0.016, n=252, end=TODAY, volume=3_000_000):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    r = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(r))
    op = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, n))
    hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol_ = (volume * (1 + rng.normal(0, 0.25, n))).clip(volume * 0.2).astype(int)
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol_}, index=idx)


def _yrs(rows):
    keys = ("revenue", "op_profit", "opm", "net_profit", "eps", "ocf", "borrowings", "networth", "roce", "total_assets", "deposits")
    return [dict(zip(("label",) + keys, r + (None,) * (len(keys) + 1 - len(r)))) for r in rows]


TRIDENT_YEARS = _yrs([
    ("Mar 2021", 4531, 826, 18.0, 304, 0.60, 505, 1562, 3329, 10.0),
    ("Mar 2022", 6998, 1520, 22.0, 834, 1.64, 903, 1597, 3845, 23.0),
    ("Mar 2023", 6332, 947, 15.0, 442, 0.86, 1426, 1442, 4193, 12.0),
    ("Mar 2024", 6809, 940, 14.0, 350, 0.69, 287, 2126, 4316, 10.0),
    ("Mar 2025", 6987, 896, 13.0, 371, 0.73, 945, 1635, 4621, 9.0),
    ("Mar 2026", 6701, 869, 13.0, 377, 0.74, 760, 1829, 4772, 10.0),
])
TRIDENT_QTRS = [dict(zip(("label", "revenue", "op_profit", "opm", "net_profit", "eps"), q)) for q in [
    ("Mar 2025", 1864, 245, 13.0, 133, 0.26), ("Jun 2025", 1707, 292, 17.0, 140, 0.27),
    ("Sep 2025", 1787, 214, 12.0, 91, 0.18), ("Dec 2025", 1574, 136, 9.0, 44, 0.09),
    ("Mar 2026", 1633, 227, 14.0, 102, 0.20), ("Jun 2026", 1787, 300, 17.0, 158, 0.31)]]
SH = [dict(label=l, promoters=p, fii=f, dii=d, public=round(100 - p - f - d, 1)) for l, p, f, d in [
    ("Sep 2024", 73.2, 2.6, 0.1), ("Dec 2024", 73.2, 2.8, 0.1), ("Mar 2025", 73.7, 3.0, 0.1), ("Jun 2025", 73.7, 3.0, 0.1),
    ("Sep 2025", 73.7, 3.2, 0.1), ("Dec 2025", 73.7, 3.2, 0.2), ("Mar 2026", 73.7, 3.2, 0.2), ("Jun 2026", 73.7, 3.2, 0.2)]]


def _stm(years, qtrs):
    import research_data as rd
    return {"source": "Screener.in", "sources": {"years": "Screener.in", "quarters": "Screener.in"}, "unit": "₹ Cr",
            "years": copy.deepcopy(years), "quarters": copy.deepcopy(qtrs), "layout": "corporate",
            "freshness": rd.freshness(copy.deepcopy(qtrs), TODAY)}


def trident(**over):
    df = prices(11, 27.5, -0.0011, 0.019)
    inp = dict(sym="TRIDENT", name="Trident Limited", df=df, nifty=prices(5, 24000, 0.0001, 0.008, volume=0),
               fund={"name": "Trident Limited", "pe": 28.7, "roe": 7.9, "pb": 2.38, "mcap": 11344e7, "div_y": 2.25, "eps": None},
               info={}, stm=_stm(TRIDENT_YEARS, TRIDENT_QTRS),
               extras={"growth": {"Compounded Sales Growth": {"3 Years": 2.0, "5 Years": 8.0},
                                  "Compounded Profit Growth": {"3 Years": -6.0, "5 Years": 3.0}}, "shareholding": copy.deepcopy(SH)},
               consensus=None, news=[], sector="🧵 Textiles", industry={"kind": None, "is_lender": False, "is_financial": False, "reasons": []},
               peers=[], peer_label=None, peer_method="none")
    inp.update(over)
    return inp


STRONG_YEARS = _yrs([
    ("Mar 2021", 4000, 880, 22.0, 600, 12.0, 640, 300, 3200, 24.0), ("Mar 2022", 4700, 1050, 22.3, 720, 14.4, 760, 280, 3800, 25.0),
    ("Mar 2023", 5500, 1250, 22.7, 860, 17.2, 900, 260, 4500, 25.5), ("Mar 2024", 6400, 1470, 23.0, 1010, 20.2, 1050, 240, 5400, 26.0),
    ("Mar 2025", 7400, 1700, 23.0, 1170, 23.4, 1230, 220, 6400, 26.0), ("Mar 2026", 8500, 1980, 23.3, 1360, 27.2, 1420, 200, 7600, 26.5)])
STRONG_QTRS = [dict(zip(("label", "revenue", "op_profit", "opm", "net_profit", "eps"), q)) for q in [
    ("Mar 2025", 1900, 440, 23.2, 300, 6.0), ("Jun 2025", 2000, 460, 23.0, 320, 6.4), ("Sep 2025", 2080, 485, 23.3, 335, 6.7),
    ("Dec 2025", 2150, 500, 23.3, 350, 7.0), ("Mar 2026", 2270, 535, 23.6, 365, 7.3), ("Jun 2026", 2350, 550, 23.4, 380, 7.6)]]


def strong(sym="STRONGCO", name="Strong Compounders Limited", **over):
    df = prices(3, 440, 0.0009, 0.012, volume=900_000)
    inp = dict(sym=sym, name=name, df=df, nifty=prices(5, 24000, 0.0003, 0.007, volume=0),
               fund={"name": name, "pe": 24.0, "roe": 21.0, "pb": 5.0, "mcap": 52000e7, "div_y": 1.2, "eps": 28.5, "de": 0.03},
               info={}, stm=_stm(STRONG_YEARS, STRONG_QTRS),
               extras={"growth": {"Compounded Sales Growth": {"3 Years": 16.0, "5 Years": 16.0},
                                  "Compounded Profit Growth": {"3 Years": 17.0, "5 Years": 17.0}}, "shareholding": copy.deepcopy(SH)},
               consensus=None, news=[], sector="🏭 Industrials", industry={"kind": None, "is_lender": False, "is_financial": False, "reasons": []},
               peers=[], peer_label=None, peer_method="none")
    inp.update(over)
    return inp
