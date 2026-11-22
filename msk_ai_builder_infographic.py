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
L, R = 34, W - 34


def _money(value):
    return "Rs " + _indian(value)


def _indian(value):
    integer, fraction = f"{value:.2f}".split(".")
    tail = integer[-3:]
    rest = integer[:-3]
    while rest:
        tail = rest[-2:] + "," + tail
        rest = rest[:-2]
    return tail + "." + fraction


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
    _label(c, ("FICTIONAL QA DEMO | TEST MODE | Fixture date: " if fictional else "Research model | Completed close: ") +
           plan.close_date.isoformat(), L+16, H-105,
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



def _right(c, text, x, y, size=9, bold=False, color=NAVY):
    c.setFont("Helvetica-Bold" if bold else "Helvetica",size)
    c.setFillColor(HexColor(color))
    c.drawRightString(x,y,str(text))


def _section(c, title, x, y):
    _label(c,title,x,y,size=15,bold=True)
    c.setStrokeColor(HexColor(TEAL))
    c.setLineWidth(2)
    c.line(x,y-8,x+30,y-8)


def _metric(c,x,y,w,label,value,detail):
    _panel(c,x,y,w,68)
    _label(c,label,x+12,y+49,size=9,color=MUTED)
    _label(c,value,x+12,y+26,size=17,bold=True,width=w-24)
    _label(c,detail,x+12,y+10,size=8,color=MUTED,width=w-24)


def _risk_data(plan,artifact,optimized):
    if not optimized or not plan.positions:
        return None
    from msk_ai_builder_optimizer import validated_returns
    import numpy as np
    symbols=[r.candidate.symbol for r in plan.positions]
    all_symbols=[r["symbol"] for r in artifact["companies"]]
    returns=validated_returns(artifact["risk_history"],all_symbols,plan.close_date)[symbols]
    weights=np.array([float(r.cost/plan.profile.capital_rupees) for r in plan.positions])
    daily=returns.to_numpy()@weights
    wealth=np.r_[1.,np.cumprod(1.+daily)]
    drawdown=wealth/np.maximum.accumulate(wealth)-1.
    corr=returns.corr().to_numpy()
    if not np.isfinite(wealth).all() or not np.isfinite(corr).all():
        raise ValueError("Invalid historical risk")
    return dict(symbols=symbols,corr=corr,wealth=wealth,drawdown=drawdown,
                volatility=float(np.std(daily,ddof=1)*np.sqrt(252)),
                max_drawdown=float(drawdown.min()),observations=len(daily),
                first=str(returns.index[0]),last=str(returns.index[-1]))


def _series(c,x,y,w,h,series,color):
    lo,hi=min(series),max(series)
    if hi==lo:
        lo-=.01
        hi+=.01
    c.setStrokeColor(HexColor(PALE))
    for i in range(4):
        yy=y+i*h/3
        c.line(x,yy,x+w,yy)
        _right(c,f"{lo+(hi-lo)*i/3:.2f}",x-5,yy-2,size=7,color=MUTED)
    line=c.beginPath()
    for i,v in enumerate(series):
        xx=x+w*i/(len(series)-1)
        yy=y+h*(float(v)-lo)/(hi-lo)
        if i==0: line.moveTo(xx,yy)
        else: line.lineTo(xx,yy)
    c.setStrokeColor(HexColor(color))
    c.setLineWidth(1.5)
    c.drawPath(line)


def allocation_pdf(plan, artifact, *, optimized=False, fictional=False):
    """Premium compact vector report using one approved allocation and its evidence.

    All allocations include cash in the denominator. Historical analytics use
    constant current weights and zero-return cash, not a realized track record.
    No unsupported fundamentals or benchmark comparison is fabricated.
    """
    from msk_ai_builder_allocation import EQUITY_CAP
    cap=Decimal(plan.profile.capital_rupees)
    holdings=tuple(plan.positions)
    invested=sum((p.cost for p in holdings),Decimal(0))
    if (cap<=0 or plan.cash<0 or invested+plan.cash!=cap
            or len(holdings)>plan.profile.max_stocks
            or any(p.shares<=0 or p.cost<=0 for p in holdings)
            or len({p.candidate.symbol for p in holdings})!=len(holdings)):
        raise ValueError("Inconsistent allocation")
    stock_limit=Decimal(10 if plan.profile.risk=="low" else 15)/100
    equity_limit=Decimal(EQUITY_CAP[plan.profile.risk][plan.profile.months])/100
    if invested>cap*equity_limit or any(p.cost>cap*stock_limit for p in holdings):
        raise ValueError("Allocation exceeds model limits")
    approved={r["symbol"]:r for r in artifact["companies"]}
    if any(p.candidate.symbol not in approved for p in holdings):
        raise ValueError("Missing source evidence")
    sectors=defaultdict(Decimal)
    for p in holdings:
        sectors[p.candidate.industry]+=p.cost
    sector_rows=sorted(sectors.items(),key=lambda v:(-v[1],v[0]))
    risk=_risk_data(plan,artifact,optimized)
    pages=4 if risk is not None else 3
    buf=io.BytesIO()
    c=canvas.Canvas(buf,pagesize=A4,pageCompression=1)
    c.setTitle("MSK Portfolio Builder | Investment research")
    _page(c,"Portfolio intelligence",1,pages,plan,fictional)
    _label(c,f"{plan.profile.risk.title()} risk preference  /  {plan.profile.months} months",L,H-142,size=12,bold=True)
    w=(R-L-12)/2
    cards=[("Investment capital",_money(cap),"Total model budget"),
           ("Equity allocation",_money(invested),f"{invested/cap:.2%} of total capital"),
           ("Cash reserve",_money(plan.cash),f"{plan.cash/cap:.2%} of total capital"),
           ("Portfolio breadth",str(len(holdings))+" stocks",f"{len(sectors)} industry groups")]
    for i,(name,value,detail) in enumerate(cards):
        _metric(c,L+(i%2)*(w+12),H-224-(i//2)*78,w,name,value,detail)
    _section(c,"Capital distribution",L,H-329)
    cx,cy,r=L+93,H-440,73
    c.setStrokeColor(HexColor(PALE))
    c.setLineWidth(26)
    c.circle(cx,cy,r,stroke=1,fill=0)
    if invested:
        c.setStrokeColor(HexColor(BLUE))
        c.arc(cx-r,cy-r,cx+r,cy+r,startAng=90,extent=-360*float(invested/cap))
    c.setFillColor(HexColor(NAVY))
    c.setFont("Helvetica-Bold",21)
    c.drawCentredString(cx,cy+2,f"{invested/cap:.1%}")
    c.setFont("Helvetica",9)
    c.drawCentredString(cx,cy-15,"Equity")
    for i,(label,value,color) in enumerate([("Equities",invested,BLUE),("Modeled cash",plan.cash,MUTED)]):
        yy=cy+35-i*53
        _panel(c,L+204,yy-8,10,10,color)
        _label(c,label,L+224,yy,bold=True)
        _right(c,f"{value/cap:.2%}",R,yy,bold=True)
        _label(c,_money(value),L+224,yy-18,size=10,color=MUTED)
    _section(c,"Allocation discipline",L,H-553)
    y=H-582
    for label,value in [("Maximum stock allocation",f"{stock_limit:.0%} of capital"),
                        ("Equity ceiling for selected profile",f"{equity_limit:.0%}"),
                        ("Largest actual holding",f"{max((p.cost/cap for p in holdings),default=Decimal(0)):.2%}"),
                        ("Method","Minimum historical variance" if optimized else "Capped equal weight")]:
        _label(c,label,L,y,size=10,color=MUTED)
        _right(c,value,R,y,size=10,bold=True)
        y-=23
    _panel(c,L,67,R-L,46)
    _label(c,"Model limitations",L+10,96,size=10,bold=True)
    _label(c,"Dated closes are not executable prices. Risk preference is not a suitability assessment.",L+10,81,size=8,width=R-L-20)
    if not holdings:
        _label(c,"No stocks fit the model limits; capital remains entirely in modeled cash.",L,H-700,size=9,bold=True)
    c.showPage()

    _page(c,"Stock allocation",2,pages,plan,fictional)
    _label(c,"Whole-share sizing | Weight as % of total capital",L,H-141,size=10,color=MUTED)
    y=H-166
    _panel(c,L,y-9,R-L,23,NAVY)
    _label(c,"Symbol / industry",L+7,y,size=8,color=WHITE,bold=True)
    for label,x in [("Shares",239),("Close (Rs)",322),("Value (Rs)",435),("Weight",R-9)]:
        _right(c,label,x,y,size=8,bold=True,color=WHITE)
    y-=25
    for i,p in enumerate(holdings):
        if i%2==0: _panel(c,L,y-13,R-L,25)
        _label(c,p.candidate.symbol,L+7,y+2,bold=True,size=9,width=153)
        _label(c,p.candidate.industry,L+7,y-9,size=7,color=MUTED,width=153)
        _right(c,f"{p.shares:,}",239,y,size=9)
        _right(c,_indian(p.candidate.close_rupees),322,y,size=9)
        _right(c,_indian(p.cost),435,y,size=9)
        _right(c,f"{p.cost/cap:.2%}",R-9,y,size=9,bold=True)
        y-=26
    if not holdings:
        _label(c,"No stock positions.",L+7,y,size=11)
        y-=25
    _panel(c,L,y-14,R-L,27,NAVY)
    _label(c,"Total equity value",L+7,y-2,color=WHITE,bold=True)
    _right(c,_indian(invested),435,y-2,bold=True,color=WHITE)
    _right(c,f"{invested/cap:.2%}",R-9,y-2,bold=True,color=WHITE)
    _label(c,"Cash reserve: "+_money(plan.cash),L,y-34,size=10,bold=True)
    _label(c,"Transaction costs and taxes are not included.",L,y-48,size=8,color=MUTED)
    c.showPage()

    _page(c,"Diversification & evidence",3,pages,plan,fictional)
    _section(c,"Industry exposure",L,H-146)
    _section(c,"Company evidence",L+285,H-146)
    _label(c,"Total capital basis",L,H-168,size=8,color=MUTED)
    _label(c,"Selected names / source links",L+285,H-168,size=8,color=MUTED)
    y=H-191
    for sector,amount in sector_rows:
        _label(c,sector,L,y,bold=True,size=8,width=173)
        _right(c,f"{amount/cap:.2%}",L+256,y,size=8)
        _bar(c,L,y-10,256,amount/cap,TEAL)
        y-=24
    if not sector_rows:
        _label(c,"No equity exposure.",L,y,size=9)
        y-=24
    _label(c,f"Cash: {plan.cash/cap:.2%}",L,y-3,bold=True,size=9)
    y=H-191
    for p in holdings:
        row=approved[p.candidate.symbol]
        _label(c,p.candidate.symbol,L+285,y,bold=True,size=8,width=105)
        for x,key,label in [(L+397,"filing_url","Filing"),(L+452,"price_source_url","Price")]:
            _label(c,"Demo" if fictional else label,x,y,size=8,color=MUTED if fictional else BLUE)
            if not fictional:
                c.linkURL(row[key],(x,y-2,x+42,y+9),relative=0,thickness=0)
        y-=24
    if not holdings:
        _label(c,"No selected-company references.",L+285,y,size=9)
    _panel(c,L,72,R-L,50)
    _label(c,"Evidence boundary",L+10,105,bold=True,size=9)
    _label(c,"Scores and source approval come from the research artifact, not the PDF renderer.",L+10,91,size=8,width=R-L-20)
    _label(c,"Fundamentals and benchmark metrics are omitted when no validated dataset exists.",L+10,79,size=8,width=R-L-20)
    c.showPage()

    if risk is not None:
        _page(c,"Historical risk intelligence",4,pages,plan,fictional)
        _label(c,f"{risk['observations']} observations | {risk['first']} to {risk['last']}",L,H-143,size=10,color=MUTED)
        _metric(c,L,H-226,w,"Annualized volatility",f"{risk['volatility']:.2%}","252-session convention")
        _metric(c,L+w+12,H-226,w,"Maximum drawdown",f"{risk['max_drawdown']:.2%}","Historical model decline")
        _section(c,"Historical model wealth",L,H-252)
        _series(c,L+32,H-359,R-L-38,85,risk["wealth"],BLUE)
        _section(c,"Stock return correlation",L,H-390)
        names=risk["symbols"]
        n=len(names)
        cell=min(24,258/n)
        x=L+140
        top=H-423
        for i,name in enumerate(names):
            _label(c,name,L,top-i*cell-cell*.75,size=min(8,cell*.65),width=135)
            c.saveState()
            c.translate(x+i*cell+cell*.7,top+4)
            c.rotate(55)
            _label(c,name,0,0,size=min(7,cell*.6),width=80)
            c.restoreState()
            for j in range(n):
                v=float(risk["corr"][i,j])
                base=(21,151,142) if v>=0 else (212,110,69)
                strength=abs(v)
                fill="#%02x%02x%02x"%tuple(int(242*(1-strength)+k*strength) for k in base)
                c.setFillColor(HexColor(fill))
                c.rect(x+j*cell,top-(i+1)*cell,cell-.5,cell-.5,fill=1,stroke=0)
                if n<=10:
                    _label(c,f"{v:.1f}",x+j*cell+2,top-(i+.72)*cell,size=6)
        _label(c,"Orange: negative   |   Pale: near zero   |   Teal: positive",L,top-n*cell-15,size=8,color=MUTED)
        _label(c,"Current weights held constant daily; cash earns zero. Not a realized track record.",L,74,size=8,width=R-L)
        _label(c,"In-sample historical illustration, not a forecast or independent strategy backtest.",L,61,size=8,width=R-L)
        c.showPage()
    c.save()
    raw=buf.getvalue()
    from pypdf import PdfReader
    if not 1000<=len(raw)<=2*1024*1024 or len(PdfReader(io.BytesIO(raw),strict=True).pages)!=pages:
        raise ValueError("Invalid report")
    return raw
