"""In-memory fictional report; deliberately unavailable for production research."""
import argparse
from io import BytesIO
from pathlib import Path
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_demo import build_demo_basket, synthetic_companies

NAVY = HexColor("#18334E")
TEAL = HexColor("#197F81")
GRAY = HexColor("#526575")
PALE = HexColor("#E9F1F5")

def demo_pdf(profile):
    basket = build_demo_basket(profile, synthetic_companies())
    stream = BytesIO()
    pdf = Canvas(stream, pagesize=A4, pageCompression=1)
    width,height = A4
    page = 0
    total = 6 + 2*len(basket.positions)
    pdf.setTitle("MSK Portfolio Builder | Fictional Demonstration")
    def heading(title, subtitle):
        nonlocal page
        page += 1
        pdf.setFillColor(NAVY)
        pdf.rect(0,height-103,width,103,fill=1,stroke=0)
        pdf.setFillColor(HexColor("#FFFFFF"))
        pdf.setFont("Helvetica-Bold",10)
        pdf.drawString(40,height-27,"MSK | FINANCIAL INTELLIGENCE")
        pdf.setFont("Helvetica-Bold",19)
        pdf.drawString(40,height-55,title)
        pdf.setFont("Helvetica",10)
        pdf.drawString(40,height-77,subtitle)
        pdf.setFillColor(TEAL)
        pdf.setFont("Helvetica-Bold",11)
        pdf.drawString(40,height-127,"FICTIONAL DEMONSTRATION - NOT INVESTMENT RESEARCH")
        pdf.setFillColor(GRAY)
        pdf.setFont("Helvetica",9)
        pdf.drawString(40,28,"Synthetic data only | No live prices, forecasts or verified stocks")
        pdf.drawRightString(width-40,28,f"{page}/{total}")
    def line(s,y,size=11,bold=False):
        pdf.setFillColor(NAVY if bold else GRAY)
        font = "Helvetica-Bold" if bold else "Helvetica"
        pdf.setFont(font,size)
        for part in simpleSplit(str(s),font,size,width-80):
            pdf.drawString(40,y,part)
            y -= size+5
        return y
    def bar(label, value, maximum, y):
        pdf.setFillColor(GRAY)
        pdf.setFont("Helvetica",9)
        pdf.drawString(40,y+6,label)
        pdf.setFillColor(PALE)
        pdf.rect(160,y,280,12,fill=1,stroke=0)
        pdf.setFillColor(TEAL)
        pdf.rect(160,y,280*value/maximum,12,fill=1,stroke=0)
        pdf.setFont("Helvetica",9)
        pdf.drawString(451,y+2,f"{value:,.0f}")
    def finish():
        pdf.showPage()
    capital=profile.capital_rupees
    equity=capital-basket.reserve
    heading("Portfolio dashboard","Executive summary | illustrative model")
    line(f"Capital: INR {capital:,}",height-180,24,True)
    line(f"Risk preference: {profile.risk.title()} | Horizon: {profile.months} months",height-222,12)
    line(f"Equity: INR {equity:,} | Reserve: INR {basket.reserve:,}",height-254,13,True)
    line(f"Companies selected: {len(basket.positions)} of maximum {profile.max_stocks}",height-285)
    bar("Equity",equity,capital,height-340)
    bar("Reserve",basket.reserve,capital,height-375)
    line("The demonstration illustrates the customer experience. Actual investment suitability, data availability and regulatory permissions have not been established.",height-430)
    finish()
    heading("Research methodology","Transparent prototype | uncalibrated rules")
    y=height-180
    for s in ("Input: capital, risk preference and time horizon.",
              "Candidate universe: invented businesses only, not Nifty 500 members.",
              "Ranking: fundamental quality 25, growth 20, valuation 20, technical 15, risk 10 and sector balance-sheet criteria 10.",
              "Prototype threshold: 75/100; not a success probability.",
              "Bank criteria differ from non-bank criteria.",
              "No backtest completed; no real-world performance claims.",
              "Shorter horizons and low risk preferences receive greater reserve allocations."):
        y=line(s,y)-18
    finish()
    heading("Capital allocation","No share quantities or live execution prices")
    y=height-168
    for pos in basket.positions:
        y=line(f"{pos.company.label} | {pos.company.sector} | INR {pos.rupees:,} | {100*pos.rupees/capital:.1f}% | {pos.score}/100",y,10)-8
    y=line(f"Reserve: INR {basket.reserve:,} | Total: INR {capital:,}",y-12,12,True)
    line("Unallocated capital remains in the reserve; no candidate is forced into the illustrative basket.",y-16,10)
    finish()
    for pos in basket.positions:
        c=pos.company
        heading(c.label,"Business overview and annual results")
        y=line(c.overview,height-168,12)-20
        y=line(f"Industry: {c.sector} | Allocation: INR {pos.rupees:,} | Score: {pos.score}/100",y,11,True)-20
        y=line("Five synthetic annual periods (INR crore): revenue / profit",y,11,True)-12
        for i,(revenue,profit) in enumerate(c.annual,1):
            y=line(f"FY {i}    Revenue {revenue:,.0f}     Net profit {profit:,.0f}",y)-7
        y-=15
        for i,(revenue,_) in enumerate(c.annual,1):
            bar(f"FY {i} revenue",revenue,max(r for r,_ in c.annual),y)
            y-=25
        finish()
        heading(c.label,"Quarterly performance and financial interpretation")
        y=line("Eight fictional quarters (INR crore): revenue / profit",height-168,11,True)-12
        for i,(revenue,profit) in enumerate(c.quarters,1):
            y=line(f"Quarter {i}    {revenue:,.0f} / {profit:,.0f}",y,10)-3
        y-=14
        y=line(f"ROE: {c.roe}% | {'Capital adequacy' if c.bank else 'ROCE'}: {c.quality}% | {'Gross NPA %' if c.bank else 'Debt/equity x'}: {c.balance_risk}",y,10)-8
        y=line(f"Peer-relative valuation multiple: {c.relative_value:.2f} | Excess return: {c.relative_strength}% | Drawdown: {c.drawdown}%",y,10)-12
        growth=((c.annual[-1][0]/c.annual[0][0])**.25-1)*100
        profit_growth=((c.annual[-1][1]/c.annual[0][1])**.25-1)*100
        y=line(f"Computed summary: fictional annual revenue CAGR {growth:.2f}%; profit CAGR {profit_growth:.2f}%. The values illustrate the scoring rubric, not a real business.",y)-12
        line("Risk review would require verified disclosures, competitive position, valuation basis and governance evidence before publication.",y)
        finish()
    heading("Financial comparison","Synthetic values only")
    y=height-169
    if not basket.positions:
        y=line("No fictional candidate was allocated under this profile. There are no company ratios to compare.",y)
    for p in basket.positions:
        c=p.company
        y=line(f"{c.label} | ROE {c.roe:.1f}% | Peer value {c.relative_value:.2f}x | Drawdown {c.drawdown:.1f}% | Score {p.score}",y,10)-8
    finish()
    heading("Portfolio stress test","Uniform hypothetical equity shock | reserve held constant")
    y=height-175
    for shock in (-30,-15,0,15,30):
        value=capital+equity*shock/100
        y=line(f"Equity shock {shock:+d}%    Portfolio INR {value:,.2f}",y,13,True)-25
    line("This sensitivity calculation excludes taxes, costs, interest and differences among individual businesses. It is not a forecast or guarantee.",y)
    finish()
    heading("Verification and limitations","Publication blocked for real stocks")
    y=height-180
    for s in ("Source: synthetic fixture embedded in the demo module.",
              "Primary filings, current index membership, market quotations, ownership, valuation and governance not verified.",
              "No live data or personalized recommendations are supplied.",
              "AI narrative generation, live source authentication, backtesting and regulatory review remain required.",
              "Report is produced in memory. CLI writes only the explicitly requested output file."):
        y=line(s,y)-20
    finish()
    pdf.save()
    if page!=total:
        raise RuntimeError("Page count invariant failed")
    return stream.getvalue()

def main():
    parser=argparse.ArgumentParser(description="Generate fictional MSK Builder demonstration PDF")
    parser.add_argument("--capital",type=int,required=True)
    parser.add_argument("--risk",choices=("low","medium","high"),required=True)
    parser.add_argument("--months",type=int,choices=(6,18,36,60),required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    args.output.write_bytes(demo_pdf(ResearchProfile(args.capital,args.risk,args.months)))

if __name__=="__main__":
    main()
