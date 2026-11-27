"""Weekly research shortlist, never a customer stock recommendation.

Turnover prioritizes data collection, not investment quality. The preliminary
fundamental rubric is intentionally small and does not establish suitability,
valuation, governance, primary-source authentication or future returns.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import time

from msk_ai_builder_bank import StagedCompany, BankError, encode_snapshot
from msk_ai_builder_evidence import EvidenceError, collect_secondary
from msk_ai_builder_weekly_sources import fetch_roster, fetch_closes, ListedCompany, WeeklyClose, INDEX_SIZES

@dataclass(frozen=True)
class WeeklyReview:
    universe_size: int
    close_coverage: int
    attempted: tuple
    scored: tuple
    companies: tuple
    reviews: dict
    failures: dict
    status: str
    reserves: tuple = ()
    primary_checks: tuple = ()
    primary_rejections: tuple = ()

def preliminary_score(e):
    """Range 0–100; provisional research triage only, not investment ranking."""
    annual=e.annual
    quarterly=e.quarterly
    if len(annual)!=5 or len(quarterly)!=8:
        raise EvidenceError("INCOMPLETE_FINANCIAL_HISTORY")
    if any(not math.isfinite(p.revenue_cr) or not math.isfinite(p.profit_cr) or p.revenue_cr<=0
           for p in (*annual,*quarterly)):
        raise EvidenceError("INVALID_FINANCIAL_VALUES")
    if annual[0].profit_cr<=0 or annual[-1].profit_cr<=0 or quarterly[-1].profit_cr<=0:
        return 0
    rev=e.revenue_cagr_pct
    profit=e.profit_cagr_pct
    if rev is None or profit is None:
        return 0
    # Absolute margins are not comparable across industries. Avoid them.
    positive_years=sum(p.profit_cr>0 for p in annual)
    positive_quarters=sum(p.profit_cr>0 for p in quarterly)
    q_growth=quarterly[-1].revenue_cr/quarterly[-5].revenue_cr-1
    points=(25*min(max(rev,0),15)/15 +25*min(max(profit,0),20)/20
            +20*positive_years/5+20*positive_quarters/8
            +10*min(max(q_growth,0),.15)/.15)
    return round(points,2)

def plan_review(roster,closes,*,previous=(),close_date,now,evidence_provider=None,
                max_attempts=40,deadline_seconds=300,clock=time.monotonic,utc_clock=None,
                quarter_verifier=None):
    """Review incumbents first; don't drop one due to a transient fetch failure.

    A bounded candidate batch is never reported as a full 500-company analysis.
    Scores and selected rows remain INTERNAL_SECONDARY_STAGING.
    """
    if not isinstance(now,datetime) or now.utcoffset() is None:
        raise BankError("TIMEZONE_REQUIRED")
    if type(max_attempts) is not int or not 20<=max_attempts<=60 or type(deadline_seconds) not in (int,float) or not 1<=deadline_seconds<=600:
        raise BankError("WEEKLY_BUDGET_INVALID")
    roster=tuple(roster)
    if len(roster) not in INDEX_SIZES or any(not isinstance(c,ListedCompany) for c in roster):
        raise BankError("INDEX_UNAVAILABLE")
    by_symbol={c.member.symbol:c for c in roster}
    if len(by_symbol)!=len(roster) or len({c.member.isin for c in roster})!=len(roster):
        raise BankError("INDEX_UNAVAILABLE")
    previous=tuple(previous)
    if len(previous)>20 or len(set(previous))!=len(previous):
        raise BankError("PREVIOUS_COMPANIES_INVALID")
    if len(closes)<400 or any(s not in by_symbol or not isinstance(p,WeeklyClose) or p.symbol!=s or p.day!=close_date or p.price<=0 or p.turnover_lakh<=0 for s,p in closes.items()):
        raise BankError("EOD_COVERAGE_INSUFFICIENT")
    provider=evidence_provider or collect_secondary
    start=clock()
    incumbent_set=set(previous)
    alternatives=sorted((s for s in closes if s not in incumbent_set),
                         key=lambda s:(-closes[s].turnover_lakh,s))
    decisions={}
    attempts=[]
    scored=[]
    candidates=[]
    failures={}
    for symbol in (*previous,*alternatives):
        if len(attempts)>=max_attempts or clock()-start>=deadline_seconds:
            break
        attempts.append(symbol)
        if symbol not in by_symbol:
            decisions[symbol]="removed: absent from current official index roster"
            continue
        if by_symbol[symbol].series!="EQ":
            if symbol in incumbent_set:
                decisions[symbol]="removed: exchange series outside ordinary equity research scope"
            continue
        if symbol not in closes:
            failures[symbol]="EOD_MISSING"
            continue
        c,p=by_symbol[symbol],closes[symbol]
        try:
            e=provider(symbol,timeout=min(9,max(1,(deadline_seconds-(clock()-start))/2)))
            if clock()-start>deadline_seconds:
                failures[symbol]="DEADLINE_EXCEEDED"
                break
            if e.symbol!=symbol or e.eligible_for_selection:
                raise EvidenceError("SECONDARY_IDENTITY_INVALID")
            score=preliminary_score(e)
            item=StagedCompany(c.member,c.sector,c.name,e,p.day,p.price,p.source)
            # Reuse the bank contract for freshness and source validation now;
            # don't wait until a complete refresh to discover an invalid row.
            checked_at=utc_clock() if utc_clock else now
            encode_snapshot([item],reviewed_at=checked_at,close_date=close_date,reviews={},now=checked_at)
            scored.append((symbol,score))
            if score>=65:
                candidates.append((score,item))
            elif symbol in incumbent_set:
                decisions[symbol]="removed: below preliminary financial screen"
        except (EvidenceError,BankError) as exc:
            failures[symbol]=str(exc)
        except Exception:
            failures[symbol]="PROVIDER_UNAVAILABLE"
    # Every prior company requires a completed assessment or index removal.
    assessed={s for s,_ in scored}|set(decisions)
    if not incumbent_set.issubset(assessed):
        return WeeklyReview(len(roster),len(closes),tuple(attempts),tuple(scored),(),{},failures,
                            "WITHHELD_INCOMPLETE_INCUMBENT_REVIEW")
    # Deterministic sector diversification: maximum five per index industry.
    # Keep passing incumbents first, then fill available slots with alternatives.
    candidates.sort(key=lambda pair:(pair[1].member.symbol not in incumbent_set,-pair[0],pair[1].member.symbol))
    selected=[]
    sector_counts={}
    for _,c in candidates:
        if len(selected)>=20 or sector_counts.get(c.sector,0)>=5:
            if c.member.symbol in incumbent_set:
                decisions[c.member.symbol]="removed: preliminary diversification limit"
            continue
        selected.append(c)
        sector_counts[c.sector]=sector_counts.get(c.sector,0)+1
        if c.member.symbol in incumbent_set:
            decisions[c.member.symbol]="retained"
    initial_symbols={c.member.symbol for c in selected}
    reserves=[c for _,c in candidates if c.member.symbol not in initial_symbols]
    primary_checks,primary_rejections=[],[]
    if quarter_verifier is not None:
        # Keep the original 20 in priority order. Verify each once, then fill
        # only failed slots from already scored alternatives, not the entire index.
        primary_start=clock()
        verified=[]
        sector_counts={}
        def verify(c):
            try:
                obs=quarter_verifier(c.member,c.evidence)
                if not isinstance(obs,dict) or obs.get("symbol")!=c.member.symbol:
                    raise EvidenceError("PRIMARY_OBSERVATION_IDENTITY")
                if obs.get("status")=="LATEST_QUARTER_MATCHED_ONLY":
                    if (obs.get("isin")!=c.member.isin
                            or obs.get("period")!=c.evidence.quarterly[-1].end.isoformat()
                            or obs.get("scope")!=c.evidence.scope
                            or obs.get("publication_status")!="WITHHELD"):
                        raise EvidenceError("PRIMARY_OBSERVATION_IDENTITY")
                    primary_checks.append(obs)
                    verified.append(c)
                    sector_counts[c.sector]=sector_counts.get(c.sector,0)+1
                    return
                if obs.get("publication_status")!="WITHHELD":
                    raise EvidenceError("PRIMARY_OBSERVATION_IDENTITY")
            except Exception:
                obs={"symbol":c.member.symbol,"status":"PRIMARY_CHECK_UNAVAILABLE",
                     "stage":"PRIMARY_VERIFICATION","publication_status":"WITHHELD"}
            primary_rejections.append(obs)
            if c.member.symbol in incumbent_set:
                decisions[c.member.symbol]="removed: latest quarter evidence unavailable or mismatched"
        for c in selected:
            if clock()-primary_start>=180:
                break
            verify(c)
        reserve_attempts=0
        for c in reserves:
            if len(verified)>=20 or reserve_attempts>=10 or clock()-primary_start>=180:
                break
            if sector_counts.get(c.sector,0)>=5:
                continue
            reserve_attempts+=1
            verify(c)
        selected=verified
        selected_symbols={c.member.symbol for c in selected}
        for symbol in incumbent_set:
            if symbol in selected_symbols:
                decisions[symbol]="retained"
            elif decisions.get(symbol)=="retained":
                decisions[symbol]="removed: latest quarter review incomplete"
        rejected_symbols={r["symbol"] for r in primary_rejections}
        remaining=tuple(c.member.symbol for c in reserves
                        if c.member.symbol not in selected_symbols|rejected_symbols)[:10]
        # A partial bank cannot replace the previous snapshot in verified mode.
        status="INTERNAL_SECONDARY_STAGING" if len(selected)==20 else "WITHHELD_PRIMARY_COVERAGE"
        return WeeklyReview(len(roster),len(closes),tuple(attempts),tuple(scored),tuple(selected),
                            decisions,failures,status,remaining,tuple(primary_checks),tuple(primary_rejections))
    return WeeklyReview(len(roster),len(closes),tuple(attempts),tuple(scored),tuple(selected),decisions,failures,
                        "INTERNAL_SECONDARY_STAGING" if selected else "WITHHELD_NO_QUALIFIERS",
                        tuple(c.member.symbol for c in reserves[:10]))

def stage_week(bank,*,now=None,roster_provider=fetch_roster,close_provider=fetch_closes,
               evidence_provider=None,max_attempts=40,deadline_seconds=300,once_per_week=False,
               quarter_verifier=None):
    """Internal staging refresh; optional one successful review per ISO session week."""
    if type(once_per_week) is not bool:
        raise BankError('INVALID_WEEKLY_MODE')
    utc_clock=(lambda: datetime.now(timezone.utc)) if now is None else (lambda: now)
    now=utc_clock()
    if now.utcoffset() is None:
        raise BankError("TIMEZONE_REQUIRED")
    from portfolio_doctor_live import expected_doctor_session
    close_date=expected_doctor_session(now)
    # The calendar helper explicitly refuses unreviewed years.
    try:
        version,old=bank.read_for_review(now=now)
    except BankError as exc:
        if str(exc)!="BANK_EMPTY":
            raise
        version,old=None,{"companies":[]}
    if once_per_week and version is not None:
        try:
            old_close=date.fromisoformat(old["close_date"])
        except (KeyError,TypeError,ValueError):
            raise BankError("BANK_CLOSE_DATE_INVALID") from None
        if old_close>close_date:
            raise BankError("REVIEW_ROLLBACK")
        if old_close.isocalendar()[:2]==close_date.isocalendar()[:2]:
            return WeeklyReview(0,0,(),(),(),{},{},"SKIPPED_ALREADY_REVIEWED"),None
    previous=[x["symbol"] for x in old["companies"]]
    roster=roster_provider()
    old_ids={x['symbol']:x['isin'] for x in old['companies']}
    if any(c.member.symbol in old_ids and c.member.isin!=old_ids[c.member.symbol] for c in roster):
        raise BankError('INCUMBENT_IDENTITY_CHANGED')
    closes=close_provider(close_date,roster)
    result=plan_review(roster,closes,previous=previous,close_date=close_date,now=now,
                       evidence_provider=evidence_provider,max_attempts=max_attempts,deadline_seconds=deadline_seconds,utc_clock=utc_clock,
                       quarter_verifier=quarter_verifier)
    if result.status!="INTERNAL_SECONDARY_STAGING":
        return result,None
    finished=utc_clock()
    if expected_doctor_session(finished)!=close_date:
        raise BankError('SESSION_CHANGED_DURING_REVIEW')
    new_version=bank.replace(result.companies,reviewed_at=finished,close_date=close_date,
                             reviews=result.reviews,expected_version=version,now=finished)
    return result,new_version
