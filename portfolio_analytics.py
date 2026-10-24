"""Evidence-led portfolio calculations. No network, bot, or PDF imports."""
import math
from datetime import date


def number(value):
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def positions(holdings, prices):
    rows = []
    for h in holdings:
        qty, buy = number(h['qty']), number(h['buy'])
        if qty is None or qty <= 0 or buy is None or buy < 0:
            raise ValueError('Holdings require finite positive quantity and nonnegative cost.')
        p = prices.get(h['symbol']) or {}
        ltp = number(p.get('price'))
        if ltp is not None and ltp <= 0:
            ltp = None
        cost = qty * buy
        value = qty * ltp if ltp is not None else None
        rows.append(dict(h, qty=qty, buy=buy, invested=cost, ltp=ltp, value=value,
                         price_date=p.get('date'), priced=value is not None,
                         pnl=value-cost if value is not None else None,
                         pnl_pct=(ltp/buy-1)*100 if ltp is not None and buy > 0 else None,
                         sector='Unclassified', review=None))
    total = sum(r['value'] for r in rows if r['priced'])
    for r in rows:
        r['weight_pct'] = r['value']/total*100 if r['priced'] and total else None
    return rows


def totals(rows):
    priced = [r for r in rows if r['priced']]
    cost = sum(r['invested'] for r in priced)
    value = sum(r['value'] for r in priced)
    all_cost = sum(r['invested'] for r in rows)
    return dict(value=value, cost=cost, all_cost=all_cost, pnl=value-cost,
                pnl_pct=(value/cost-1)*100 if cost else None,
                complete=len(priced)==len(rows), priced=len(priced), total=len(rows),
                priced_cost_pct=cost/all_cost*100 if all_cost else None)


def annual_cagr(years, key, span):
    """Dated, consecutive annual periods with positive endpoints only."""
    from research_data import period_end
    if len(years) < span + 1:
        return None
    rows = years[-(span+1):]
    dates = [period_end(r.get('label','')) for r in rows]
    if any(d is None for d in dates):
        return None
    if any(dates[i].month != dates[i-1].month or dates[i].year-dates[i-1].year != 1 for i in range(1,len(dates))):
        return None
    a,b = number(rows[0].get(key)),number(rows[-1].get(key))
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return round(((b/a)**(1/span)-1)*100,1)


def consecutive_periods(rows, months):
    from research_data import period_end
    dates=[period_end(r.get('label','')) for r in rows]
    return bool(dates) and all(d is not None for d in dates) and all(
        (dates[i].year-dates[i-1].year)*12+dates[i].month-dates[i-1].month == months
        for i in range(1,len(dates)))


def fundamental_view(fv, stm=None, today=None):
    """Descriptive fundamental assessment; technical trend never changes this view.

    Missing lender-specific asset quality and capital metrics deliberately prevent
    a positive lender rating. No generic industrial leverage rules for lenders.
    """
    from research_data import period_end, freshness
    today = today or date.today()
    stm = stm or {}
    years, quarters = stm.get('years') or [], stm.get('quarters') or []
    annual = period_end(years[-1].get('label', '')) if years else None
    q = freshness(quarters, today=today)
    gaps, strengths, risks = [], [], []
    if not annual or not 0 <= (today-annual).days <= 550:
        gaps.append('Latest annual financial period missing, future-dated or over 550 days old')
    latest_q = period_end(quarters[-1].get('label', '')) if quarters else None
    if q['status'] != 'FRESH' or latest_q is None or latest_q > today:
        gaps.append('Quarterly results are missing or behind the reporting calendar')
    lender = bool(fv.get('fin'))
    required = ['roe', 'rev_cagr3', 'pat_cagr3', 'q_pat_yoy'] + ([] if lender else ['roce', 'de', 'ocf_pat'])
    for key in required:
        if number(fv.get(key)) is None:
            gaps.append(key + ' unavailable')
    def val(key):
        return number(fv.get(key))
    if val('roe') is not None:
        if val('roe') >= 15: strengths.append('ROE at least 15%')
        elif val('roe') < 8: risks.append('ROE below 8%')
    if val('pat_cagr3') is not None:
        if val('pat_cagr3') >= 10: strengths.append('Three-year profit CAGR at least 10%')
        elif val('pat_cagr3') < 0: risks.append('Profits shrinking over three years')
    if val('rev_cagr3') is not None and val('rev_cagr3') < 0:
        risks.append('Revenue shrinking over three years')
    if val('q_pat_yoy') is not None and val('q_pat_yoy') <= -15:
        risks.append('Latest-quarter profit fell at least 15% YoY')
    if years and number(years[-1].get('net_profit')) is not None and number(years[-1]['net_profit']) < 0:
        risks.append('Latest annual net profit is negative')
    if not lender:
        if val('roce') is not None and val('roce') >= 15: strengths.append('ROCE at least 15%')
        if val('de') is not None and val('de') > 1.5: risks.append('Debt/equity above 1.5x')
        if val('ocf_pat') is not None:
            if val('ocf_pat') >= .8: strengths.append('Three-year cash conversion at least 0.8x')
            elif val('ocf_pat') < .6: risks.append('Weak three-year cash conversion below 0.6x')
    else:
        gaps.append('Lender review requires GNPA/NNPA, credit costs, ROA and capital adequacy; not verified by this feed')
    if gaps:
        view = 'Insufficient verified information'
    elif risks:
        view = 'Fundamental concerns'
    elif len(strengths) >= 3:
        view = 'Sound fundamentals on available measures'
    else:
        view = 'Mixed fundamentals'
    pe, prem, pb = val('pe'), val('pe_prem'), val('pb')
    valuation = 'Valuation basis unavailable'
    if lender:
        valuation = f'P/B {pb:.2f}x; requires asset-quality and ROE context' if pb is not None and pb > 0 else valuation
    elif pe is not None and pe <= 0:
        valuation = 'Loss-making / nonpositive P/E; P/E comparison not meaningful'
    elif pe is not None and pe > 0:
        valuation = f'P/E {pe:.1f}x; comparable peer valuation unavailable'
        if prem is not None:
            valuation = f'P/E {pe:.1f}x; {prem:+.0f}% versus supplied peer median'
    next_review = 'Verify missing financial data before drawing an investment conclusion' if gaps else (
        'Review earnings, cash flow and leverage concerns at the next results release' if risks else
        'Monitor quarterly growth, cash conversion and valuation; no automatic trade instruction')
    return dict(view=view, valuation=valuation, strengths=strengths, risks=risks, gaps=gaps,
                annual=annual.isoformat() if annual else 'unavailable',
                quarter=q.get('label') or 'unavailable', next_review=next_review,
                lender=lender, source=stm.get('source') or 'unavailable')


def exposure(rows):
    total = sum(r['value'] for r in rows if r['priced'])
    buckets, sectors = {}, {}
    reviewed = 0.0
    for r in rows:
        if not r['priced']:
            continue
        review = r.get('review')
        key = review['view'] if review else 'Not analysed'
        buckets[key] = buckets.get(key, 0) + r['value']
        sectors[r.get('sector') or 'Unclassified'] = sectors.get(r.get('sector') or 'Unclassified', 0) + r['value']
        if review:
            reviewed += r['value']
    pct = lambda d: {k: v/total*100 for k,v in d.items()} if total else {}
    return dict(buckets=pct(buckets), sectors=pct(sectors), analysed_pct=reviewed/total*100 if total else 0)


def format_card(rows):
    from html import escape
    t = totals(rows)
    money = lambda v: f'Rs {v:,.2f}'
    lines = ['💼 <b>PORTFOLIO OVERVIEW</b>', f"Prices: {t['priced']}/{t['total']} holdings",
             ('Total' if t['complete'] else 'Priced subset') + f" value: {money(t['value'])}",
             f"Priced subset cost: {money(t['cost'])} | P&amp;L: {money(t['pnl'])}"]
    if not t['complete']:
        lines.append('⚠️ Totals incomplete. Missing prices are never replaced with purchase cost.')
    lines.append('Weights below are current value within the priced subset.' if not t['complete'] else 'Weights below use current market value.')
    for r in sorted(rows, key=lambda x: -(x['value'] or 0)):
        sym = escape(r['symbol'])
        if not r['priced']:
            lines.append(f'• <b>{sym}</b>: price unavailable; excluded from value/P&amp;L')
        else:
            lines.append(f"• <b>{sym}</b> {r['weight_pct']:.1f}% | {money(r['ltp'])} | P&amp;L {money(r['pnl'])} | {escape(str(r['price_date']))}")
    lines += ['', '📋 /portfolio_review — business quality, growth, cash flow, valuation and exposure PDF.',
              '➕ /buy SYM QTY PRICE  ➖ /sell SYM', 'Educational review; not a personalised allocation recommendation.']
    return '\n'.join(lines)
