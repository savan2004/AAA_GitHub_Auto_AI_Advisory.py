"""
fundamental_pipeline.py — one validated equity-research post per day (replaces the basic "Fundamental Gem").

Flow (all bounded, no AI call, no new service):
  1. universe  : reuse universe.get_universe() ONCE, persist a snapshot, clean it (equity only, one listing per company).
  2. stage 1   : rank candidates from a persisted compact fundamentals cache (no per-click network scan).
                 A small, bounded number of stale/missing symbols is warmed each run.
  3. stage 2   : deeply refresh + validate only the top-K shortlist (reuses report_generator collect/assemble/build_pdf).
  4. gate      : validate_report() must pass before anything is handed to the Telegram sender.
  5. ledger    : SQLite publication ledger -> one post per channel/day, no concurrent duplicates, no blind retries.
"""
import os
import re
import json
import time
import sqlite3
import hashlib
import logging
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
POST_TYPE = "fundamental_research"

DATA_DIR = os.getenv("FUND_DATA_DIR", "data")
UNIVERSE_FILE = os.path.join(DATA_DIR, "fund_universe.json")
ISIN_MAP_FILE = os.path.join(DATA_DIR, "isin_map.json")          # optional {SYMBOL: ISIN}
SCREEN_FILE = os.path.join(DATA_DIR, "fund_screen_cache.json")
LEDGER_DB = os.getenv("FUND_LEDGER_DB", os.path.join(DATA_DIR, "publication_ledger.db"))

SHORTLIST_SIZE = int(os.getenv("FUND_SHORTLIST", "5"))            # deep-validated candidates per run (max)
WARM_BATCH = int(os.getenv("FUND_WARM_BATCH", "30"))              # symbols refreshed into the screen cache per run
WARM_BUDGET_S = int(os.getenv("FUND_WARM_BUDGET_S", "150"))
UNIVERSE_MAX_AGE_D = 7
SCREEN_MAX_AGE_H = 24 * 7
RECENT_DAYS = 30                                                  # do not re-post the same company within N days
CLAIM_LEASE_S = 30 * 60                                           # a crashed worker's claim expires after this
MAX_SEND_ATTEMPTS = 2                                             # explicit, small retry bound
CAPTION_LIMIT = 1024                                              # Telegram document-caption limit

POLICY = {
    "max_price_age_days": 5,       # last completed session must be this recent (covers weekends/holidays)
    "max_annual_age_days": 800,    # latest annual period must be within ~2 fiscal years
    "min_fund_avg": float(os.getenv("FUND_MIN_FUND_AVG", "55")),   # Quality/Valuation/Growth average (same engine)
    "min_turnover_cr": 3.0,
    "min_pdf_bytes": 15_000,
}

# ══════════════════════════════════════════════════════════════════════════
# 1. UNIVERSE
# ══════════════════════════════════════════════════════════════════════════
_NON_EQUITY_SUFFIX = ("BEES", "ETF", "IETF")
_NON_EQUITY_PREFIX = ("NIFTY", "SENSEX", "BANKNIFTY", "LIQUID", "GILT")
_NON_EQUITY_SERIES = re.compile(r"-(RE|PP|P\d|W\d|X\d)$")          # rights / partly-paid / preference-type suffixes


def canonical_symbol(s) -> str:
    s = str(s or "").upper().strip()
    for suf in (".NS", ".BO", "-EQ"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


def is_equity_symbol(sym: str) -> bool:
    s = canonical_symbol(sym)
    if not s or s.endswith(_NON_EQUITY_SUFFIX) or s.startswith(_NON_EQUITY_PREFIX) or _NON_EQUITY_SERIES.search(s):
        return False
    return True


def clean_universe(symbols: List[str], isin_map: Optional[Dict[str, str]] = None) -> Tuple[List[str], dict]:
    """Equity-only, one canonical listing per company (ISIN where known, else symbol). Order (liquidity rank) is kept."""
    isin_map = {canonical_symbol(k): v for k, v in (isin_map or {}).items()}
    seen, out, excl, dup = set(), [], 0, 0
    for raw in symbols:
        s = canonical_symbol(raw)
        if not is_equity_symbol(s):
            excl += 1
            continue
        key = isin_map.get(s) or s
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        out.append(s)
    return out, {"excluded_non_equity": excl, "duplicates_removed": dup,
                 "isin_known": sum(1 for s in out if s in isin_map)}


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def load_universe(now: Optional[datetime] = None, fetch=None, force: bool = False) -> dict:
    """Persisted snapshot first (no constituent fetch per click); rebuilt at most every UNIVERSE_MAX_AGE_D days."""
    now = now or datetime.now(IST)
    snap = _read_json(UNIVERSE_FILE, None)
    if snap and not force:
        try:
            if (now - datetime.fromisoformat(snap["built_at"])).days < UNIVERSE_MAX_AGE_D and snap.get("symbols"):
                return snap
        except Exception:
            pass
    if fetch is None:
        from universe import get_universe as fetch
    raw = fetch() or {}
    syms, stats = clean_universe(raw.get("symbols") or [], _read_json(ISIN_MAP_FILE, {}))
    if not syms and snap and snap.get("symbols"):
        return snap                                              # keep the last good snapshot rather than an empty one
    snap = {"symbols": syms, "count": len(syms), "built_at": now.isoformat(), "source": raw.get("source"),
            "degraded": bool(raw.get("degraded")),
            "basis": "Most-traded NSE EQ-series stocks (liquidity rank). NSE canonical listing only; "
                     "this is a screening universe, not an official index.", **stats}
    _write_json(UNIVERSE_FILE, snap)
    return snap


# ══════════════════════════════════════════════════════════════════════════
# 2. STAGE-1 SCREEN (cached data only, sector-aware, missing != 0)
# ══════════════════════════════════════════════════════════════════════════
def _lin(x, lo, hi):
    """0..100 linear scale; None stays None (a missing value is never scored as zero)."""
    if x is None:
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100.0))


def n_(v, nd=1):
    return "n/a" if v is None else f"{v:.{nd}f}%"


def _avg_(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def screen_metrics(m: dict) -> dict:
    """
    m: compact metrics (see compact_metrics). Returns {ok, score, reasons, fails, missing_mandatory}.
    Mandatory fields decide eligibility; optional fields (fwd P/E, peers, ...) never reject a stock by being absent.
    """
    lender, fin = bool(m.get("lender")), bool(m.get("fin"))
    missing = [k for k in ("mcap", "pe", "roe") if m.get(k) is None]
    if lender and m.get("pb") is None:
        missing.append("pb")
    if (m.get("n_years") or 0) < 4:
        missing.append("annual statements (4+ periods)")
    if m.get("fresh") in ("STALE", "MISSING"):
        missing.append(f"current quarterly results ({str(m.get('fresh')).lower()})")
    fails = []
    if missing:
        return {"ok": False, "score": 0, "reasons": [], "fails": [], "missing_mandatory": missing}

    roe, roce = m["roe"], m.get("roce")
    if lender:
        if roe < 11:
            fails.append(f"ROE {roe:.1f}% below 11% (lender)")
        if m.get("book_growth") is not None and m["book_growth"] < 8:
            fails.append(f"balance-sheet growth {m['book_growth']:.0f}% below 8%")
        if m.get("lev") is not None and m.get("lender_kind") != "Bank" and m["lev"] > 8:
            fails.append(f"leverage {m['lev']:.1f}x above 8x")
    else:
        if roe < 12 and not (roce is not None and roce >= 14):
            fails.append(f"ROE {roe:.1f}% / ROCE {n_(roce)} below quality bar")
        if not fin and m.get("de") is not None and m["de"] > 1.0:
            fails.append(f"debt/equity {m['de']:.2f} above 1.0")
        if not fin and m.get("ocf_pat") is not None and m["ocf_pat"] < 0.6:
            fails.append(f"cash conversion {m['ocf_pat']:.2f}x below 0.6x")
    if m.get("pat_cagr3") is not None and m["pat_cagr3"] < 5:
        fails.append(f"3Y profit CAGR {m['pat_cagr3']:.0f}% below 5%")
    if m.get("down_years") is not None and m["down_years"] > 2:
        fails.append(f"{m['down_years']} profit-decline years in last 5")
    if m["pe"] <= 0 or m["pe"] > 80:
        fails.append(f"P/E {m['pe']:.0f} outside 0-80")

    comps = {  # weight, value
        "quality": (30, _lin(roe, 8, 25)),
        "growth": (25, _avg_([_lin(m.get("pat_cagr3"), 0, 25), _lin(m.get("rev_cagr3"), 0, 20)])),
        "balance": (20, _avg_([_lin(-(m["de"]), -1.0, 0.0) if (not fin and m.get("de") is not None) else None,
                               _lin(m.get("ocf_pat"), 0.5, 1.5) if (not fin and m.get("ocf_pat") is not None) else None,
                               _lin(m.get("book_growth"), 8, 25) if lender else None])),
        "valuation": (15, _lin(-(m["pe"]), -50, -12) if m["pe"] > 0 else None),
        "consistency": (10, _lin(-(m["down_years"]), -3, 0) if m.get("down_years") is not None else None),
    }
    avail = {k: v for k, v in comps.items() if v[1] is not None}
    if len(avail) < 3:
        return {"ok": False, "score": 0, "reasons": [], "fails": [], "missing_mandatory": ["too few scorable factors"]}
    wsum = sum(w for w, _ in avail.values())
    score = round(sum(w * v for w, v in avail.values()) / wsum, 1)
    reasons = [f"ROE {roe:.1f}%"]
    if roce is not None and not lender:
        reasons.append(f"ROCE {roce:.1f}%")
    if m.get("pat_cagr3") is not None:
        reasons.append(f"3Y profit CAGR {m['pat_cagr3']:.0f}%")
    if lender and m.get("book_growth") is not None:
        reasons.append(f"balance-sheet growth {m['book_growth']:.0f}%")
    elif not fin and m.get("de") is not None:
        reasons.append(f"debt/equity {m['de']:.2f}")
    if not fin and m.get("ocf_pat") is not None:
        reasons.append(f"cash conversion {m['ocf_pat']:.2f}x")
    return {"ok": not fails, "score": score, "reasons": reasons, "fails": fails, "missing_mandatory": []}


def compact_metrics(fv: dict, stm: Optional[dict]) -> dict:
    """Reduce engine fundamentals to the small record persisted in the screen cache."""
    return {"mcap": fv.get("mcap"), "pe": fv.get("pe"), "pb": fv.get("pb"), "roe": fv.get("roe"), "roce": fv.get("roce"),
            "de": fv.get("de"), "ocf_pat": fv.get("ocf_pat"), "rev_cagr3": fv.get("rev_cagr3"), "pat_cagr3": fv.get("pat_cagr3"),
            "down_years": fv.get("down_years"), "book_growth": fv.get("book_growth"), "lev": fv.get("leverage"),
            "lender": fv.get("lender"), "lender_kind": fv.get("lender_kind"), "fin": fv.get("fin"),
            "n_years": len(((stm or {}).get("years")) or []), "fresh": (fv.get("freshness") or {}).get("status")}


def rank_candidates(universe: List[str], cache: dict, now: Optional[datetime] = None, exclude=()) -> List[dict]:
    """Stage 1: purely from the persisted cache. Best score first; ties keep liquidity order."""
    now = now or datetime.now(IST)
    out = []
    for pos, sym in enumerate(universe):
        rec = cache.get(sym)
        if not rec or sym in exclude:
            continue
        if now.timestamp() - rec.get("ts", 0) > SCREEN_MAX_AGE_H * 3600:
            continue
        r = screen_metrics(rec["m"])
        if r["ok"]:
            out.append({"sym": sym, "score": r["score"], "reasons": r["reasons"], "liq_rank": pos + 1, "m": rec["m"]})
    out.sort(key=lambda x: (-x["score"], x["liq_rank"]))
    return out


def warm_screen_cache(universe: List[str], cache: dict, n: int, fetch_one, now: Optional[datetime] = None,
                      budget_s: int = WARM_BUDGET_S) -> int:
    """Refresh at most `n` missing/stale symbols (liquidity order). fetch_one(sym) -> compact metrics dict or None."""
    now = now or datetime.now(IST)
    cutoff = now.timestamp() - SCREEN_MAX_AGE_H * 3600
    todo = [s for s in universe if (cache.get(s) or {}).get("ts", 0) < cutoff][:max(0, n)]
    t0, done = time.time(), 0
    for s in todo:
        if time.time() - t0 > budget_s:
            break
        try:
            m = fetch_one(s)
        except Exception as e:
            logger.info(f"[fund] warm {s} failed: {str(e)[:100]}")
            m = None
        if m:
            cache[s] = {"ts": now.timestamp(), "m": m}
            done += 1
    return done


# ══════════════════════════════════════════════════════════════════════════
# 3. PRE-PUBLICATION VALIDATION GATE
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class GateResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _expected_stance(comp: int, trend: Optional[float]) -> str:
    if trend is not None and trend < 35 and comp >= 48:
        return "QUALITY WATCHLIST / WAIT"
    return ("HIGH-CONVICTION CONSTRUCTIVE" if comp >= 75 else "CONSTRUCTIVE" if comp >= 62 else
            "NEUTRAL / WAIT" if comp >= 48 else "CAUTIOUS" if comp >= 35 else "NEGATIVE")


def _pdf_info(path: str) -> Tuple[Optional[int], Optional[str]]:
    """(pages, text) using pypdf or poppler when available; (None, None) otherwise."""
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return len(r.pages), "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        pass
    try:
        txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=30).stdout
        pages = txt.count("\f") or None
        return pages, txt
    except Exception:
        return None, None


def validate_report(expected: dict, inp: dict, R: dict, pdf_path: Optional[str] = None, caption: Optional[str] = None,
                    now: Optional[datetime] = None, policy: Optional[dict] = None) -> GateResult:
    """Every check from the brief; any error blocks publication."""
    import research_engine as eng
    pol = {**POLICY, **(policy or {})}
    now = now or datetime.now(IST)
    g = GateResult()
    err, warn = g.errors.append, g.warnings.append
    sym = canonical_symbol(expected.get("symbol"))

    # identity end to end
    if canonical_symbol(inp.get("sym")) != sym:
        err(f"fetched-data symbol {inp.get('sym')!r} != selected {sym!r}")
    if canonical_symbol(R.get("sym")) != sym:
        err(f"report symbol {R.get('sym')!r} != selected {sym!r}")
    if _norm_name(inp.get("name")) != _norm_name(R.get("name")):
        err("company name differs between fetched data and report")
    if _norm_name(R.get("name")) in ("", _norm_name(sym)):
        warn("company name unavailable; symbol used as the name")
    isin_info = ((inp.get("info") or {}).get("isin") or "").upper()
    if expected.get("isin") and isin_info and expected["isin"].upper() != isin_info:
        err(f"ISIN mismatch ({expected['isin']} vs {isin_info})")

    # required data + freshness
    # Scheduled/automated publication is forbidden until provenance, source
    # freshness and regulatory review have explicitly been approved. The
    # research PDF may still be generated privately for inspection.
    publication = R.get("reasoning") or {}
    if publication.get("publication_eligible") is not True:
        err("automated publication blocked: research provenance/freshness/regulatory review not approved")
    cm = R.get("completeness") or {}
    if cm.get("missing_mandatory"):
        err("mandatory data missing: " + ", ".join(cm["missing_mandatory"]))
    df = inp.get("df")
    ltp = (R.get("t") or {}).get("ltp")
    try:
        last = df.index[-1]
        last_d = last.date() if hasattr(last, "date") else date.fromisoformat(str(last)[:10])
        age = (now.date() - last_d).days
        if age < 0:
            err(f"price date {last_d} is in the future")
        elif age > pol["max_price_age_days"]:
            err(f"price data is {age} days old (limit {pol['max_price_age_days']})")
        close = float(df["Close"].iloc[-1])
        if not ltp or ltp <= 0 or abs(ltp / close - 1) > 0.001:
            err("report price does not match the last candle close")
    except Exception as e:
        err(f"price history unusable: {e}")
    fv = R.get("fv") or {}
    fr = (fv.get("freshness") or {}).get("status")
    if fr == "STALE":
        err("quarterly results are stale")
    elif fr == "LAGGING":
        warn("quarterly results are one cycle behind")
    elif fr in (None, "MISSING"):
        warn("quarterly results unavailable")
    years = ((R.get("stm") or {}).get("years")) or []
    try:
        from research_data import period_end
        ends = [period_end(y["label"]) for y in years]
        if any(e is None for e in ends) or any(b <= a for a, b in zip(ends, ends[1:])):
            err("annual periods are not strictly increasing / unparseable")
        elif (now.date() - ends[-1]).days > pol["max_annual_age_days"]:
            err("latest annual period is too old")
        if any((y.get("revenue") is not None and y["revenue"] < 0) for y in years):
            err("negative revenue in annual table (unit/period problem)")
    except Exception as e:
        warn(f"annual period check skipped: {e}")

    # same numbers in table and narrative
    for k, key, yrs in (("rev_cagr3", "revenue", 3), ("rev_cagr5", "revenue", 5),
                        ("pat_cagr3", "net_profit", 3), ("pat_cagr5", "net_profit", 5)):
        if str((fv.get("cagr_basis") or {}).get(k) or "").startswith("annual table"):
            val = eng._cagr_checked(years, key, yrs)[0]
            if val is None or abs(val - fv[k]) > 0.05:
                err(f"{k} ({fv.get(k)}) does not match the annual table ({val})")
    if fv.get("eps") and fv.get("pe") and ltp and abs(fv["eps"] * fv["pe"] / ltp - 1) > 0.30:
        warn("EPS x P/E differs >30% from price (mixed feeds/periods)")

    # score arithmetic + narrative + stance/plan consistency
    sc, plan = R.get("sc") or {}, R.get("plan") or {}
    pill = {k: v for k, v in (sc.get("pillars") or {}).items() if v is not None}
    if pill:
        wsum = sum(eng.WEIGHTS[k] for k in pill)
        comp = sum(eng.WEIGHTS[k] * v for k, v in pill.items()) / wsum
        if abs(comp - sc.get("composite_raw", comp)) > 0.6 or abs(comp - sc.get("composite", comp)) > 1.0:
            err(f"composite {sc.get('composite')} does not match pillars x weights ({comp:.1f})")
        exp = _expected_stance(int(sc["composite"]), (sc["pillars"] or {}).get("Trend"))
        if sc.get("stance") != exp:
            err(f"stance {sc.get('stance')!r} inconsistent with composite {sc['composite']} (expected {exp!r})")
    else:
        err("no scored pillars")
    summ = (R.get("nar") or {}).get("summary", "")
    if str(sc.get("composite")) not in summ or str(sc.get("stance")) not in summ:
        err("narrative summary does not match the scoring output")
    if (sc.get("fund_avg") is None) or sc["fund_avg"] < pol["min_fund_avg"]:
        err(f"fundamental strength {sc.get('fund_avg')} below the {pol['min_fund_avg']:.0f} bar")
    if str(sc.get("stance", "")).startswith(("CAUTIOUS", "NEGATIVE", "QUALITY")) and plan.get("mode") != "DEFENSIVE":
        err("stance and trade-plan mode contradict each other")
    if plan.get("mode") == "DEFENSIVE" and "confirm upside continuation" in (R.get("nar") or {}).get("outlook", ""):
        err("defensive plan described as confirmed upside")
    bt, br = plan.get("bull_trigger"), plan.get("bear_trigger")
    if ltp and bt and br and not (br < ltp < bt):
        err(f"trigger levels inconsistent with price ({br} < {ltp} < {bt} violated)")
    if plan.get("early_trigger") and plan.get("confirm_level") and plan["confirm_level"] < plan["early_trigger"] - 1e-9:
        err("confirmation level is below the early-recovery trigger")
    tc = (R.get("t") or {}).get("traded_cr")
    if tc is None:
        warn("average traded value unavailable")
    elif tc < pol["min_turnover_cr"]:
        err(f"average daily turnover Rs {tc} Cr below {pol['min_turnover_cr']} Cr")

    # PDF
    if pdf_path:
        try:
            size = os.path.getsize(pdf_path)
            with open(pdf_path, "rb") as f:
                head = f.read(5)
                f.seek(max(0, size - 2048))
                tail = f.read()
            if size < pol["min_pdf_bytes"]:
                err(f"PDF too small ({size} bytes)")
            if head != b"%PDF-" or b"%%EOF" not in tail:
                err("PDF is truncated or not a PDF")
            pages, text = _pdf_info(pdf_path)
            if text is None:
                warn("PDF text could not be verified (no pypdf/pdftotext)")
            else:
                if sym not in text or _norm_name(R.get("name"))[:10] not in _norm_name(text):
                    err("PDF text does not contain the selected company")
                if str(sc.get("composite")) not in text:
                    err("PDF text does not contain the composite score")
            if pages is not None and pages < 3:
                err(f"PDF has only {pages} page(s)")
        except Exception as e:
            err(f"PDF check failed: {e}")
    else:
        err("PDF was not generated")

    # caption <-> document association
    if caption is not None:
        if len(caption) > CAPTION_LIMIT:
            err("caption exceeds Telegram's limit")
        if sym not in caption or str(sc.get("stance")) not in caption:
            err("caption does not match the report (symbol/stance)")
        if expected.get("report_id") and pdf_path and expected["report_id"] not in os.path.basename(pdf_path):
            err("PDF file is not associated with this report request")
    return g


def build_caption(R: dict, why: str) -> str:
    sc, t = R["sc"], R["t"]
    hz = sc["horizons"]
    lt, st = hz["Long term (1–3 yrs)"], hz["Short term (1–4 wks)"]
    risks = (R.get("flags") or {}).get("risks") or []
    main_risk = f"{risks[0]['title']} - {risks[0]['evidence']}" if risks else "No major red flag triggered in the available data"
    cap = "\n".join([
        f"{R['name']} | {R['sym']}",
        f"Fundamental Research | {R['asof']}",
        f"Price: ₹{t['ltp']:,.2f} (close of {R['asof']}, not live)",
        f"Stance: {sc['stance']}",
        f"Fundamental assessment: {lt[0].title()} (score {lt[1] if lt[1] is not None else 'n/a'})",
        f"Technical timing: {st[0].title()} (score {st[1] if st[1] is not None else 'n/a'})",
        f"Why selected: {why}",
        f"Main risk: {main_risk}",
        "Full research PDF attached.",
        "Automated educational research; no guaranteed returns.",
    ])
    if len(cap) > CAPTION_LIMIT:                       # trim the longest free-text lines, never the disclaimer
        over = len(cap) - CAPTION_LIMIT + 3
        why_short = why[: max(20, len(why) - over)].rstrip(" ,;") + "..."
        cap = cap.replace(f"Why selected: {why}", f"Why selected: {why_short}")
    if len(cap) > CAPTION_LIMIT:
        mr = main_risk[: max(20, len(main_risk) - (len(cap) - CAPTION_LIMIT + 3))].rstrip() + "..."
        cap = cap.replace(f"Main risk: {main_risk}", f"Main risk: {mr}")
    return cap


# ══════════════════════════════════════════════════════════════════════════
# 4. PUBLICATION LEDGER (SQLite; atomic claims; no blind retries)
# ══════════════════════════════════════════════════════════════════════════
class Ledger:
    """
    Rows: scope 'D' = one per channel+post type+IST date (blocks duplicate scans/posts)
          scope 'C' = one per channel+company+post type+trading date (blocks the same report twice)
    States: processing -> ready -> sending -> sent | failed | ambiguous | skipped
            (a crash while 'sending' is treated as ambiguous and never auto-retried)
    """

    def __init__(self, path: str = LEDGER_DB):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._tx() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS publications(
                pub_key TEXT PRIMARY KEY, scope TEXT, channel TEXT, symbol TEXT, post_type TEXT, trade_date TEXT,
                state TEXT, attempts INTEGER DEFAULT 0, message_id TEXT, report_id TEXT, detail TEXT,
                created_at REAL, updated_at REAL)""")

    @contextmanager
    def _tx(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("BEGIN IMMEDIATE")                         # serialises concurrent workers
            yield con
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            con.close()

    @staticmethod
    def day_key(channel, day):
        return f"D|{channel}|{POST_TYPE}|{day}"

    @staticmethod
    def co_key(channel, symbol, day):
        return f"C|{channel}|{canonical_symbol(symbol)}|{POST_TYPE}|{day}"

    def acquire_day(self, channel: str, day: str, now: Optional[float] = None) -> Tuple[str, Optional[dict]]:
        """-> (status, row). status: acquired | sent | in_progress | ambiguous | skipped | exhausted"""
        now = now or time.time()
        key = self.day_key(channel, day)
        with self._tx() as c:
            row = c.execute("SELECT state, attempts, updated_at, message_id, detail FROM publications WHERE pub_key=?", (key,)).fetchone()
            if row is None:
                c.execute("INSERT INTO publications(pub_key,scope,channel,post_type,trade_date,state,attempts,created_at,updated_at)"
                          " VALUES(?,?,?,?,?,'processing',0,?,?)", (key, "D", channel, POST_TYPE, day, now, now))
                return "acquired", None
            state, attempts, upd, mid, detail = row
            info = {"state": state, "attempts": attempts, "message_id": mid, "detail": detail}
            if state in ("processing", "ready"):
                if now - upd < CLAIM_LEASE_S:
                    return "in_progress", info
                c.execute("UPDATE publications SET state='processing', updated_at=? WHERE pub_key=?", (now, key))
                return "acquired", info                                 # crashed worker's lease expired
            if state == "failed" and attempts < MAX_SEND_ATTEMPTS:
                c.execute("UPDATE publications SET state='processing', updated_at=? WHERE pub_key=?", (now, key))
                return "acquired", info
            return {"sent": "sent", "ambiguous": "ambiguous", "sending": "ambiguous", "skipped": "skipped"}.get(state, "exhausted"), info

    def recent_symbols(self, channel: str, days: int = RECENT_DAYS, now: Optional[float] = None) -> set:
        now = now or time.time()
        with self._tx() as c:
            rows = c.execute("SELECT symbol FROM publications WHERE scope='C' AND channel=? AND updated_at>=?"
                             " AND (state IN ('sent','ambiguous','sending') OR (state='ready' AND updated_at>=?))",
                             (channel, now - days * 86400, now - CLAIM_LEASE_S)).fetchall()
        return {r[0] for r in rows}

    def attach_company(self, channel: str, symbol: str, trade_date: str, report_id: str, day: str) -> bool:
        key, now = self.co_key(channel, symbol, trade_date), time.time()
        with self._tx() as c:
            if c.execute("SELECT 1 FROM publications WHERE pub_key=? AND (state IN ('sent','ambiguous','sending')"
                         " OR (state='ready' AND updated_at>=?))", (key, now - CLAIM_LEASE_S)).fetchone():
                return False                                            # same company/report already published or pending
            c.execute("INSERT OR REPLACE INTO publications(pub_key,scope,channel,symbol,post_type,trade_date,state,attempts,report_id,created_at,updated_at)"
                      " VALUES(?,?,?,?,?,?,'ready',COALESCE((SELECT attempts FROM publications WHERE pub_key=?),0),?,?,?)",
                      (key, "C", channel, canonical_symbol(symbol), POST_TYPE, trade_date, key, report_id, now, now))
            c.execute("UPDATE publications SET state='ready', symbol=?, report_id=?, updated_at=? WHERE pub_key=?",
                      (canonical_symbol(symbol), report_id, now, self.day_key(channel, day)))
        return True

    def mark(self, channel: str, day: str, state: str, symbol: Optional[str] = None, trade_date: Optional[str] = None,
             message_id=None, detail: Optional[str] = None, bump_attempt: bool = False):
        now = time.time()
        keys = [self.day_key(channel, day)]
        if symbol and trade_date:
            keys.append(self.co_key(channel, symbol, trade_date))
        with self._tx() as c:
            for k in keys:
                c.execute("UPDATE publications SET state=?, message_id=COALESCE(?,message_id), detail=COALESCE(?,detail),"
                          " attempts=attempts+?, updated_at=? WHERE pub_key=?",
                          (state, None if message_id is None else str(message_id), (detail or "")[:500] or None,
                           1 if bump_attempt else 0, now, k))

    def get(self, key: str) -> Optional[dict]:
        with self._tx() as c:
            r = c.execute("SELECT state, attempts, message_id, symbol, detail, report_id FROM publications WHERE pub_key=?", (key,)).fetchone()
        return dict(zip(("state", "attempts", "message_id", "symbol", "detail", "report_id"), r)) if r else None


# ══════════════════════════════════════════════════════════════════════════
# 5. ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════
class Deps:
    """Network-facing pieces, injectable for tests."""

    def universe(self, now):
        return load_universe(now)

    def fetch_compact(self, sym: str) -> Optional[dict]:
        from fundamentals import get_fundamentals
        import research_data as rd
        import industry as industry_mod
        import research_engine as eng
        from nifty500_collector import get_stock_sector
        fund = get_fundamentals(sym) or {}
        stm = rd.get_statements(sym)
        extras = rd.get_screener_extras(sym) or {}
        sector = get_stock_sector(sym)
        ind = industry_mod.classify(fund.get("name") or sym, sector, None, stm)
        fv = eng.compute_fundamentals(fund, stm, extras, [], sector, 0.0, ind)
        return compact_metrics(fv, stm)

    def collect(self, sym):
        import report_generator as rg
        return rg.collect_inputs(sym)

    def assemble(self, inp):
        import report_generator as rg
        return rg.assemble(inp, use_ai=False)                         # rule-based commentary only

    def build_pdf(self, R, path):
        import report_generator as rg
        return rg.build_pdf(R, path)


def selection_why(cand: dict, rank: int, n: int) -> str:
    return (f"passed the sector-appropriate quality screen (score {cand['score']:.0f}/100, #{rank} of {n} shortlisted) - "
            + ", ".join(cand["reasons"][:4]))


def prepare(channel: str, now: Optional[datetime] = None, deps: Optional[Deps] = None, ledger: Optional[Ledger] = None,
            out_dir: Optional[str] = None) -> dict:
    """Returns a JSON-able dict. status: READY | DUPLICATE | IN_PROGRESS | BLOCKED | SKIP | ERROR."""
    now = now or datetime.now(IST)
    deps, ledger = deps or Deps(), ledger or Ledger()
    day = now.strftime("%Y-%m-%d")
    status, info = ledger.acquire_day(channel, day)
    if status != "acquired":
        return {"status": {"sent": "DUPLICATE", "in_progress": "IN_PROGRESS", "ambiguous": "BLOCKED", "skipped": "DUPLICATE"}.get(status, "BLOCKED"),
                "reason": f"ledger: {status}", "detail": info, "alert": status in ("ambiguous", "exhausted")}
    try:
        uni = deps.universe(now)
        symbols = uni["symbols"]
        cache = _read_json(SCREEN_FILE, {})
        warmed = warm_screen_cache(symbols, cache, WARM_BATCH, deps.fetch_compact, now)
        if warmed:
            _write_json(SCREEN_FILE, cache)
        ranked = rank_candidates(symbols, cache, now, exclude=ledger.recent_symbols(channel))
        shortlist = ranked[:SHORTLIST_SIZE]
        if not shortlist:
            msg = (f"no candidate passed the quality screen (universe {len(symbols)}, cached {len(cache)}, warmed {warmed} this run)")
            ledger.mark(channel, day, "skipped", detail=msg)
            return {"status": "SKIP", "reason": msg, "alert": True}
        failures = []
        for i, cand in enumerate(shortlist, 1):
            sym = cand["sym"]
            try:
                inp = deps.collect(sym)
                inp["selection"] = {"why": selection_why(cand, i, len(shortlist)), "screen_score": cand["score"]}
                R = deps.assemble(inp)
                report_id = uuid.uuid4().hex[:12]
                od = out_dir or os.path.join("output", "reports")
                os.makedirs(od, exist_ok=True)
                pdf = os.path.join(od, f"{sym}_Research_{R['asof'].replace('-', '')}_{report_id}.pdf")
                deps.build_pdf(R, pdf)
                caption = build_caption(R, inp["selection"]["why"])
                gate = validate_report({"symbol": sym, "isin": (uni.get("isin") or {}).get(sym), "report_id": report_id},
                                       inp, R, pdf, caption, now)
                if not gate.ok:
                    failures.append(f"{sym}: " + "; ".join(gate.errors[:3]))
                    logger.warning(f"[fund] gate blocked {sym}: {gate.errors}")
                    continue
                last = inp["df"].index[-1]
                trade_date = (last.date() if hasattr(last, "date") else date.fromisoformat(str(last)[:10])).isoformat()
                if not ledger.attach_company(channel, sym, trade_date, report_id, day):
                    failures.append(f"{sym}: already published/pending")
                    continue
                with open(pdf, "rb") as f:
                    sha = hashlib.sha256(f.read()).hexdigest()[:16]
                return {"status": "READY", "symbol": sym, "name": R["name"], "pdf": os.path.abspath(pdf), "caption": caption,
                        "report_id": report_id, "day": day, "trade_date": trade_date, "pdf_sha": sha,
                        "warnings": gate.warnings, "skipped_candidates": failures}
            except Exception as e:                                     # one bad candidate must not abort the run
                failures.append(f"{sym}: {str(e)[:120]}")
                logger.warning(f"[fund] candidate {sym} failed: {e}")
        msg = "all shortlisted candidates failed validation: " + " | ".join(failures)
        ledger.mark(channel, day, "skipped", detail=msg)
        return {"status": "SKIP", "reason": msg[:900], "alert": True}
    except Exception as e:
        ledger.mark(channel, day, "failed", detail=str(e), bump_attempt=True)
        logger.error(f"[fund] prepare failed: {e}", exc_info=True)
        return {"status": "ERROR", "reason": str(e)[:300], "alert": True}


def begin_send(channel: str, day: str, symbol: str, trade_date: str, ledger: Optional[Ledger] = None):
    """Called right before the Telegram request. A crash after this point is 'ambiguous' and is never auto-retried."""
    (ledger or Ledger()).mark(channel, day, "sending", symbol, trade_date)


def confirm_sent(channel: str, day: str, symbol: str, trade_date: str, message_id, ledger: Optional[Ledger] = None):
    (ledger or Ledger()).mark(channel, day, "sent", symbol, trade_date, message_id=message_id)


def record_failure(channel: str, day: str, symbol: str, trade_date: str, reason: str, ambiguous: bool = False,
                   ledger: Optional[Ledger] = None) -> str:
    """ambiguous -> never auto-retried (the document may already be in the channel). Otherwise 'failed' (bounded retry)."""
    state = "ambiguous" if ambiguous else "failed"
    (ledger or Ledger()).mark(channel, day, state, symbol, trade_date, detail=reason, bump_attempt=True)
    return state
