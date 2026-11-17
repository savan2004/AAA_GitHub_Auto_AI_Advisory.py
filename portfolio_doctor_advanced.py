"""Advanced Portfolio Doctor calculations: no holdings cache, DB, disk files or logs.

Use only inside the short-lived Doctor worker. Imported public-market data
parsers can return unavailable; never fill missing financial fields with guesses.
Actions are withheld without independent compliance approval.
"""
from collections import defaultdict
from datetime import datetime, date, timezone
import re
from decimal import Decimal, ROUND_FLOOR, InvalidOperation
import math

KNOWN_OPINIONS = ("HOLD", "ADD CANDIDATE", "REDUCE", "EXIT REVIEW", "INSUFFICIENT DATA")


class DoctorEvidenceError(ValueError):
    """Safe data-quality failure without a symbol or customer holding in its text."""


def _d(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DoctorEvidenceError("invalid_numeric_evidence")
    if not d.is_finite():
        raise DoctorEvidenceError("nonfinite_numeric_evidence")
    return d


def _finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def check_basic_totals(recs, expected_count=None):
    """Verify *every* priced holding and preserve exact invested/value/P&L totals."""
    if not recs or (expected_count is not None and len(recs) != expected_count):
        raise DoctorEvidenceError("incomplete_holdings")
    invested = value = Decimal("0")
    for row in recs:
        q, buy, close = (_d(row.get(k)) for k in ("qty", "buy", "close"))
        if min(q, buy, close) <= 0 or not row.get("date"):
            raise DoctorEvidenceError("missing_verified_price")
        calc_cost, calc_value = q * buy, q * close
        if (abs(_d(row.get("invested")) - calc_cost) > Decimal("0.01") or
                abs(_d(row.get("value")) - calc_value) > Decimal("0.01") or
                abs(_d(row.get("pnl")) - (calc_value - calc_cost)) > Decimal("0.01")):
            raise DoctorEvidenceError("inconsistent_price_or_cost")
        invested += calc_cost
        value += calc_value
    if value <= 0 or invested <= 0:
        raise DoctorEvidenceError("invalid_portfolio_value")
    for row in recs:
        expected = Decimal("100") * _d(row["value"]) / value
        if abs(_d(row.get("weight")) - expected) > Decimal("0.11"):
            raise DoctorEvidenceError("inconsistent_portfolio_weight")
    return {"invested": float(invested), "value": float(value),
            "pnl": float(value - invested),
            "pnl_pct": float(100 * (value - invested) / invested),
            "covered": len(recs), "expected": expected_count or len(recs)}


def _normalize_sector(label):
    raw = str(label or "").strip()
    if not raw or raw.lower() in ("others", "unknown", "not analysed", "other / unclassified") or "others" in raw.lower():
        return "Unknown / unclassified"
    return raw


def sector_allocations(recs, resolve=None):
    """Use all current-value weights; unknown sectors stay visible in totals."""
    total = _d(check_basic_totals(recs)["value"])
    if resolve is None:
        from nifty500_collector import get_stock_sector
        resolve = get_stock_sector
    weights = defaultdict(Decimal)
    for row in recs:
        try:
            label = resolve(row["symbol"])
        except Exception:
            label = None
        weights[_normalize_sector(label)] += _d(row["value"]) * 100 / total
    result = dict(sorted(((sector, float(amt)) for sector, amt in weights.items()),
                         key=lambda item: (-item[1], item[0])))
    if abs(sum(result.values()) - 100) > 0.01:
        raise DoctorEvidenceError("sector_total_not_100")
    return result


def _dated_annual_rows(rows, *, as_of=None):
    from research_data import period_end
    as_of = as_of or datetime.now(timezone.utc).date()
    result = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        parsed = period_end(str(item.get("label") or ""))
        if parsed is not None and parsed <= as_of:
            result.append((parsed, item))
    result.sort(key=lambda pair: pair[0])
    dates = [day for day, _ in result]
    if len(set(dates)) != len(dates):
        raise DoctorEvidenceError("duplicate_annual_periods")
    return result


def _cagr(earlier, later, years):
    a, b = _finite(earlier), _finite(later)
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    growth = 100 * ((b / a) ** (1 / years) - 1)
    return round(growth, 2) if math.isfinite(growth) and abs(growth) <= 1000 else None


def normalize_fundamentals(symbol, sector, extras, ratio_values=None, *, today=None):
    """Dated annual/quarterly evidence; no inferred consolidation or TTM basis."""
    from research_data import freshness
    from industry import classify
    today = today or datetime.now(timezone.utc).date()
    extras = extras or {}
    ratios = ratio_values or {}
    evidence_scope = extras.get("_statement_scope") if extras.get("_statement_scope") in ("consolidated", "standalone") else "unverified"
    if evidence_scope != "unverified" and ratios.get("_screener_scope") != evidence_scope:
        ratios = {}  # Ratios from another scope must not be mixed with statements.
    fetched_at = extras.get("_retrieved_utc") if extras.get("_source") == "Screener.in" else None
    statement_source = "Screener.in (secondary)" if extras.get("_source") == "Screener.in" else "Unverified secondary financial data"
    annual = _dated_annual_rows(extras.get("years"), as_of=today)
    annual = [(day, row) for day, row in annual if day <= today]
    quarter_status = freshness(extras.get("quarters") or [], today=today)
    kind = classify(sector_label=_normalize_sector(sector),
                    statements={"layout": extras.get("layout")})
    is_lender = bool(kind.get("is_lender")) or "bank" in sector.lower()
    p = annual[-1][1] if annual else {}
    latest_year = annual[-1][0].isoformat() if annual else "unavailable"
    three_y = None
    if len(annual) >= 4:
        start_day, old = annual[-4]
        days = (annual[-1][0] - start_day).days
        if 2.5 * 365.25 <= days <= 3.6 * 365.25:
            three_y = (old, days / 365.25)
    def metric(value, period, basis="FY", unit=""):
        val = _finite(value)
        return {"value": val, "source": statement_source if val is not None else "Unavailable",
                "period": period, "basis": basis, "scope": evidence_scope,
                "retrieved_utc": fetched_at, "unit": unit}
    metrics = {
        "revenue": metric(p.get("revenue"), latest_year, unit="INR Cr"),
        "net_profit": metric(p.get("net_profit"), latest_year, unit="INR Cr"),
        "operating_margin": metric(p.get("opm") if not is_lender else None,
                                   latest_year, unit="%"),
        "roe": metric(ratios.get("roe"), "not supplied", basis="vendor ratio", unit="%"),
        "roce": metric(p.get("roce") if not is_lender else None, latest_year, unit="%"),
        "operating_cash_flow": metric(p.get("ocf") if not is_lender else None,
                                      latest_year, unit="INR Cr"),
        "debt_equity": metric(None if is_lender else
                               (float(p["borrowings"]) / float(p["networth"])
                                if _finite(p.get("borrowings")) is not None and
                                   _finite(p.get("networth")) and float(p["networth"]) > 0
                                else None),
                              latest_year, unit="x"),
        "pe": metric(ratios.get("pe"), "not supplied", basis="vendor ratio", unit="x"),
        "pb": metric(ratios.get("pb"), "not supplied", basis="vendor ratio", unit="x"),
        "revenue_cagr3": metric(_cagr(three_y[0].get("revenue"),
                                       p.get("revenue"), three_y[1]) if three_y else None,
                                latest_year, basis="3-year CAGR", unit="%"),
        "profit_cagr3": metric(_cagr(three_y[0].get("net_profit"),
                                      p.get("net_profit"), three_y[1]) if three_y else None,
                               latest_year, basis="3-year CAGR", unit="%"),
    }
    # FCF and interest cover aren't in the validated Screener annual model.
    metrics["free_cash_flow"] = metric(None, latest_year, unit="INR Cr")
    metrics["interest_coverage"] = metric(None, latest_year, unit="x")
    applicable = ["revenue", "net_profit", "roe", "pe", "pb"] if is_lender else [
        "revenue", "net_profit", "operating_margin", "roe", "roce", "debt_equity",
        "operating_cash_flow", "pe"]
    available = sum(metrics[k]["value"] is not None for k in applicable)
    status = ("PARTIAL" if available else "MISSING")
    return {"symbol": symbol, "sector": _normalize_sector(sector),
            "is_lender": is_lender, "status": status,
            "freshness": quarter_status.get("status", "MISSING"),
            "annual_period": latest_year,
            "quarter_period": str(quarter_status.get("latest") or "unavailable"),
            "scope": evidence_scope, "source": statement_source,
            "retrieved_utc": fetched_at, "issuer_filing_verified": False,
            "available": available, "required": len(applicable), "metrics": metrics,
            "basis_warning": "Issuer filings not authenticated; vendor ratios have undated periods. No decisive opinions."}


def _ratio_values_from_html(html, scope):
    """Reuse Screener ratio format but read it from the *same* statement page."""
    from fundamentals import _parse_num
    from html import unescape
    fields = {}
    # Bound extraction to the top-ratios section so another occurrence of ROE
    # elsewhere in the document cannot be mislabelled as a current ratio.
    section = re.search(r'<ul[^>]*id=["\']top-ratios["\'][^>]*>(.*?)</ul>',
                        html, flags=re.I | re.S)
    if section is None:
        return {"_screener_scope": scope}
    blocks = re.findall(
        r'<li[^>]*>.*?<span[^>]*class=["\'][^"\']*name[^"\']*["\'][^>]*>(.*?)</span>'
        r'.*?<span[^>]*>(.*?)</span>', section.group(1), re.I | re.S)
    for name, val in blocks:
        key = unescape(re.sub(r"<[^>]+>", "", name)).strip().lower()
        value = _parse_num(unescape(re.sub(r"<[^>]+>", "", val)))
        if value is None or not math.isfinite(value):
            continue
        if key in ("roe", "return on equity") and -1000 < value < 1000:
            fields["roe"] = value
        elif key in ("stock p/e", "pe", "p/e") and 0 < value < 500:
            fields["pe"] = value
        elif key in ("price to book", "p/b") and 0 < value < 100:
            fields["pb"] = value
    fields["_screener_scope"] = scope
    return fields


def _screener_same_page_evidence(symbol, *, get=None):
    """One bounded public issuer-page fetch, no shared cache or customer details."""
    import requests
    from research_data import (
        _HEADERS, parse_years, parse_quarters, detect_layout,
    )
    if not re.fullmatch(r"[A-Z0-9&-]{2,20}", str(symbol).upper()):
        return {}, {}
    get = get or requests.get
    for scope, suffix in (("consolidated", "/consolidated/"),
                          ("standalone", "/")):
        url = "https://www.screener.in/company/" + symbol.upper() + suffix
        try:
            resp = get(url, headers=_HEADERS, timeout=9)
            if not getattr(resp, "ok", False) or "data-table" not in resp.text:
                continue
            annual = parse_years(resp.text)
            quarterly = parse_quarters(resp.text)
            if not (annual or quarterly):
                continue
            evidence = {
                "years": annual, "quarters": quarterly,
                "layout": detect_layout(resp.text),
                "_source": "Screener.in", "_statement_scope": scope,
                "_retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            return evidence, _ratio_values_from_html(resp.text, scope)
        except (requests.RequestException, ValueError, TypeError, KeyError):
            continue
    return {}, {}


def collect_fundamentals_no_holdings_cache(symbol, sector, collector=None, ratio_collector=None):
    """Disposable worker fetch; same-page fundamentals cannot mix reporting bases.

    In a live request, the statement tables and key ratios are extracted from
    the same public HTML response. No shared issuer/holdings cache is touched.
    Injected collectors are for isolated unit tests only.
    """
    if collector is None and ratio_collector is None:
        extras, ratios = _screener_same_page_evidence(symbol)
    else:
        try:
            extras = (collector(symbol) if collector else {}) or {}
        except Exception:
            extras = {}
        try:
            ratios = (ratio_collector(symbol) if ratio_collector else {}) or {}
        except Exception:
            ratios = {}
    return normalize_fundamentals(symbol, sector, extras, ratios)


def aligned_relative_return(stock_frame, bench_frame, sessions=21):
    """Compare identical endpoints after joining dated closes; no guessed dates."""
    if bench_frame is None or stock_frame is None or bench_frame.empty or stock_frame.empty:
        return None
    import pandas as pd
    try:
        joined = pd.concat(
            [stock_frame["Close"].rename("stock"), bench_frame["Close"].rename("benchmark")],
            axis=1, join="inner").dropna()
        if (len(joined) < sessions + 1 or not joined.index.is_monotonic_increasing or
                joined.index[-1] != stock_frame.index[-1] or
                joined.index[-1] != bench_frame.index[-1]):
            return None
        frame = joined.iloc[-sessions - 1:]
        if any(float(v) <= 0 for v in frame[["stock", "benchmark"]].to_numpy().flat):
            return None
        s = 100 * (float(frame["stock"].iloc[-1]) / float(frame["stock"].iloc[0]) - 1)
        b = 100 * (float(frame["benchmark"].iloc[-1]) / float(frame["benchmark"].iloc[0]) - 1)
        return {"sessions": sessions, "stock_pct": round(s, 2), "nifty_pct": round(b, 2),
                "relative_pp": round(s - b, 2),
                "end": str(frame.index[-1].date())}
    except (TypeError, ValueError, AttributeError, KeyError, IndexError):
        return None


def evaluate_opinion(fund, technical, portfolio_weight, *,
                     corporate_actions_verified=False, compliance_approved=False):
    """Not an order or prediction. Gate all meaningful review opinions."""
    missing = []
    if not compliance_approved:
        missing.append("regulatory_compliance_approval")
    if not corporate_actions_verified:
        missing.append("corporate_action_cost_basis_verification")
    if not isinstance(fund, dict) or fund.get("status") != "COMPLETE" or not fund.get("issuer_filing_verified"):
        missing.append("audited_comparable_fundamentals")
    if not isinstance(technical, dict) or technical.get("trend") is None:
        missing.append("validated_technical_trend")
    weight = _finite(portfolio_weight)
    if weight is None or not 0 <= weight <= 100:
        missing.append("portfolio_allocation")
    if missing:
        return {"opinion": "INSUFFICIENT DATA", "evidence": [],
                "conditions": missing, "limitation": "No personalised action permitted"}
    trend = str(technical["trend"])
    high_risk = bool(fund.get("material_risk_verified"))
    deteriorating = bool(fund.get("earnings_declining_verified"))
    if high_risk and deteriorating and "DOWNTREND" in trend:
        label = "EXIT REVIEW"
    elif weight > 40 and (high_risk or deteriorating):
        label = "REDUCE"
    elif trend in ("UPTREND", "STRONG UPTREND") and bool(fund.get("valuation_attractive_verified")) and weight < 20:
        label = "ADD CANDIDATE"
    else:
        label = "HOLD"
    return {"opinion": label,
            "evidence": [str(v) for v in fund.get("verified_evidence", [])[:4]],
            "conditions": ["human review required", "not an order"],
            "limitation": "Conditional research, not assured accuracy or an investment instruction"}


def compare_replacements(holding, candidates, *, same_period_required=True):
    """Never turn a single price/RSI signal into a stock swap."""
    chosen = []
    for cand in candidates or []:
        if cand.get("symbol") == holding.get("symbol"):
            continue
        fields = ("fundamental_verified", "valuation_verified", "technical_verified",
                  "liquid_verified", "sector_verified", "period_comparable")
        if any(cand.get(k) is not True for k in fields):
            continue
        if not all(_finite(cand.get(k)) is not None for k in ("quality", "valuation", "technical")):
            continue
        if cand.get("diversification_improvement") is not True:
            continue
        chosen.append({"symbol": cand["symbol"], "quality": cand["quality"],
                       "valuation": cand["valuation"], "technical": cand["technical"],
                       "sector": cand.get("sector"), "liquidity": "verified",
                       "status": "COMPARISON ONLY; compliance approval required"})
    return {"status": "COMPARABLE" if chosen else "NO SUITABLE REPLACEMENT FOUND",
            "candidates": chosen[:3], "automatic_swap": False}


def build_portfolio_scenario(*, capital, horizon_months, risk_tolerance,
                             liquidity_needed, stock_cap_pct, sector_cap_pct,
                             candidates, excluded=(), current_values=None,
                             current_sectors=None, funding_mode="additional"):
    """Hypothetical whole-share allocation including cash. No trade or persistence.

    additional: current holdings are retained, capital added to current wealth.
    rebalance: capital is the TOTAL hypothetical model value, not extra funds.
    """
    if funding_mode not in ("additional", "rebalance") or risk_tolerance not in ("low", "medium", "high"):
        raise DoctorEvidenceError("invalid_builder_profile")
    budget = _d(capital)
    try:
        horizon = int(horizon_months)
    except (ValueError, TypeError, OverflowError):
        raise DoctorEvidenceError("invalid_builder_capital_or_horizon")
    if budget <= 0 or budget > Decimal("1e12") or not 1 <= horizon <= 600:
        raise DoctorEvidenceError("invalid_builder_capital_or_horizon")
    need = _d(liquidity_needed)
    max_stock, max_sector = _d(stock_cap_pct), _d(sector_cap_pct)
    if need < 0 or need > budget or not 0 < max_stock <= 100 or not 0 < max_sector <= 100:
        raise DoctorEvidenceError("invalid_builder_constraints")
    prior = {str(k).upper(): _d(v) for k, v in (current_values or {}).items()}
    if any(v < 0 for v in prior.values()):
        raise DoctorEvidenceError("invalid_existing_holdings")
    current_total = sum(prior.values(), Decimal("0"))
    prior_total = current_total if funding_mode == "additional" else Decimal("0")
    full = budget + prior_total
    excluded = {str(x).upper() for x in excluded}
    seen = set()
    total_target = Decimal("0")
    rows, sectors = [], defaultdict(Decimal)
    spending = Decimal("0")
    requested = list(candidates or [])
    for row in requested:
        sym = str(row.get("symbol") or "").upper()
        if (not sym or sym in seen or sym in excluded or
                not bool(row.get("fresh_price_verified"))):
            raise DoctorEvidenceError("invalid_or_duplicate_builder_candidate")
        seen.add(sym)
        price, target = _d(row.get("price")), _d(row.get("target_weight_pct"))
        if price <= 0 or target < 0 or target > max_stock:
            raise DoctorEvidenceError("invalid_builder_target")
        total_target += target
        sector = _normalize_sector(row.get("sector"))
        # Whole shares, never fractional quantity or silently borrowed capital.
        to_spend = (budget * target / 100)
        shares = int((to_spend / price).to_integral_value(rounding=ROUND_FLOOR))
        amount = price * shares
        spending += amount
        sectors[sector] += amount
        rows.append({"symbol": sym, "shares": shares, "price": float(price),
                     "new_value": float(amount), "sector": sector,
                     "requested_weight_pct": float(target)})
    if total_target > 100 or spending + need > budget:
        raise DoctorEvidenceError("builder_cash_or_weight_shortfall")
    cash = budget - spending
    if cash < need:
        raise DoctorEvidenceError("builder_liquidity_shortfall")
    for row in rows:
        after_value = prior.get(row["symbol"], Decimal("0")) + _d(row["new_value"])
        if 100 * after_value / full > max_stock + Decimal("0.00000001"):
            raise DoctorEvidenceError("builder_stock_cap_exceeded")
        row["proposed_total_weight_pct"] = float(100 * after_value / full)
    if funding_mode == "additional" and prior:
        # Previous holdings are not assumed diversified without verified labels.
        current_sectors = current_sectors or {}
        if set(prior) - set(current_sectors):
            raise DoctorEvidenceError("existing_sector_breakdown_required")
        for sym, value in prior.items():
            if 100 * value / full > max_stock + Decimal("0.00000001"):
                raise DoctorEvidenceError("existing_stock_cap_exceeded")
            sector = _normalize_sector(current_sectors[sym])
            if sector == "Unknown / unclassified":
                raise DoctorEvidenceError("existing_sector_breakdown_required")
            sectors[sector] += value
    for value in sectors.values():
        if 100 * value / full > max_sector + Decimal("0.00000001"):
            raise DoctorEvidenceError("builder_sector_cap_exceeded")
    current = {k: float(100 * v / current_total) for k, v in prior.items()} if current_total else {}
    return {"mode": funding_mode, "current_weights": current,
            "proposed": rows, "cash": float(cash), "cash_weight_pct": float(100 * cash / full),
            "planned_new_capital": float(budget),
            "portfolio_value_after": float(full),
            "total_weights_pct": float(100 * ((spending + cash + prior_total) / full)),
            "uninvested_from_rounding_included": True,
            "no_automatic_trades": True, "warnings": ["Hypothetical only; no order placed"]}


def assemble_review(recs, price_frames, *, fund_collector=None, ratio_collector=None,
                    sector_lookup=None, benchmark=None, advanced_fundamentals=True):
    """Build complete 1..5-holding review in a disposable worker, no holdings cache."""
    totals = check_basic_totals(recs)
    sectors = sector_allocations(recs, resolve=sector_lookup)
    from research_engine import compute_technicals
    fund_by_symbol = []
    tech_by_symbol = []
    for row in recs:
        sym = row["symbol"]
        try:
            sector_name = (sector_lookup(sym) if sector_lookup else
                           __import__("nifty500_collector").get_stock_sector(sym))
        except Exception:
            sector_name = None
        sector = _normalize_sector(sector_name)
        if advanced_fundamentals:
            fund = collect_fundamentals_no_holdings_cache(
                sym, sector, collector=fund_collector, ratio_collector=ratio_collector)
        else:
            fund = normalize_fundamentals(sym, sector, {}, {})
        fund_by_symbol.append(fund)
        frame = price_frames.get(sym)
        tech = {"symbol": sym, "trend": None, "rsi14": row.get("rsi14"),
                "ema20": row.get("ema20"), "ema50": row.get("ema50"),
                "support": row.get("low20"), "resistance": row.get("high20"),
                "volume_ratio": None, "relative_1m": None}
        if frame is not None and len(frame) >= 60:
            try:
                t = compute_technicals(frame, None)
                tech.update(trend=t.get("trend"), rsi14=t.get("rsi"),
                            ema20=t.get("ema20"), ema50=t.get("ema50"),
                            support=(t.get("supports") or [{"price": None}])[0].get("price"),
                            resistance=(t.get("resistances") or [{"price": None}])[0].get("price"),
                            volume_ratio=t.get("vol_ratio"),
                            relative_1m=aligned_relative_return(frame, benchmark))
            except (ValueError, IndexError, KeyError, TypeError, OverflowError):
                pass
        tech_by_symbol.append(tech)
    return {"totals": totals, "sectors": sectors, "holdings": recs,
            "fundamentals": fund_by_symbol, "technicals": tech_by_symbol,
            "opinions": [evaluate_opinion(f, t, r["weight"]) for f, t, r in
                         zip(fund_by_symbol, tech_by_symbol, recs)],
            "replacements": {"status": "NO SUITABLE REPLACEMENT FOUND", "candidates": [],
                             "automatic_swap": False},
            "builder": None,
            "quality": {"holdings_covered": len(recs), "holdings_expected": len(recs),
                        "financial_source_scope": "UNVERIFIED",
                        "corporate_action_basis": "NOT VERIFIED"}}
