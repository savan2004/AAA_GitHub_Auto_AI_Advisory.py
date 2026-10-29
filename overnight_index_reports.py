"""MSK nightly full-index equity evidence ledger (no Telegram, no auto-BUY).

Each constituent is examined, and each failure is preserved separately. Uses
official NSE memberships, existing free price/fundamental collectors, a durable
PostgreSQL DB in production, or SQLite for tests/offline development. Never
requires a paid LLM; never turns preliminary figures into validated Top 5.

Usage (IST): python -m overnight_index_reports run --profile all
             python -m overnight_index_reports finalize --profile all
             python -m overnight_index_reports status --profile moderate
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from index_constituents import INDEXES, get_index_universe, IST

LOG = logging.getLogger(__name__)
PROFILES = tuple(INDEXES)
PRICE_REQUIRED = 0.90
MAX_REPORT_CHARS = 3900
LIMITS = {"conservative": 8.0, "moderate": 11.0, "aggressive": 15.0}
TBL_ROWS = "msk_index_evidence_v1"
TBL_REPORTS = "msk_index_reports_v1"


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return _clean(value.item())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json(value):
    return json.dumps(_clean(value), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)


def _session(value=None, *, now=None):
    if value:
        return date.fromisoformat(str(value))
    now = now or datetime.now(IST)
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        # Morning build belongs to preceding trading *weekday*. Exchange
        # holidays require price-session validation; never invent a candle.
        x = now.date() - timedelta(days=1)
    else:
        x = now.date()
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


class EvidenceStore:
    """Small, auditable persistence API. Neon optional but required for cron.

    No fallback to /tmp in production: a Render restart would lose reports.
    SQL queries use parameters; symbols and source text are never SQL strings.
    """
    def __init__(self, url=None):
        self.url = url if url is not None else os.environ.get("MSK_INDEX_DATABASE_URL", "")
        if not self.url:
            raise RuntimeError("MSK_INDEX_DATABASE_URL missing: no durable overnight database")
        self.pg = self.url.startswith(("postgresql://", "postgres://"))
        if self.pg:
            import psycopg
            self.db = psycopg.connect(self.url, connect_timeout=8)
        elif self.url.startswith("sqlite:///"):
            self.db = sqlite3.connect(self.url[10:], timeout=25)
        else:
            raise ValueError("Expected postgres:// or sqlite:/// store URI")
        self.prepare()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def _execute(self, sql, args=()):
        # Same prepared SQL on two database adapters.
        return self.db.execute(sql.replace("?", "%s") if self.pg else sql, args)

    def prepare(self):
        self._execute(f"""CREATE TABLE IF NOT EXISTS {TBL_ROWS} (
            trade_date TEXT NOT NULL, profile TEXT NOT NULL, symbol TEXT NOT NULL,
            data TEXT NOT NULL, updated_utc TEXT NOT NULL,
            PRIMARY KEY(trade_date,profile,symbol))""")
        self._execute(f"""CREATE TABLE IF NOT EXISTS {TBL_REPORTS} (
            trade_date TEXT NOT NULL, profile TEXT NOT NULL,
            state TEXT NOT NULL, data TEXT NOT NULL, rendered TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            PRIMARY KEY(trade_date,profile))""")
        self.db.commit()

    def row(self, day, profile, sym):
        got = self._execute(f"SELECT data FROM {TBL_ROWS} WHERE trade_date=? AND profile=? AND symbol=?",
                            (str(day), profile, sym)).fetchone()
        return json.loads(got[0]) if got else None

    def write_row(self, day, profile, sym, value):
        self._execute(f"""INSERT INTO {TBL_ROWS}
            (trade_date,profile,symbol,data,updated_utc) VALUES(?,?,?,?,?)
            ON CONFLICT (trade_date,profile,symbol) DO UPDATE
            SET data=excluded.data,updated_utc=excluded.updated_utc""",
            (str(day),profile,sym,_json(value),datetime.utcnow().isoformat()))
        self.db.commit()

    def rows(self, day, profile):
        return {sym: json.loads(raw) for sym, raw in self._execute(
            f"SELECT symbol,data FROM {TBL_ROWS} WHERE trade_date=? AND profile=?",
            (str(day), profile)).fetchall()}

    def save_report(self, day, profile, state, data, rendered):
        self._execute(f"""INSERT INTO {TBL_REPORTS}
            (trade_date,profile,state,data,rendered,updated_utc) VALUES(?,?,?,?,?,?)
            ON CONFLICT (trade_date,profile) DO UPDATE
            SET state=excluded.state,data=excluded.data,rendered=excluded.rendered,
                updated_utc=excluded.updated_utc""",
            (str(day),profile,state,_json(data),rendered,
             datetime.utcnow().isoformat()))
        self.db.commit()

    def get_report(self, profile, day=None):
        if day:
            query=f"SELECT trade_date,state,data,rendered FROM {TBL_REPORTS} WHERE profile=? AND trade_date=?"
            params=(profile,str(day))
        else:
            query=(f"SELECT trade_date,state,data,rendered FROM {TBL_REPORTS} "
                   "WHERE profile=? ORDER BY trade_date DESC LIMIT 1")
            params=(profile,)
        record=self._execute(query,params).fetchone()
        return dict(day=record[0],state=record[1],stats=json.loads(record[2]),
                    text=record[3]) if record else None


def _fresh(row, session):
    return bool(row and row.get("asof")==str(session))


def _member_hash(universe):
    return hashlib.sha256(",".join(sorted(universe["symbols"])).encode()).hexdigest()[:16]


def collect_profile(profile, store, session=None, *, batch_size=50,
                    delay_s=0.8, max_seconds=4.5 * 3600):
    """Attempt the FULL official index, always checkpointing each symbol.

    Idempotent reruns skip fresh completed work and retry missing price bars.
    Financials are fetched at least once for EVERY member, not top 28 only.
    """
    if profile not in INDEXES:
        raise ValueError("Unknown index profile")
    from market_data import bulk_history
    from top5_company_ratings import fetch_company_evidence
    from top5_technical_ratings import technical_candidate

    session = _session(session)
    started=time.monotonic()
    universe=get_index_universe(profile)
    symbols=tuple(universe["symbols"])
    identity=_member_hash(universe)
    saved=store.rows(session,profile)
    # Data is downloaded for the whole index in bounded 50-stock chunks.
    # Fresh prices still require bar-session validation later.
    pending_price=[s for s in symbols if not _fresh(saved.get(s,{}).get("technical"),session)
                   or saved.get(s,{}).get("membership_hash")!=identity]
    prices=bulk_history(pending_price,"6mo",chunk_size=batch_size,
                        cache_ttl=0,adjusted=False,retries=1) if pending_price else {}
    if not isinstance(prices,dict):
        prices={}

    examined=0
    for sym in symbols:
        old=saved.get(sym,{})
        if time.monotonic()-started >= max_seconds:
            LOG.warning("%s budget exhausted after %s; will resume",profile,examined)
            break
        fresh_member=(old.get("membership_hash")==identity)
        need_price=not fresh_member or not _fresh(old.get("technical"),session)
        need_fund=not fresh_member or not old.get("fund_attempted")
        # Rerun unverified providers once more on an overnight retry, bounded.
        need_fund |= bool(old.get("fund_status")=="UNVERIFIED" and
                          int(old.get("fund_attempts",0))<2)
        if not need_price and not need_fund:
            examined+=1
            continue
        row=dict(old if fresh_member else {})
        row.update(symbol=sym,industry=universe["industries"].get(sym,"Unclassified"),
                   membership_hash=identity,index_source=universe["source"],
                   trade_date=str(session),price_checked=True)
        if need_price:
            frame=prices.get(sym)
            candidate_now=datetime.combine(session,datetime.min.time(),tzinfo=IST).replace(hour=16)
            tech=None
            if frame is not None and not frame.empty:
                try:
                    tech=technical_candidate(sym,frame,profile,candidate_now)
                except (TypeError,ValueError,KeyError,AttributeError):
                    tech=None
            source="Yahoo bulk NSE daily OHLCV"
            # Retry keyless bhavcopy after the nightly publication window.
            # No routine retries for already-fresh Yahoo prices.
            if (not _fresh(tech,session) and
                os.getenv("MSK_INDEX_TEJ_FALLBACK","0")=="1"):
                from overnight_index_sources import fetch_tej_history
                alternate=fetch_tej_history(sym,session)
                if alternate is not None:
                    try:
                        recovered=technical_candidate(sym,alternate,profile,candidate_now)
                        if _fresh(recovered,session):
                            tech=recovered
                            source="TejHQ / NSE bhavcopy (raw EOD)"
                    except (TypeError,ValueError,KeyError,AttributeError):
                        pass
            if _fresh(tech,session):
                row["technical"]=_clean(tech)
                row["price_reason"]="ok"
                row["price_source"]=source
            else:
                row["technical"]=None
                row["price_reason"]=("stale_candle_"+str((tech or {}).get("asof"))
                                     if tech else "missing_or_rejected_ohlcv")
                row["price_source"]="unavailable"
        if need_fund:
            row["fund_attempted"]=True
            row["fund_attempts"]=int(row.get("fund_attempts",0))+1
            try:
                f=fetch_company_evidence(sym,row["industry"],profile,session)
                if not isinstance(f,dict):
                    raise ValueError("invalid_fundamental_payload")
                row["fundamental"]=_clean(f)
                row["fund_status"]=f.get("fund_status","UNVERIFIED")
                if row["fund_status"] not in (
                      "VALIDATED","PRELIMINARY","REJECTED","UNVERIFIED"):
                    row["fund_status"]="UNVERIFIED"
            except Exception as e:
                row["fundamental"]={}
                row["fund_status"]="UNVERIFIED"
                row["fundamental_error"]=type(e).__name__
        row["collected_utc"]=datetime.utcnow().isoformat()+"Z"
        store.write_row(session,profile,sym,row)
        examined+=1
        if delay_s and need_fund:
            time.sleep(delay_s)
    return dict(profile=profile,session=str(session),
                expected=len(symbols),examined=examined,member_hash=identity,
                elapsed_s=round(time.monotonic()-started,1))


def render_snapshot(profile, universe, records, session):
    """Build a READY-only verified research report. No provisional Top 5."""
    symbols=tuple(universe["symbols"])
    total=len(symbols)
    identity=_member_hash(universe)
    rows=[records.get(s) for s in symbols]
    reviewed=sum(bool(r and r.get("fund_attempted") and
                    r.get("membership_hash")==identity) for r in rows)
    complete=sum(bool(r and r.get("technical") and _fresh(r.get("technical"),session))
                 for r in rows)
    verified=sum(bool(r and r.get("fund_status")=="VALIDATED") for r in rows)
    provisional=sum(bool(r and r.get("fund_status")=="PRELIMINARY") for r in rows)
    missing=[s for s,r in zip(symbols,rows)
             if not r or not r.get("fund_attempted")]
    stale=[s for s,r in zip(symbols,rows)
           if not r or not _fresh(r.get("technical"),session)]
    counts=Counter((r or {}).get("fund_status","NOT_ATTEMPTED") for r in rows)
    full_review=reviewed==total
    quote_coverage=complete/total if total else 0.0
    publishable=bool(full_review and quote_coverage>=PRICE_REQUIRED and
                     not universe.get("stale"))
    evaluated=[]
    if publishable:
        for r in rows:
            if (not r or r.get("fund_status")!="VALIDATED" or
                not _fresh(r.get("technical"),session)):
                continue
            t=r["technical"]
            if t.get("risk_pct",float("inf"))>LIMITS[profile]:
                continue
            if t.get("signal")!="CONDITIONAL BUY":
                continue
            f=r.get("fundamental") or {}
            if f.get("fund_score") is None:
                continue
            evaluated.append(dict(t,sector=r.get("industry","Unclassified"),
                                  fundamentals=f,overall=.6*f["fund_score"]+
                                  .4*t["tech_score"]))
    selected=[];sectors=Counter()
    for row in sorted(evaluated,key=lambda x:(-x["overall"],-x["tech_score"],x["sym"])):
        sector=row["sector"]
        if sectors[sector]>=2:
            continue
        sectors[sector]+=1;selected.append(row)
        if len(selected)==5:
            break
    state="READY" if publishable else "PARTIAL"
    lines=[
        f"📊 <b>{html.escape(universe['cap'].upper())} | {html.escape(universe['name'])}</b>",
        f"📅 NSE completed session: <b>{session}</b> | Prepared overnight",
        f"🏛 Membership: {html.escape(universe['source'])} ({total} constituents)",
        f"🔍 Fundamental reviews attempted: <b>{reviewed}/{total}</b> ({reviewed/max(1,total):.1%})",
        f"📈 Fresh daily-chart analysis: <b>{complete}/{total}</b> ({quote_coverage:.1%})",
        f"🧾 Rule-complete evidence: <b>{verified}/{total}</b> | Provisional {provisional}",
        "📑 Issuer filing independently audited: <b>NOT CLAIMED</b>",
        f"🧭 Report status: <b>{state}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not publishable:
        lines.append("🛑 Top 5 WITHHELD: incomplete index-wide review, price coverage,"
                     " or stale membership. No forced recommendations.")
        if missing:lines.append(f"⚠️ Not reviewed: {len(missing)} ({', '.join(missing[:7])})")
        if stale:lines.append(f"⚠️ Missing/stale EOD: {len(stale)} ({', '.join(stale[:7])})")
    elif not selected:
        lines.append("🛑 No fully rule-validated conditional setups. No Top 5 issued.")
    else:
        lines.append(f"✅ <b>Verified-rule watchlist: {len(selected)} / 5 qualifying</b>")
        for i,t in enumerate(selected,1):
            f=t["fundamentals"]
            lines.extend([
                f"<b>{i}. {html.escape(t['sym'])}</b> | {html.escape(t['sector'])}",
                f"₹{t['ltp']:,.2f} | Technical {t['tech_score']:.0f}/100 |"
                f" Financial rule score {f['fund_score']:.0f}/100",
                f"RSI {t['rsi']:.1f} | ADX {t['adx']:.1f} | Stop distance {t['risk_pct']:.1f}%",
                f"Conditional entry ₹{t['entry']:,.2f} | SL ₹{t['sl']:,.2f}",
                "⚠️ Mathematical risk levels; NOT return forecasts or validated trade win rates.",
            ])
    lines.append("ℹ️ Financial evidence from public aggregators; not an audit of issuer filings.")
    lines.append("Educational research; no SEBI-registered personal advice.")
    output="\n".join(lines)
    if len(output)>MAX_REPORT_CHARS:
        output=output[:MAX_REPORT_CHARS-135].rsplit("\n",1)[0]+"\n⚠️ Output shortened; see ledger for stock-by-stock records."
    return state,dict(trade_date=str(session),expected=total,reviewed=reviewed,
                      price_fresh=complete,validated=verified,provisional=provisional,
                      breakdown=dict(counts),missing_review=missing,missing_price=stale,
                      membership_hash=identity,candidate_count=len(selected),
                      eligible_count=len(evaluated)),output


def finalize_profile(profile, store, session=None):
    session=_session(session)
    universe=get_index_universe(profile)
    records=store.rows(session,profile)
    state,stats,body=render_snapshot(profile,universe,records,session)
    store.save_report(session,profile,state,stats,body)
    return dict(profile=profile,state=state,stats=stats,text=body)


def load_prepared_report(profile, *, store=None, now=None):
    """Telegram-safe status from durable snapshot; never fetch on tap."""
    if profile not in INDEXES:
        raise ValueError("Unknown index profile")
    try:
        if store is not None:
            record=store.get_report(profile)
        else:
            with EvidenceStore() as owned:
                record=owned.get_report(profile)
    except Exception as e:
        LOG.warning("Prepared report DB unavailable (%s)",type(e).__name__)
        return "⚠️ Overnight index research database unavailable. No unverified Top 5 released."
    if not record:
        return "⏳ Overnight report not prepared yet. No substitute 28-company scan."
    date_loaded=date.fromisoformat(record["day"])
    now=now or datetime.now(IST)
    age=(now.date()-date_loaded).days
    expected=_session(now=now)
    if age<0 or age>4 or date_loaded!=expected:
        return (f"⏳ Latest report is dated {date_loaded}; expected completed "
                f"NSE session {expected}. Overnight report not ready. "
                "Old rankings will not be presented as fresh.")
    if record["state"]!="READY":
        return record["text"]+"\n⏳ Awaiting next overnight retry."
    return record["text"]


def main(argv=None):
    parser=argparse.ArgumentParser(description="Full-universe overnight equity research")
    parser.add_argument("action",choices=("run","finalize","status"))
    parser.add_argument("--profile",choices=("all",)+PROFILES,default="all")
    parser.add_argument("--session",help="Expected completed NSE session YYYY-MM-DD")
    parser.add_argument("--pause",type=float,default=.8)
    parser.add_argument("--budget-hours",type=float,default=4.5)
    opts=parser.parse_args(argv)
    profiles=PROFILES if opts.profile=="all" else (opts.profile,)
    with EvidenceStore() as store:
        for profile in profiles:
            if opts.action=="run":
                result=collect_profile(profile,store,opts.session,
                    delay_s=max(0,opts.pause),
                    max_seconds=max(1,opts.budget_hours)*3600)
            elif opts.action=="finalize":
                result=finalize_profile(profile,store,opts.session)
            else:
                result=store.get_report(profile) or {"state":"NOT_READY"}
            print(_json(result),flush=True)


if __name__=="__main__":
    main()
