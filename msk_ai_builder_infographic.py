"""Static A4 allocation infographics from a validated model; no remote I/O."""
from collections import defaultdict
from decimal import Decimal
import io

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth

NAVY = "#102B46"
BLUE = "#3478B8"
TEAL = "#15978E"
MUTED = "#51657A"
PALE = "#EDF3F8"
WHITE = "#FFFFFF"
W, H = A4
L, R = 42, W - 42


def _money(value):
    return "Rs " + format(value, ",.2f")


def _label(c, text, x, y, *, size=10, color=NAVY, bold=False, width=None):
    font = "Helvetica-Bold" if bold else "Helvetica"
    s = str(text)
    if width is not None and stringWidth(s, font, size) > width:
        while s and stringWidth(s + "...", font, size) > width:
            s = s[:-1]
        s += "..."
    c.setFillColor(HexColor(color))
    c.setFont(font, size)
    c.drawString(x, y, s)


def _panel(c, x, y, w, h, color=PALE):
    c.setFillColor(HexColor(color))
    c.roundRect(x, y, w, h, 7, stroke=0, fill=1)


def _page(c, title, page, total, plan, fictional):
    _panel(c, L, H-117, R-L, 77, NAVY)
    _label(c, "MSK FINANCIAL INTELLIGENCE", L+16, H-61, size=9, color=WHITE, bold=True)
    _label(c, title, L+16, H-87, size=20, color=WHITE, bold=True)
    _label(c, ("FICTIONAL QA DEMO | " if fictional else "Research model | ") +
           "Completed close: " + plan.close_date.isoformat(), L+16, H-105,
           size=8, color=WHITE)
    _label(c, "Private | Hypothetical allocation | No automatic orders", L, 32, size=8, color=MUTED)
    _label(c, f"{page} / {total}", R-40, 32, size=8, color=MUTED)


def _bar(c, x, y, w, fraction, color=BLUE):
    c.setFillColor(HexColor(PALE))
    c.roundRect(x,y,w,8,3,stroke=0,fill=1)
    span = w*float(fraction)
    if span > 0:
        c.setFillColor(HexColor(color))
        c.rect(x,y,span,8,stroke=0,fill=1)


def allocation_pdf(plan, artifact, *, optimized=False, fictional=False):
    """Render the actual approved allocation (fixture use visibly labeled).

    No invented financial metrics, forecasts or unsupported benchmark charts.
    Caller is responsible for the approval gate. A single immutable plan drives
    every number; cash and holding totals are independently reconciled here.
    """
    cap = Decimal(plan.profile.capital_rupees)
    holdings = tuple(plan.positions)
    invested = sum((p.cost for p in holdings), Decimal(0))
    if (cap <= 0 or plan.cash < 0 or any(p.shares <= 0 or p.cost <= 0 for p in holdings)
            or invested + plan.cash != cap or len(holdings) > 20):
        raise ValueError("Inconsistent allocation")
    approved = {r["symbol"]: r for r in artifact["companies"]}
    if any(p.candidate.symbol not in approved for p in holdings):
        raise ValueError("Missing source evidence")
    sectors = defaultdict(Decimal)
    for p in holdings:
        sectors[p.candidate.industry] += p.cost
    sector_rows = sorted(sectors.items(), key=lambda v:(-v[1],v[0]))
    chunks = [holdings[i:i+10] for i in range(0,len(holdings),10)] or [()]
    sector_chunks = [sector_rows[i:i+12] for i in range(0,len(sector_rows),12)] or [[]]
    pages = 2 + len(chunks) + len(sector_chunks)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle("MSK Portfolio Builder | Allocation research")
    _page(c, "Portfolio Builder 2.0", 1, pages, plan, fictional)
    _label(c, f"{plan.profile.risk.title()} risk preference | {plan.profile.months} months",
           L, H-140, size=12, bold=True)
    cards = [("Investment capital", _money(cap)), ("Stock value", _money(invested)),
             ("Modeled cash", _money(plan.cash)), ("Selected stocks", str(len(holdings)))]
    w = (R-L-12)/2
    for i,(name,value) in enumerate(cards):
        x = L+(i%2)*(w+12)
        y = H-235-(i//2)*80
        _panel(c,x,y,w,66)
        _label(c,name,x+12,y+44,size=10,color=MUTED)
        _label(c,value,x+12,y+17,size=19,bold=True,width=w-24)
    cy=H-435
    _label(c,"Capital distribution",L,cy+80,size=15,bold=True)
    c.setFillColor(HexColor(PALE))
    c.roundRect(L,cy+45,R-L,20,5,stroke=0,fill=1)
    c.setFillColor(HexColor(BLUE))
    c.rect(L,cy+45,(R-L)*float(invested/cap),20,stroke=0,fill=1)
    _label(c,f"Equities {invested/cap:.1%}",L,cy+23,bold=True)
    _label(c,f"Cash {plan.cash/cap:.1%}",R-130,cy+23,bold=True)
    _label(c,"Allocation approach",L,cy-20,size=13,bold=True)
    _label(c, "Constrained minimum historical variance" if optimized else "Capped equal-weight research model",
           L,cy-42,size=11)
    _label(c,"Risk preference is not a personal suitability assessment.",L,cy-60,size=9,color=MUTED)
    _label(c,"Dated closes are not executable prices. Cash includes whole-share rounding.",L,cy-77,size=9,color=MUTED)
    if not holdings:
        _label(c,"No stocks fit this model; the full capital remains modeled cash.",L,cy-106,bold=True)
    c.showPage()
    page=2
    for chunk in chunks:
        _page(c,"Stock allocation",page,pages,plan,fictional)
        _label(c,"Symbol / sector",L,H-147,bold=True,size=9)
        _label(c,"Shares",218,H-147,bold=True,size=9)
        _label(c,"Close (Rs)",274,H-147,bold=True,size=9)
        _label(c,"Value (Rs)",365,H-147,bold=True,size=9)
        _label(c,"Weight",490,H-147,bold=True,size=9)
        y=H-174
        for p in chunk:
            item=p.candidate
            if y < 90:
                raise ValueError("Allocation table overflow")
            _label(c,item.symbol,L,y,bold=True,size=10)
            _label(c,item.industry,L,y-13,size=8,color=MUTED,width=165)
            _label(c,f"{p.shares:,}",218,y,size=9,width=52)
            _label(c,f"{item.close_rupees:,.2f}",274,y,size=9,width=86)
            _label(c,f"{p.cost:,.2f}",365,y,size=9,width=113)
            _label(c,f"{p.cost/cap:.2%}",490,y,size=9,width=60)
            _bar(c,L,y-28,R-L,p.cost/cap)
            y-=53
        if not chunk:
            _label(c,"No stock positions.",L,y)
        c.showPage()
        page+=1
    for chunk in sector_chunks:
        _page(c,"Sector diversification",page,pages,plan,fictional)
        _label(c,"Share of total capital (cash included in denominator)",L,H-148,size=10,color=MUTED)
        y=H-180
        for sector,amount in chunk:
            _label(c,sector,L,y,bold=True,width=225)
            _label(c,f"{amount/cap:.2%} | {_money(amount)}",R-180,y,size=9)
            _bar(c,L,y-16,R-L,amount/cap,TEAL)
            y-=41
        _label(c,f"Cash: {plan.cash/cap:.2%} | {_money(plan.cash)}",L,y-6,bold=True)
        c.showPage()
        page+=1
    _page(c,"Method and evidence",page,pages,plan,fictional)
    _label(c,"Approved-source references for selected stocks",L,H-148,bold=True,size=12)
    y=H-173
    for p in holdings:
        s=p.candidate.symbol
        row=approved[s]
        _label(c,s,L,y,bold=True,size=9)
        for x,key,label in [(200,"filing_url","Issuer filing"),(340,"price_source_url","Exchange price")]:
            if not fictional:
                _label(c,label,x,y,color=BLUE,size=9)
                c.linkURL(row[key],(x,y-2,x+95,y+10),relative=0,thickness=0)
            else:
                _label(c,"Fictional source",x,y,color=MUTED,size=9)
        y-=22
    if not holdings:
        _label(c,"No selected-company references.",L,y,size=9)
    y-=25
    _label(c,"Historical data does not imply future performance.",L,y,size=9,color=MUTED)
    _label(c,"Benchmark and company metrics omitted unless independently supported.",L,y-16,size=9,color=MUTED)
    c.showPage()
    c.save()
    raw=buf.getvalue()
    from pypdf import PdfReader
    if not 1000 <= len(raw) <= 2*1024*1024 or len(PdfReader(io.BytesIO(raw),strict=True).pages)!=pages:
        raise ValueError("Invalid report")
    return raw
