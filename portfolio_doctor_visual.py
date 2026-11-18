"""Four-page, evidence-bounded private Portfolio Doctor PDF. RAM only."""
import io
from datetime import datetime, timedelta, timezone

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from portfolio_doctor_diagnostics import diagnose_portfolio, industry_exposure

IST = timezone(timedelta(hours=5, minutes=30))
NAVY = HexColor('#132338')
BLUE = HexColor('#1769A9')
INK = HexColor('#233346')
MUTED = HexColor('#637489')
PANEL = HexColor('#F1F5F9')
BORDER = HexColor('#DDE5ED')
GREEN = HexColor('#168257')
RED = HexColor('#BD4040')
WHITE = HexColor('#FFFFFF')


def render_pdf(holdings, peers=None, risk=None, companies=None, max_bytes=5 * 1024 * 1024):
    """Use already validated records; never fetch market data or persist files."""
    peers = peers or {}
    risk = risk if isinstance(risk, dict) else None
    has_risk = bool(risk and risk.get("status") == "VERIFIED" and
                    len(risk.get("chart_portfolio") or []) >= 65)
    # Only currently submitted verified symbols may produce company pages.
    held = {row["symbol"] for row in holdings}
    companies = [row for row in (companies or []) if isinstance(row, dict)
                 and row.get("symbol") in held][:5]
    total_pages = (5 if has_risk else 4) + len(companies)
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4, pageCompression=1)
    w, h = A4
    left, right = 42, w - 42
    y, page_no = 0, 0

    def text_fit(text, x, baseline, width, size=10, bold=False, color=INK):
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        s = str(text).replace('\n', ' ').replace('\r', ' ')
        current = float(size)
        while current >= 6.5 and stringWidth(s, font, current) > width:
            current -= 0.5
        pdf.setFillColor(color)
        pdf.setFont(font, current)
        if stringWidth(s, font, current) <= width:
            pdf.drawString(x, baseline, s)
        else:
            # Cards abbreviate only the decorative number; full totals below.
            ellipsis = '...'
            while s and stringWidth(s + ellipsis, font, current) > width:
                s = s[:-1]
            pdf.drawString(x, baseline, s + ellipsis)

    def footer():
        pdf.setStrokeColor(BORDER)
        pdf.line(left, 62, right, 62)
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 7)
        pdf.drawString(left, 46, 'PRIVATE | Historical closes | Educational research | No automatic orders')
        pdf.drawRightString(right, 46, 'Page {} of {}'.format(page_no, total_pages))

    def new_page(title, desc):
        nonlocal y, page_no
        if page_no:
            footer()
            pdf.showPage()
        page_no += 1
        pdf.setFillColor(NAVY)
        pdf.roundRect(33, h - 114, w - 66, 78, 9, stroke=0, fill=1)
        text_fit('MSK | PORTFOLIO DOCTOR', left + 5, h - 68, w - 112, 17, True, WHITE)
        text_fit(title.upper(), left + 5, h - 92, w - 112, 10, True, HexColor('#90D7FF'))
        y = h - 140
        line(desc, 8, color=MUTED, leading=14)
        y -= 10

    def line(value, size=9, bold=False, leading=15, color=INK):
        nonlocal y
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        raw = str(value).replace('\n', ' ').replace('\r', ' ').strip()
        width = right - left
        for item in simpleSplit(raw, font, size, width) or ['']:
            if y < 74:
                # No silent content truncation: long narratives start a new page,
                # although normal 1-5 stock reports stay four pages.
                new_page('CONTINUED', 'Remaining verified portfolio content')
            pdf.setFillColor(color)
            pdf.setFont(font, size)
            pdf.drawString(left, y, item)
            y -= leading

    def subhead(value):
        nonlocal y
        y -= 7
        pdf.setFillColor(BLUE)
        pdf.roundRect(left, y - 6, 4, 17, 2, stroke=0, fill=1)
        text_fit(value, left + 12, y, right - left - 14, 11, True, NAVY)
        y -= 23

    def money(value):
        return 'Rs {:,.2f}'.format(value)

    def compact_money(value):
        n = float(value)
        if abs(n) >= 10_000_000:
            return 'Rs {:.2f} Cr'.format(n / 10_000_000)
        if abs(n) >= 100_000:
            return 'Rs {:.2f} L'.format(n / 100_000)
        return money(value)

    def draw_historical_chart(details):
        nonlocal y
        values = details['chart_portfolio']
        benchmark = details.get('chart_nifty')
        days = details['chart_dates']
        if len(values) < 2 or len(values) != len(days):
            return
        if benchmark is not None and len(benchmark) != len(values):
            benchmark = None
        all_values = values + (benchmark or [])
        low = min(all_values) - 4
        high = max(all_values) + 4
        if high <= low:
            high = low + 1
        x0, x1 = left + 40, right - 15
        top_y, bot_y = y - 16, y - 153
        pdf.setStrokeColor(BORDER)
        pdf.setLineWidth(0.6)
        for j in range(4):
            yy = bot_y + (top_y - bot_y) * j / 3
            pdf.line(x0, yy, x1, yy)
            text_fit('{:.0f}'.format(low + (high - low) * j / 3),
                     left, yy - 2, 36, 7.5, color=MUTED)
        def trace(points, color):
            pdf.setStrokeColor(color)
            pdf.setLineWidth(1.7)
            previous = None
            for i, value in enumerate(points):
                px = x0 + (x1 - x0) * i / (len(points) - 1)
                py = bot_y + (top_y - bot_y) * (value - low) / (high - low)
                if previous is not None:
                    pdf.line(previous[0], previous[1], px, py)
                previous = (px, py)
        trace(values, BLUE)
        if benchmark is not None:
            trace(benchmark, MUTED)
        text_fit(days[0], x0, bot_y - 16, 95, 7, color=MUTED)
        text_fit(days[-1], x1 - 95, bot_y - 16, 95, 7, color=MUTED)
        text_fit("Current holdings (rebased 100)", x0 + 6, top_y + 8, 190, 8, True, BLUE)
        if benchmark is not None:
            text_fit('Nifty 50 (same dates)', x1 - 150, top_y + 8,
                     150, 8, True, MUTED)
        y = bot_y - 35

    def company_price_chart(details):
        nonlocal y
        dates = details.get('chart_dates') or []
        rows = [(details.get('chart') or [], BLUE),
                (details.get('chart_ema20') or [], HexColor('#CB8B22')),
                (details.get('chart_ema50') or [], HexColor('#8066AE'))]
        valid = [(series, color) for series, color in rows
                 if len(series) == len(dates) and len(series) >= 2]
        if not valid:
            line('Chart unavailable: insufficient dated historical candles.', 8)
            return
        points = [float(v) for series, _ in valid for v in series]
        low, high = min(points), max(points)
        gap = max(0.01, (high-low)*.08)
        low -= gap
        high += gap
        x0, x1 = left+48, right-10
        ytop, ybottom = y-35, y-169
        for gridline in range(4):
            yy = ybottom + (ytop-ybottom)*gridline/3
            pdf.setStrokeColor(BORDER)
            pdf.setLineWidth(.55)
            pdf.line(x0, yy, x1, yy)
            text_fit('{:,.0f}'.format(low+(high-low)*gridline/3),
                     left, yy-3, 43, 7, color=MUTED)
        for values, color in valid:
            pdf.setStrokeColor(color)
            pdf.setLineWidth(1.45)
            last = None
            for j, v in enumerate(values):
                pt = (x0+(x1-x0)*j/(len(values)-1),
                      ybottom+(ytop-ybottom)*(float(v)-low)/(high-low))
                if last:
                    pdf.line(last[0],last[1],pt[0],pt[1])
                last = pt
        text_fit('Close', x0, ytop+10, 50, 8, True, BLUE)
        text_fit('EMA20', x0+72, ytop+10, 60, 8, True, HexColor('#CB8B22'))
        text_fit('EMA50', x0+157, ytop+10, 60, 8, True, HexColor('#8066AE'))
        text_fit(dates[0], x0, ybottom-15, 96, 7, color=MUTED)
        text_fit(dates[-1], x1-96, ybottom-15, 96, 7, color=MUTED)
        y = ybottom-33

    invested = sum(row['invested'] for row in holdings)
    value = sum(row['value'] for row in holdings)
    pnl = value - invested
    top = sorted(holdings, key=lambda r: (-r['weight'], r['symbol']))
    insights = diagnose_portfolio(holdings)
    exposures = industry_exposure(holdings, peers)
    stamp = datetime.now(IST).strftime('%d %b %Y %H:%M IST')

    # PAGE 1: meaningful quantitative summary; all source data is verified.
    new_page('01 / PORTFOLIO DASHBOARD', 'Generated {} | As-of: completed daily closing prices'.format(stamp))
    width = (right - left - 20) / 3
    for index, (label, metric, color) in enumerate((
        ('INVESTED', compact_money(invested), NAVY),
        ('LAST-CLOSE VALUE', compact_money(value), NAVY),
        ('UNREALISED P/L', compact_money(pnl), GREEN if pnl >= 0 else RED),
    )):
        x = left + index * (width + 10)
        pdf.setFillColor(PANEL)
        pdf.roundRect(x, y - 66, width, 70, 6, stroke=0, fill=1)
        text_fit(label, x + 10, y - 16, width - 20, 7.6, True, MUTED)
        text_fit(metric, x + 10, y - 44, width - 20, 13, True, color)
    y -= 91
    line('PORTFOLIO SUMMARY', 11, True, 20, NAVY)
    line('Invested: {}   Last-close value: {}'.format(money(invested), money(value)), 9)
    line('Unrealised P/L: Rs {:+,.2f} ({:+.2f}%)'.format(pnl, 100 * pnl / invested), 10, True, 18,
         GREEN if pnl >= 0 else RED)
    subhead('EXECUTIVE DIAGNOSIS (PRICE-BASED)')
    biggest = top[0]
    line('{} positions | Largest: {} at {:.1f}% of value.'.format(
        len(holdings), biggest['symbol'], biggest['weight']), 9)
    if biggest['weight'] > 40:
        line('Concentration warning: a single holding exceeds the illustrative 40% threshold.', 9,
             color=RED)
    line('No numerical health score or personalised target allocation without verified suitability data.', 8,
         color=MUTED)
    subhead('CURRENT HOLDING ALLOCATION')
    for row in top:
        pct = float(row['weight'])
        text_fit(row['symbol'], left, y - 1, 86, 9, True, INK)
        bar_x, bar_w = left + 94, 310
        pdf.setFillColor(PANEL)
        pdf.roundRect(bar_x, y - 3, bar_w, 10, 3, stroke=0, fill=1)
        pdf.setFillColor(RED if pct > 40 else BLUE)
        pdf.roundRect(bar_x, y - 3, max(0.1, bar_w * min(100, pct) / 100), 10, 3, stroke=0, fill=1)
        text_fit('{:.1f}%'.format(pct), bar_x + bar_w + 12, y - 1, 64, 9, True,
                 RED if pct > 40 else NAVY)
        y -= 37
    subhead('DATA BOUNDARIES')
    line('Values use your current quantity and average buy cost, plus a verified dated close.', 8)
    line('Price-based allocation is not a recommendation to sell or add any security.', 8)
    subhead('WHAT IS DRIVING RESULTS')
    line('Top 2 holdings: {:.1f}% of value | Effective stock count: {:.2f} (from {} holdings).'.format(
        insights['top_two_pct'], insights['effective_positions'], len(holdings)), 8.5)
    if insights['top_gain'] or insights['top_loss']:
        gain = insights['top_gain']
        loss = insights['top_loss']
        line('Largest gain: {} | Largest loss: {}'.format(
            '{} (+{})'.format(gain[0], money(gain[1])) if gain else 'None',
            '{} ({})'.format(loss[0], money(loss[1])) if loss else 'None'), 8.2)

    # PAGE 2: all 1-5 holdings, with full precision and technical observations.
    new_page('02 / HOLDING-WISE VERIFIED RESULTS', 'Every holding contributes to the portfolio summary; technicals use completed daily prices.')
    for row in holdings:
        card_top = y
        pdf.setFillColor(PANEL)
        pdf.roundRect(left - 2, card_top - 103, right - left + 4, 110, 7, stroke=0, fill=1)
        label = '{} | Qty {:g} | Average purchase {}'.format(
            row['symbol'], row['qty'], money(row['buy']))
        line(label, 10, True, 17, NAVY)
        line('Last close Rs {:,.2f} ({}) | Value {}'.format(
            row['close'], row['date'], money(row['value'])), 9, leading=16)
        line('P/L Rs {:+,.2f} ({:+.2f}%) | Portfolio weight {:.1f}%'.format(
            row['pnl'], row['pnl_pct'], row['weight']), 9, leading=15,
            color=GREEN if row['pnl'] >= 0 else RED)
        ema50 = row.get('ema50')
        trend = ('Above EMA20 and EMA50' if ema50 and row['close'] > row['ema20'] > ema50
                 else 'Below EMA20 and EMA50' if ema50 and row['close'] < row['ema20'] < ema50
                 else 'Mixed / insufficient trend confirmation')
        rsi = 'N/A' if row['rsi14'] is None else '{:.1f}'.format(row['rsi14'])
        line('EMA20 Rs {:.2f} | EMA50 {} | RSI14 {} | {}'.format(
            row['ema20'], 'Rs {:.2f}'.format(ema50) if ema50 else 'N/A',
            rsi, trend), 8, leading=13)
        movement = row.get('return20')
        twenty = ' | 20-session change {:+.1f}%'.format(movement) if movement is not None else ''
        line('20-close high Rs {:.2f} | low Rs {:.2f}{}'.format(
            row['high20'], row['low20'], twenty), 8, leading=14)
        if str(row.get('price_source') or '').startswith('BSE:'):
            line('Source: BSE exact ticker; INR completed daily OHLCV.', 7.5, leading=12)
        elif str(row.get('price_source') or '').startswith('NSE EOD'):
            line('Source: NSE official daily file; historical OHLC cross-checked.', 7.5, leading=12)
        # Colored review flag communicates *which rule fired*, not a trade.
        priority = insights['by_symbol'][row['symbol']]['priority']
        label_color = RED if priority == 'HIGH FLAGS' else (BLUE if priority == 'WATCH FLAGS' else GREEN)
        pdf.setFillColor(label_color)
        pdf.roundRect(right - 120, card_top - 100, 111, 16, 4, stroke=0, fill=1)
        text_fit(priority, right - 115, card_top - 96, 100, 7, True, WHITE)
        y = min(card_top - 118, y - 7)
    line('Price status is evidence, not a HOLD / ADD / EXIT instruction.', 8, color=MUTED)

    # PAGE 3: sample coverage, vetted signals and non-vetted comparisons separately.
    new_page('03 / NIFTY 500 INDUSTRY-PEER WATCHLIST',
             'Same-industry comparisons require official index membership and verified dated histories.')
    subhead('WHAT WAS ACTUALLY SCREENED')
    if peers.get('status') == 'SCREENED':
        line('Official constituents: {} | Same-industry pool: {} | Screened: {} | Fresh: {}'.format(
            peers['universe'], peers['pool'], peers['screened'], peers['fresh']), 9)
        line('Moderate technical screen qualified: {}; up to 2 per industry shown.'.format(
            peers['qualified']), 9)
        if exposures:
            subhead('OFFICIAL INDUSTRY EXPOSURE (NOT TARGET ALLOCATION)')
            for group in exposures:
                line('{} | {:.1f}% of current value'.format(
                    group['industry'], group['weight']), 8, leading=14)
        subhead('QUALIFIED TECHNICAL WATCHLIST')
        candidates = peers.get('candidates') or []
        if candidates:
            for row in candidates[:8]:
                line('{} | {} | Technical score {:.0f}/100 | Close {} ({})'.format(
                    row['symbol'], row['industry'], float(row['score']),
                    money(row['close']), row['date']), 8.3, leading=16)
        else:
            line('No candidate passed all fresh-price, trend, liquidity and risk gates.', 9)
        subhead('OTHER VERIFIED INDUSTRY COMPARISONS (NOT QUALIFIED)')
        comparisons = peers.get('comparisons') or []
        names = {c['symbol'] for c in candidates}
        shown = 0
        for row in comparisons:
            if row.get('symbol') in names:
                continue
            if shown >= 8:
                break
            line('{} | {} | Close {} | 20D {:+.1f}% | RSI14 {:.1f} ({})'.format(
                row['symbol'], row['industry'], money(row['close']),
                row['return20'], row['rsi14'], row['date']), 8, leading=15)
            shown += 1
        if not shown:
            line('No additional fresh, fully verified peer history available.', 9)
        line('These comparisons have NOT passed the full moderate technical screen.', 8, color=RED)
        line('Industry labels are broad NSE categories, not proof of direct competition.', 8,
             color=MUTED)
    else:
        line('Unavailable: official membership, same-industry peers or fresh data not verified.', 9)
    subhead('INTERPRETING THE SHORTLIST')
    line('Sample screen only; NOT a ranking of all 500 companies.', 8)
    line('Technical strength is not fundamental quality, suitability or expected returns.', 8)
    line('Candidates are for further research; no automatic replacement or order.', 8)

    # PAGE 4: explicit evidence gaps, risk and scope.
    new_page('04 / RISK, SOURCES AND LIMITS',
             'Risk flags are deterministic historical-price observations, never trade instructions.')
    subhead('CONCENTRATION SENSITIVITY (HYPOTHETICAL ONLY)')
    line('Top 2 holdings = {:.1f}% of value; effective stock count {:.2f}.'.format(
        insights['top_two_pct'], insights['effective_positions']), 8.7)
    line('If ONLY {} fell 10% with other prices fixed: approx -{} ({:.2f}% of portfolio).'.format(
        insights['largest_symbol'], money(insights['largest_ten_percent_stress']),
        insights['largest_stress_portfolio_pct']), 8.7)
    line('This is an arithmetic scenario, NOT a probability, price target or forecast.', 8, color=MUTED)
    subhead('RISK CHECKS')
    flags = []
    for row in holdings:
        if row['weight'] > 40:
            flags.append('{} concentration {:.1f}% (over 40% of current portfolio value)'.format(
                row['symbol'], row['weight']))
        if row['rsi14'] is not None and row['rsi14'] > 70:
            flags.append('{} RSI above 70; extended/overbought conditions'.format(row['symbol']))
        if row.get('ema50') and row['close'] < row['ema50']:
            flags.append('{} close below its 50-session exponential moving average'.format(row['symbol']))
    for message in flags or ['No threshold flags from this limited price-only check.']:
        line('- ' + message, 8.8, leading=19, color=RED if 'concentration' in message else INK)
    subhead('LIMITS & DISCLAIMER')
    for message in (
        'Last close is not a tradeable live price. Prices can change after the last session.',
        'Indicators are calculated from historical closes, not audited fundamentals.',
        'Cost and P/L use your entered current share count and average purchase cost.',
        'Use broker-adjusted quantities/costs after splits or bonuses; these are not inferred.',
        'Telegram processes your messages and PDF under its own storage policies.',
        'No BUY/SELL signal, guaranteed return or order is provided.',
        'Educational information only. Not SEBI-registered investment advice.',
    ):
        line('- ' + message, 8.5, leading=20)
    subhead('MISSING EVIDENCE FOR A TRUE QUALITY RANKING')
    line('Before selecting replacement shares: verify quarterly filings, debt, cash flow,', 8)
    line('valuation, corporate actions, business-model fit and sector overlap.', 8)
    line('No "best stock" label can be earned from 20-day momentum alone.', 8, color=RED)
    subhead('DATA QUALITY CHECKLIST')
    line('Market prices: dated completed OHLCV, checked for missing/stale/nonfinite values.', 8)
    line('Constituents: official Nifty 500 membership verified before peer comparison.', 8)
    line('Company fundamentals, valuation, governance: NOT VERIFIED in the basic report.', 8)
    line('Sector target weights, tax impact, health score and replacements: NOT CALCULATED.', 8)
    line('Privacy: PDF created in memory for one request; MSK does not store holdings.', 8)

    if has_risk:
        new_page('05 / HISTORICAL RISK & NIFTY 50',
                 "Reconstructed value of today's holdings; not your actual trade history.")
        subhead('VERIFIED HISTORY COVERAGE')
        line('All {} holdings | {} common observed sessions | {} to {}.'.format(
            risk['included'], risk['coverage'],
            risk['first_date'], risk['last_date']), 8.8)
        line('Only matching market dates; no missing-day interpolation or backfill.', 8,
             color=MUTED)
        subhead('RISK METRICS FOR STATIC SHARE COUNTS')
        line('Annualised volatility: {:.1f}% | Max historical drawdown: {:.1f}%'.format(
            risk['annual_vol_pct'], risk['max_drawdown_pct']), 9.2, True)
        line('Current drawdown: {:.1f}% | Observed window change: {:+.1f}%'.format(
            risk['current_drawdown_pct'], risk['static_change_pct']), 9.0)
        if risk.get('beta') is not None:
            line('Beta vs Nifty 50: {:.2f} | Paired observations: {}'.format(
                risk['beta'], risk['beta_coverage']), 9)
        else:
            line('Beta vs Nifty 50: not verified on enough matching index sessions.', 8.5)
        subhead('REBASED HISTORICAL PATH (START = 100)')
        draw_historical_chart(risk)
        subhead('STOCKS THAT MOVED TOGETHER')
        if risk.get('pairs'):
            for pair in risk['pairs'][:4]:
                line('{} / {} | daily-return correlation {:.2f}'.format(
                    pair['a'], pair['b'], pair['correlation']), 8.5)
        else:
            line('No pair reached correlation >=0.75 with sufficient daily variation.', 8.5)
        subhead('IMPORTANT INTERPRETATION')
        line('These are risk estimates for fixed current quantities, not realised P/L.', 8)
        line('Past results are not a forecast. Corporate actions can affect comparability.', 8)
        line('No BUY / SELL, asset allocation or risk tolerance has been assumed.', 8)
    # OPTIONAL COMPANY RESEARCH APPENDIX (one page for each held stock).
    # Full 8+ page standalone equity reports are delivered only on explicit
    # Telegram button tap, outside the private Doctor worker.
    by_symbol = {row['symbol']: row for row in holdings}
    for index, company in enumerate(companies):
        symbol = company['symbol']
        held_row = by_symbol[symbol]
        new_page('{:02d} / COMPANY RESEARCH: {}'.format(
            (5 if has_risk else 4)+index+1, symbol),
            'Dated technical facts and limited secondary financial evidence; not a trade opinion.')
        line('Exchange close {}: {} | Current portfolio weight {:.2f}%'.format(
            company['date'], money(held_row['close']), held_row['weight']), 9, True)
        line('Industry: {}'.format(company.get('industry') or 'Not verified'), 8)
        if company.get('status') != 'VERIFIED':
            subhead('TECHNICAL EVIDENCE UNAVAILABLE')
            line('Not enough independently verified completed candles for analysis.', 9)
        else:
            subhead('PRICE, TREND AND RISK')
            line('20-session return: {:+.1f}% | RSI14: {:.1f} | MACD histogram: {:+.3f}'.format(
                company['return20'], company['rsi14'],
                company['macd_hist'] if company['macd_hist'] is not None else 0.0), 8.5)
            line('EMA20: {} | EMA50: {} | EMA200: {}'.format(
                money(company['ema20']) if company['ema20'] is not None else 'N/A',
                money(company['ema50']) if company['ema50'] is not None else 'N/A',
                money(company['ema200']) if company['ema200'] is not None else 'N/A'), 8.5)
            line('1Y close range: {} to {} | 60-day annualised vol: {:.1f}%'.format(
                money(company['year_close_low']),
                money(company['year_close_high']), company['annual_vol_pct']), 8.5)
            atr = company.get('atr14_pct')
            volume = company.get('volume_ratio')
            line('ATR14: {} | Latest volume / previous 20-day mean: {}'.format(
                '{:.2f}% of price'.format(atr) if atr is not None else 'Unavailable',
                '{:.2f}x'.format(volume) if volume is not None else 'Unavailable'), 8.5)
            subhead('VERIFIED DAILY CLOSE / EMA CHART')
            company_price_chart(company)
        subhead('FUNDAMENTAL AND VALUATION FACTS')
        finance = company.get('financial') or {}
        if finance.get('status') == 'SECONDARY':
            line('Source: Screener.in (secondary); {} | FY: {}.'.format(
                finance.get('scope', 'unknown'), finance.get('annual_period','unknown')), 8)
            line('Retrieved UTC: {} | Issuer filing reconciliation: NO.'.format(
                str(finance.get('retrieved_utc','unavailable'))[:24]), 8, color=RED)
            for metric in (finance.get('metrics') or [])[:11]:
                line('{}: {:,.2f} {} | Period: {}'.format(
                    metric['label'], metric['value'], metric['unit'],
                    metric['period']), 8, leading=13)
        else:
            line('Not verified: no usable dated comparable financial statement facts.', 8)
            line('P/E, sales, profit, margins, cash flow and debt are not guessed.', 8)
        subhead('IMPORTANT RESEARCH LIMITS')
        line('Shareholding, news catalysts and peer valuation: NOT VERIFIED here.', 8)
        line('Scores, buying zones, targets and personal trade calls: NOT GENERATED.', 8)
        line('For the full separate 8+ page company report, tap the stock button', 8)
        line('under the Portfolio Doctor PDF in Telegram. Validate issuer filings.', 8)
    footer()
    pdf.save()
    data = out.getvalue()
    if not (1000 <= len(data) <= max_bytes and data.startswith(b'%PDF-')):
        raise ValueError('invalid_portfolio_pdf')
    return data