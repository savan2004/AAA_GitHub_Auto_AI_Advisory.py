"""Four-page, evidence-bounded private Portfolio Doctor PDF. RAM only."""
import io
from datetime import datetime, timedelta, timezone

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

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


def render_pdf(holdings, peers=None, max_bytes=5 * 1024 * 1024):
    """Use already validated records; never fetch market data or persist files."""
    peers = peers or {}
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
        pdf.drawRightString(right, 46, 'Page {} of 4'.format(page_no))

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

    invested = sum(row['invested'] for row in holdings)
    value = sum(row['value'] for row in holdings)
    pnl = value - invested
    top = sorted(holdings, key=lambda r: (-r['weight'], r['symbol']))
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
        line('20-close high Rs {:.2f} | low Rs {:.2f}'.format(
            row['high20'], row['low20']), 8, leading=14)
        if str(row.get('price_source') or '').startswith('BSE:'):
            line('Source: BSE exact ticker; INR completed daily OHLCV.', 7.5, leading=12)
        elif str(row.get('price_source') or '').startswith('NSE EOD'):
            line('Source: NSE official daily file; historical OHLC cross-checked.', 7.5, leading=12)
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
    else:
        line('Unavailable: official membership, same-industry peers or fresh data not verified.', 9)
    subhead('INTERPRETING THE SHORTLIST')
    line('Sample screen only; NOT a ranking of all 500 companies.', 8)
    line('Technical strength is not fundamental quality, suitability or expected returns.', 8)
    line('Candidates are for further research; no automatic replacement or order.', 8)

    # PAGE 4: explicit evidence gaps, risk and scope.
    new_page('04 / RISK, SOURCES AND LIMITS',
             'Risk flags are deterministic historical-price observations, never trade instructions.')
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
    subhead('DATA QUALITY CHECKLIST')
    line('Market prices: dated completed OHLCV, checked for missing/stale/nonfinite values.', 8)
    line('Constituents: official Nifty 500 membership verified before peer comparison.', 8)
    line('Company fundamentals, valuation, governance: NOT VERIFIED in the basic report.', 8)
    line('Sector target weights, tax impact, health score and replacements: NOT CALCULATED.', 8)
    line('Privacy: PDF created in memory for one request; MSK does not store holdings.', 8)
    footer()
    pdf.save()
    data = out.getvalue()
    if not (1000 <= len(data) <= max_bytes and data.startswith(b'%PDF-')):
        raise ValueError('invalid_portfolio_pdf')
    return data