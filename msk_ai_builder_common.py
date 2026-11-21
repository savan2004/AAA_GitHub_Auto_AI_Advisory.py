"""Shared Top5 calculation adapters, without trade triggers or customer exposure.

The Top5 functions validate their own heuristic rules, not filing authenticity.
Weekly bank records remain secondary-only; this adapter adds research evidence,
never a recommendation or an investment suitability determination.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
from msk_ai_builder_evidence import EvidenceError, normalize_secondary, _exact_issuer_get

def _num(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):
        return None

def financial_review(company,*,now,collector=None,clock=None):
    from portfolio_doctor_advanced import _screener_same_page_evidence
    from top5_company_ratings import rate_financial_evidence
    if collector is None:
        def collector(symbol):
            return _screener_same_page_evidence(symbol,get=_exact_issuer_get,request_timeout=9)
    try:
        raw,ratios=collector(company.member.symbol)
        now=clock() if clock else now
        secondary=normalize_secondary(company.member.symbol,raw,now=now)
        old=company.evidence
        if (secondary.scope,secondary.annual,secondary.quarterly)!=(old.scope,old.annual,old.quarterly):
            raise EvidenceError("STATEMENTS_CHANGED_DURING_REVIEW")
        if not isinstance(ratios,dict) or ratios.get("_screener_scope")!=secondary.scope:
            raise EvidenceError("RATIO_SCOPE_UNVERIFIED")
        # Same-page ratios are explicitly provider-reported; no claim of
        # alignment to the exchange closing-price session.
        stm={"years":raw["years"],"quarters":raw["quarters"],"source":secondary.source_url}
        assessments={}
        for profile in ("conservative","moderate","aggressive"):
            outcome=rate_financial_evidence(profile,ratios,stm,company.sector,now.date())
            assessments[profile]={"status":outcome["fund_status"],"reason":outcome["fund_reason"],
                "score":_num(outcome.get("fund_score"))}
        return {"status":"SECONDARY_ONLY","source":secondary.source_url,"scope":secondary.scope,
                "ratio_basis":"PROVIDER_REPORTED_NOT_PRICE_DATE_RECONCILED",
                "roe":_num(ratios.get("roe")),"pe":_num(ratios.get("pe")),"pb":_num(ratios.get("pb")),
                "profiles":assessments}
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceError("FINANCIAL_REVIEW_UNAVAILABLE") from exc

def technical_review(company,frame,*,now):
    from top5_technical_ratings import technical_candidate
    if frame is None or frame.empty:
        raise EvidenceError("HISTORY_UNAVAILABLE")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise EvidenceError("HISTORY_ORDER_INVALID")
    result=technical_candidate(company.member.symbol,frame,"moderate",now)
    if not result or result.get("asof")!=company.close_date.isoformat():
        raise EvidenceError("HISTORY_DATE_UNVERIFIED")
    try:
        observed=Decimal(str(result["ltp"]))
        expected=Decimal(str(company.close_rupees))
        if not observed.is_finite() or not expected.is_finite() or expected<=0 or abs(observed-expected)>Decimal("0.02"):
            raise EvidenceError("HISTORY_CLOSE_MISMATCH")
    except (InvalidOperation,KeyError,ValueError) as exc:
        raise EvidenceError("HISTORY_CLOSE_MISMATCH") from exc
    # Exclude entry, stop, targets, and trade signals from investment research.
    return {"status":"EOD_CLOSE_MATCHED_HISTORY_NOT_INDEPENDENTLY_VERIFIED",
            "asof":result["asof"],"technical_score":result["tech_score"],
            "rsi":result["rsi"],"adx":result["adx"],"atr_pct":result["atr_pct"],
            "trend_confirmed":result["trend_confirmed"]}

def review_companies(companies,frames,*,now,collector=None,clock=None):
    """Stored candidate universe only, no ranking promotion or forced passes."""
    from datetime import timezone
    if len(companies)>20 or len({c.member.symbol for c in companies})!=len(companies):
        raise EvidenceError("REVIEW_COMPANY_COUNT")
    rows={}
    for c in companies:
        row={}
        try:
            row["financial"]=financial_review(c,now=now,collector=collector,clock=clock)
        except EvidenceError as exc:
            row["financial"]={"status":"WITHHELD","reason":str(exc)}
        try:
            row["technical"]=technical_review(c,frames.get(c.member.symbol),now=now)
        except EvidenceError as exc:
            row["technical"]={"status":"WITHHELD","reason":str(exc)}
        rows[c.member.symbol]=row
    return {"publication_status":"WITHHELD","companies":rows}
