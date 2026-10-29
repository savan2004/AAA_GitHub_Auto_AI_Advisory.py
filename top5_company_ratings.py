"""Fundamental evidence snapshots for market-cap Top-5 advisory cards.

Rating remains explicitly provisional if EPS/annual statements/quarterly
freshness cannot be verified. Missing values are NEVER substituted by zero.
"""
import math
from datetime import datetime
from investment_screener import IST
from top5_fundamental_gate import assess_fundamentals, _cagr, _annual_periods, _numeric
import logging

LOG = logging.getLogger(__name__)


def _as_display(n, nd=1):
    val = _numeric(n)
    return round(val, nd) if val is not None else None


def _preliminary_score(roe, pe, debt_equity, profit_growth, sales_growth):
    """Only score when at least THREE known factors; disclose coverage (not audited)."""
    factors = [
        (min(100,max(0,(roe-5)*4)) if roe is not None else None),
        (min(100,max(0,100-pe*1.6)) if pe is not None and pe>0 else None),
        (min(100,max(0,100-debt_equity*65)) if debt_equity is not None else None),
        (min(100,max(0,profit_growth*3)) if profit_growth is not None else None),
        (min(100,max(0,sales_growth*3)) if sales_growth is not None else None),
    ]
    valid = [x for x in factors if x is not None and math.isfinite(x)]
    return (round(sum(valid)/len(valid)), len(valid)) if len(valid)>=3 else (None,len(valid))


def rate_financial_evidence(profile, fundamentals, stm, industry, today):
    """Expose known ratios, separate complete validation from incomplete research."""
    fv, st = fundamentals or {}, stm or {}
    roe, pe, eps = [_numeric(fv.get(x)) for x in ("roe","pe","eps")]
    pb = _numeric(fv.get("pb"))
    annual = _annual_periods(st, today)
    profit_growth = sales_growth = debt_equity = None
    if len(annual)>=4:
        d0,old = annual[-4]
        d1,new = annual[-1]
        years=(d1-d0).days/365.25
        profit_growth=_cagr(old.get("net_profit"),new.get("net_profit"),years)
        sales_growth=_cagr(old.get("revenue"),new.get("revenue"),years)
        debt,networth = _numeric(new.get("borrowings")),_numeric(new.get("networth"))
        if debt is not None and debt>=0 and networth is not None and networth>0:
            debt_equity=debt/networth
    validated, why=assess_fundamentals(profile,fv,st,industry,today)
    if validated:
        return dict(validated, fund_status="VALIDATED", fund_reason="qualified",
                    fund_known=5, fund_source=st.get("source") or "Public financial data")
    from top5_fundamental_gate import _is_financial
    if _is_financial(industry):
        # Corporate D/E does not measure solvency of banks; omit misleading proxy.
        debt_equity=None
    from top5_fundamental_gate import POLICY
    limits=POLICY[profile]
    latest_profit=_numeric(annual[-1][1].get("net_profit")) if annual else None
    measured_failure = (
        (roe is not None and (roe < limits["min_roe"] or roe > 150))
        or (pe is not None and (pe <= 0 or pe > limits["max_pe"]))
        or (eps is not None and eps <= 0)
        or (latest_profit is not None and latest_profit <= 0)
        or (profit_growth is not None and profit_growth < limits["min_profit_cagr"])
        or (debt_equity is not None and debt_equity > limits["max_de"])
        or (not _is_financial(industry) and sales_growth is not None
            and sales_growth < limits["min_sales_cagr"])
        or (_is_financial(industry) and pb is not None and (pb <= 0 or pb > 6))
    )
    preliminary,known=_preliminary_score(roe,pe,debt_equity,profit_growth,sales_growth)
    status=("REJECTED" if measured_failure else
            "PRELIMINARY" if preliminary is not None else "UNVERIFIED")
    return {
        "fund_score":preliminary if not measured_failure else None,
        "fund_status":status,
        "fund_reason":why, "fund_known":known,
        "fund_source":st.get("source") or "Public financial data (incomplete)",
        "roe":_as_display(roe),"pe":_as_display(pe),"pb":_as_display(pb,2),
        "de":_as_display(debt_equity,2),
        "eps":_as_display(eps,2),
        "profit_growth":_as_display(profit_growth),
        "sales_growth":_as_display(sales_growth),
        "quarter":(str(st.get("freshness",{}).get("latest") or "unverified")),
        "fin":_is_financial(industry)
    }


def fetch_company_evidence(symbol, industry, profile, today=None):
    """Use pre-existing public market data collectors. Failure is explicit."""
    today=today or datetime.now(IST).date()
    try:
        from fundamentals import get_fundamentals
        from research_data import get_statements
        fv=get_fundamentals(symbol) or {}
        stm=get_statements(symbol) or {}
        return rate_financial_evidence(profile,fv,stm,industry,today)
    except Exception as exc:
        LOG.warning("MSK Top5 company evidence %s unavailable (%s)",symbol,type(exc).__name__)
        return {
            "fund_score":None, "fund_status":"UNVERIFIED",
            "fund_reason":"provider_unavailable", "fund_known":0,
            "fund_source":"unavailable",
            "roe":None,"pe":None,"pb":None,"de":None,"eps":None,
            "profit_growth":None,"sales_growth":None,"quarter":"unverified","fin":False,
        }
