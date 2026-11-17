"""Multi-page advanced Doctor PDF rendered directly into bytes, no chart files."""
import io
import math


def render_advanced_pdf(review):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor

    def finite(value, digits=2, unit=""):
        try:
            x = float(value)
            return ("{:,.%df}" % digits).format(x) + unit if math.isfinite(x) else "N/A"
        except (TypeError, ValueError, OverflowError):
            return "N/A"

    def money(n):
        return "Rs " + finite(n)

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    w, h = A4
    left, right = 42, w - 42
    dark, teal, grey, red = (HexColor(v) for v in
                             ("#152B45", "#007E8A", "#516173", "#C32B35"))
    y = h - 53
    page_number = 0

    def safe(text, limit=110):
        # No customer text may break layout or escape PDF markup.
        return str(text).replace("\r", " ").replace("\n", " ").strip()[:limit]

    def line(text, size=9, bold=False, leading=14, color=None):
        nonlocal y
        font = "Helvetica-Bold" if bold else "Helvetica"
        text = safe(text, 400)
        tokens = text.split(" ")
        out = []
        row = ""
        for tok in tokens:
            test = (row + " " + tok).strip()
            if row and stringWidth(test, font, size) > right - left:
                out.append(row)
                row = tok
            else:
                row = test
        if row:
            out.append(row)
        pdf.setFillColor(color or dark)
        pdf.setFont(font, size)
        for phrase in out:
            if y < 75:
                # Page spill is not dropped: continue onto a new numbered page.
                start_page("CONTINUED", subtitle="Continued content; all rows preserved")
            pdf.drawString(left, y, phrase)
            y -= leading

    def start_page(title, subtitle=""):
        nonlocal y, page_number
        if page_number:
            footer()
            pdf.showPage()
        page_number += 1
        y = h - 53
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(left, y, "MSK | PORTFOLIO DOCTOR")
        y -= 29
        pdf.setFillColor(teal)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(left, y, safe(title))
        y -= 19
        if subtitle:
            line(subtitle, size=8, leading=14, color=grey)
        pdf.setStrokeColor(HexColor("#C9D6E0"))
        pdf.line(left, y - 3, right, y - 3)
        y -= 22

    def footer():
        pdf.setFillColor(grey)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(left, 40, "Private one-time research | Historical closing prices, not live quotes")
        pdf.drawRightString(right, 40, "Page {}".format(page_number))

    def gap(amount=9):
        nonlocal y
        y -= amount

    def separator():
        nonlocal y
        pdf.setStrokeColor(HexColor("#DDE5EA"))
        pdf.line(left, y + 3, right, y + 3)
        y -= 12

    def subhead(text):
        line(text, size=10, bold=True, leading=17, color=teal)

    totals = review["totals"]
    holdings = review["holdings"]
    fundamentals = review["fundamentals"]
    technicals = review["technicals"]
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))

    # PAGE 1 / dashboard; basic customer figures are unchanged.
    start_page("01 / PORTFOLIO DASHBOARD",
               datetime.now(ist).strftime("Generated %d %b %Y %H:%M IST"))
    line("Source: completed daily history; NSE EOD repair requires prior-day OHLC agreement.", 8)
    line("Provider provenance is not individually certified; no live trade quote.", 8)
    gap()
    subhead("TOTAL INVESTMENT")
    line("{} invested  |  {} last-close value".format(
        money(totals["invested"]), money(totals["value"])), 12, True, 23)
    line("Unrealised profit/loss: {} ({}%)".format(
        money(totals["pnl"]), finite(totals["pnl_pct"])), 11, True, 24)
    line("Coverage: {}/{} holdings; all positions included in totals.".format(
        totals["covered"], totals["expected"]), 9)
    gap()
    subhead("YOUR HOLDINGS")
    for r in holdings:
        line("{}  |  Qty {:g}  |  Avg {}  |  Close {} ({})".format(
            safe(r["symbol"], 24), r["qty"], money(r["buy"]), money(r["close"]),
            safe(r["date"], 12)), 9, True)
        line("Invested {} | Value {} | P/L {} ({:+.2f}%) | Weight {:.1f}%".format(
            money(r["invested"]), money(r["value"]), money(r["pnl"]),
            r["pnl_pct"], r["weight"]), 8)
        if r.get("price_source", "").startswith("NSE EOD"):
            line("Close: NSE official bhavcopy; previous day cross-checked with history.", 8)
        separator()
    gap()
    line("This is a mark-to-last-close view. Cost basis is customer supplied.", 8, color=grey)
    line("Dividends, fees, tax, adjustments and unsettled transactions are excluded.", 8, color=grey)

    # PAGE 2 / fundamentals — every visible value is evidence scoped.
    start_page("02 / HOLDING-WISE FUNDAMENTAL REVIEW",
               "Secondary issuer-data research. Missing values are not guessed.")
    for fund in fundamentals:
        subhead("{}  |  {}  |  {} indicators available".format(
            safe(fund["symbol"], 24), fund["status"],
            "{}/{}".format(fund["available"], fund["required"])))
        line("FY: {} | Latest quarter: {} ({})".format(
            fund["annual_period"], fund["quarter_period"], fund["freshness"]), 8)
        line("Source: {} | Statement scope: {}".format(
            safe(fund.get("source") or "Unavailable", 55),
            safe(str(fund.get("scope") or "unverified").upper(), 20)), 8)
        line("Retrieved UTC: {} | Issuer filing authentication: NO".format(
            safe(fund.get("retrieved_utc") or "NOT VERIFIED", 32)), 8)
        line("Vendor ratios: source page only; ratio reporting period not supplied.", 8)
        m = fund["metrics"]
        a = [
            ("Sales FY", "revenue", "INR Cr"), ("Profit FY", "net_profit", "INR Cr"),
            ("3Y sales growth", "revenue_cagr3", "%"),
            ("3Y profit growth", "profit_cagr3", "%"),
            ("ROE (vendor)", "roe", "%"),
            ("P/B (vendor)", "pb", "x"),
            ("P/E (vendor)", "pe", "x"),
        ]
        if not fund["is_lender"]:
            a.extend([("Operating margin FY", "operating_margin", "%"),
                      ("ROCE FY", "roce", "%"), ("Debt/equity FY", "debt_equity", "x"),
                      ("Operating cash flow FY", "operating_cash_flow", "INR Cr")])
        for i in range(0, len(a), 2):
            vals = []
            for title, key, unit in a[i:i+2]:
                datum = m[key]
                vals.append("{}: {}{}".format(title, finite(datum["value"]),
                                               " " + unit if datum["value"] is not None else ""))
            line("   |   ".join(vals), size=7.7, leading=12)
        if fund["is_lender"]:
            line("Bank/finance basis: corporate debt, ROCE and cash-flow ratios not applied.", 7.5, color=grey)
        gap(9)
        separator()
    line("Interest coverage and free cash flow: NOT VERIFIED; deliberately omitted.", 8, color=grey)

    # PAGE 3 / technical research; no false trade recommendation.
    start_page("03 / TECHNICAL AND BENCHMARK OBSERVATIONS",
               "Only completed historical candles. Indicators are observations.")
    for row, tech in zip(holdings, technicals):
        subhead("{} | {}".format(row["symbol"], tech.get("trend") or "Trend unavailable"))
        line("EMA20 {} | EMA50 {} | RSI14 {} | 20-close high/low {} / {}".format(
            money(tech.get("ema20")), money(tech.get("ema50")),
            finite(tech.get("rsi14"), 1),
            money(row.get("high20")), money(row.get("low20"))), 8)
        line("Support {} | Resistance {} | 20-session relative volume {}".format(
            money(tech.get("support")) if isinstance(tech.get("support"), (int, float)) else "N/A",
            money(tech.get("resistance")) if isinstance(tech.get("resistance"), (int, float)) else "N/A",
            finite(tech.get("volume_ratio"), 2, "x")), 8)
        rel = tech.get("relative_1m")
        if rel:
            line("Aligned {} sessions: stock {}% vs Nifty {}%; excess {} pp (to {})".format(
                rel["sessions"], finite(rel["stock_pct"]), finite(rel["nifty_pct"]),
                finite(rel["relative_pp"]), rel["end"]), 8)
        else:
            line("Nifty-relative performance: NOT VERIFIED on matching dates.", 8, color=grey)
        cost_basis = tech.get("corporate_action_review") or {}
        line("Split/bonus/cost-basis status: {} (not broker-verified).".format(
            cost_basis.get("status") or "NOT VERIFIED"), 8, color=grey)
        for warning in (cost_basis.get("warnings") or [])[1:]:
            line(warning, 7.5, color=red)
        if _safe_float(tech.get("rsi14")) is not None and tech["rsi14"] > 70:
            line("High RSI is a momentum-risk observation; NOT an EXIT instruction.", 8, color=red)
        gap(7)
        separator()

    # PAGE 4 / allocation and price-based checks.
    start_page("04 / SECTOR ALLOCATION & CONCENTRATION",
               "Sector labels are mapping-based, not guaranteed current classifications.")
    sectors = review["sectors"]
    for sector, pct in sectors.items():
        label = safe(sector, 43)
        line("{} | {:.1f}%".format(label, pct), 9, True, 15)
        bar_y = y - 5
        pdf.setFillColor(HexColor("#E9EFF3"))
        pdf.roundRect(left, bar_y, 415, 9, 4, fill=1, stroke=0)
        pdf.setFillColor(red if pct > 35 else teal)
        pdf.roundRect(left, bar_y, 415 * max(0, min(pct, 100)) / 100, 9, 4, fill=1, stroke=0)
        y -= 24
    gap()
    line("Sector allocation total: {:.2f}% including unclassified positions.".format(
        sum(sectors.values())), 9, True)
    gap()
    subhead("PORTFOLIO FIT")
    for row in holdings:
        if row["weight"] > 40:
            line("{}: {:.1f}% value concentration (illustrative 40% threshold).".format(
                row["symbol"], row["weight"]), 9, color=red)
    if "Unknown / unclassified" in sectors:
        line("Unknown sector holdings are included in totals but not treated as diversified.", 8)
    line("Concentration thresholds are research warnings, not personalised limits.", 8, color=grey)

    # PAGE 5 / No personalised BUY/SELL assertions.
    start_page("05 / HOLDING REVIEW & REPLACEMENT COMPARISON",
               "Personalised recommendations require compliance approval.")
    for row, finding in zip(holdings, review["opinions"]):
        subhead("{} | {}".format(row["symbol"], finding["opinion"]))
        line("Evidence gate: {}".format(", ".join(finding.get("conditions") or []) or
             "conditional human review"), 8)
        gap(7)
    separator()
    subhead("REPLACEMENT RESEARCH")
    replacement = review.get("replacements") or {}
    line(replacement.get("status") or "NO SUITABLE REPLACEMENT FOUND", 9, True)
    line("No automatic swap; comparisons need verified issuer, valuation, liquidity,", 8)
    line("technical and sector data on comparable reporting periods.", 8)
    gap(12)
    subhead("OPTIONAL PORTFOLIO BUILDER")
    if review.get("builder"):
        build = review["builder"]
        line("Hypothetical mode: {} | Cash {} | Total weights {}%".format(
            build["mode"], money(build["cash"]), finite(build["total_weights_pct"])), 9)
    else:
        line("OFF: no capital, horizon, risk, cash-needs or sector caps supplied.", 8)
        line("Builder cannot create a proposed portfolio without customer inputs.", 8)

    # PAGE 6 / sources and limits.
    start_page("06 / DATA SOURCES, GAPS & PRIVACY",
               "Missing and noncomparable evidence is visible, not invented.")
    subhead("MARKET DATA")
    line("Daily OHLCV: market_data.bulk_history, primary Yahoo/yfinance with", 9)
    line("bounded provider fallbacks. Official NSE EOD rescue is labelled per holding.", 9)
    line("Last available close is NOT guaranteed to be live or official NSE settlement.", 9)
    line("Historical series and customer buy averages need corporate-action verification.", 9)
    gap(11)
    subhead("FUNDAMENTALS")
    line("Financial tables/ratios: Screener.in secondary data where available.", 9)
    line("Annual FY and reported-quarter dates are shown; TTM is not silently FY.", 9)
    line("Statement-page scope displayed when a verified source URL was fetched.", 9)
    line("Issuer filing authentication and corporate actions: NOT VERIFIED.", 9)
    line("No fixed interest cover/free cash flow/governance flags without evidence.", 9)
    line("Sector-specific interpretation applies; bank ROCE/debt gates are withheld.", 9)
    gap(11)
    subhead("COVERAGE & LIMITS")
    line("Priced holdings: {}/{} | Sector allocation: {:.2f}%".format(
        len(holdings), totals["expected"], sum(sectors.values())), 9)
    line("Fundamental sources vary by date and may be missing or delayed.", 9)
    line("Indicator scores are not probabilities or backtested predictive accuracy.", 9)
    gap(11)
    subhead("REQUEST-ONLY PRIVACY")
    line("MSK generates this report in a disposable process and does not store", 9)
    line("your holdings or PDF in an MSK database. Telegram retains messages/files", 9)
    line("according to its own retention rules. No automatic orders.", 9)
    line("Educational research only; not SEBI-registered personal advice.", 9)
    footer()
    pdf.save()
    raw = stream.getvalue()
    if not (raw.startswith(b"%PDF-") and 3500 < len(raw) < 5 * 1024 * 1024):
        raise ValueError("invalid_advanced_pdf")
    return raw


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None
