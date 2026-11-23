"""Vector research charts for the MSK Design 3 editorial issue.

All visualisations are derived from a dated local evidence register. No
synthetic candle series, inferred prices, benchmark forecasts or trade signals.
The evidence register remains publication-gated until independent approval.
"""
from __future__ import annotations
from datetime import date
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from reportlab.lib.colors import HexColor

NAVY = "#132B46"
TEAL = "#007D84"
NEG = "#C45551"
MUTED = "#536A78"
LIGHT = "#EEF5F6"
WHITE = "#FFFFFF"
TINT = "#D8EDF0"
DASHBOARD_URL = "https://www.niftyindices.com/Index_Dashboard/Index_Dashboard_OCT2026.pdf"


class ResearchMetricError(ValueError):
    pass


def load_metrics(path: Path, issue: str) -> dict:
    pack = json.loads(Path(path).read_text(encoding="utf-8"))
    if pack.get("issue") != issue:
        raise ResearchMetricError("Research pack issue mismatch")
    if date.fromisoformat(pack["as_of"]) < date(2026, 11, 23):
        raise ResearchMetricError("Research register is older than approved reference cutoff")
    m = pack.get("market_source", {})
    if m.get("url") != DASHBOARD_URL or m.get("report_date") != "2026-10-31":
        raise ResearchMetricError("Unsupported/unmatched NSE dashboard evidence source")
    if m.get("return_convention", "").startswith("Total-return") is False:
        raise ResearchMetricError("Index-return basis not declared")
    indices = pack.get("market_indices")
    sectors = pack.get("sectors")
    issuers = pack.get("issuer_results")
    if not isinstance(indices, list) or len(indices) != 6:
        raise ResearchMetricError("Broad market comparison has missing rows")
    if not isinstance(sectors, list) or len(sectors) != 8:
        raise ResearchMetricError("Sector evidence incomplete")
    if not isinstance(issuers, list) or len(issuers) != 3:
        raise ResearchMetricError("Issuer comparison incomplete")
    if len({x["name"] for x in indices}) != 6:
        raise ResearchMetricError("Duplicate broad-market index")
    if len({x["name"] for x in sectors}) != 8:
        raise ResearchMetricError("Duplicate sector")
    for row in indices:
        for k in ("m1", "m3", "y1", "vol", "pe", "pb", "dy"):
            number = row.get(k)
            if not isinstance(number, (float, int)) or isinstance(number, bool):
                raise ResearchMetricError("Non-numeric " + k)
            if not math.isfinite(number) or abs(number) > 1000:
                raise ResearchMetricError("Invalid metric " + k)
    for row in sectors:
        v = row.get("m1")
        if not isinstance(v, (float, int)) or not math.isfinite(v) or abs(v)>100:
            raise ResearchMetricError("Invalid sector return")
    for row in issuers:
        if not isinstance(row.get("usd_revenue_yoy_pct"),(int,float)):
            raise ResearchMetricError("Invalid issuer revenue growth")
        if urlsplit(row.get("url","")).scheme != "https":
            raise ResearchMetricError("Issuer source must use HTTPS")
    return pack


def _t(c,x,y,s,size=9,color=NAVY,bold=False):
    c.setFont("Helvetica-Bold" if bold else "Helvetica",size)
    c.setFillColor(HexColor(color))
    c.drawString(x,y,str(s))


def _rect(c,x,y,w,h,color):
    c.setFillColor(HexColor(color))
    c.rect(x,y,w,h,fill=1,stroke=0)


def _bars(c, rows, *, x=55, y=507, width=478, height=143,
          label_width=126, value_key="value", title="", suffix="%",
          max_rows=8):
    rows = rows[:max_rows]
    _t(c,x,y+height+4,title,9.2,TEAL,True)
    positive=[float(r[value_key]) for r in rows if float(r[value_key])>0]
    negative=[abs(float(r[value_key])) for r in rows if float(r[value_key])<0]
    scale=max([1.0]+positive+negative)
    left=x+label_width
    right=x+width-48
    center=(left+right)/2
    usable=(right-left)/2-12
    c.setStrokeColor(HexColor("#A7BEC4"))
    c.setLineWidth(.7)
    c.line(center,y+4,center,y+height-16)
    row_h=(height-19)/max(1,len(rows))
    for i,row in enumerate(rows):
        num=float(row[value_key])
        ry=y+height-26-(i+0.5)*row_h
        _t(c,x,ry,str(row["name"])[:24],7.1,NAVY)
        bw=usable*abs(num)/scale
        _rect(c,center if num>=0 else center-bw,ry-1,max(1.0,bw),7.5,
              TEAL if num>=0 else NEG)
        _t(c,right+8,ry,f"{num:+.2f}{suffix}",7.1,TEAL if num>=0 else NEG,True)


def _tiles(c, tiles, *, title, x=57, y=515, width=470, height=134):
    _t(c,x,y+height+3,title,9.2,TEAL,True)
    cols=2
    rows=2
    cw=(width-10)/cols
    ch=(height-22)/rows
    for i,(label,val,detail) in enumerate(tiles[:4]):
        col=i%2
        row=i//2
        xx=x+col*(cw+10)
        yy=y+(1-row)*(ch+3)
        _rect(c,xx,yy,cw-3,ch,WHITE)
        _t(c,xx+11,yy+ch-16,label[:33],7.8,MUTED)
        _t(c,xx+11,yy+16,val,15,TEAL,True)
        _t(c,xx+83,yy+17,detail[:29],7,MUTED)


def _events(c, events, *, x=59, y=519):
    _t(c,x,y+132,"UPCOMING POLICY AND DATA EVENTS",9.2,TEAL,True)
    for i,item in enumerate(events):
        yy=y+101-i*43
        _rect(c,x+4,yy-6,8,8,TEAL)
        _t(c,x+23,yy,item["date"],10,NAVY,True)
        _t(c,x+107,yy,item["event"][:63],8.1,MUTED)


def draw_research_chart(c, page: int, pack: dict):
    """Draw one real-data vector chart within the locked A4 top evidence panel."""
    indices={x["name"]:x for x in pack["market_indices"]}
    def group(names,key="m1"):
        return [{"name":name.replace("Nifty ",""),"value":indices[name][key]} for name in names]
    if page==3:
        _bars(c,group(["Nifty 50","Nifty Bank","Nifty 500","Nifty Midcap 150","Nifty Smallcap 250"]),
              title="OCTOBER 2026 INDEX TOTAL RETURNS - 1 MONTH")
    elif page==4:
        m={x["name"]:x for x in pack["macro"]}
        _tiles(c,[
            ("Policy repo rate",f'{m["RBI policy repo rate"]["value"]:.2f}%',"7 Oct 2026"),
            ("Headline CPI YoY",f'{m["October CPI YoY"]["value"]:.2f}%',"Oct 2026"),
            ("Food CPI YoY",f'{m["October food CPI YoY"]["value"]:.2f}%',"Oct 2026"),
            ("Industrial output YoY",f'{m["September IIP YoY"]["value"]:.1f}%',"Sep 2026")],
            title="OFFICIAL MACRO SNAPSHOT - MIXED OBSERVATION DATES")
    elif page==5:
        row=indices["Nifty 50"]
        _bars(c,[{"name":"1 month","value":row["m1"]},
                 {"name":"3 months","value":row["m3"]},
                 {"name":"1 year","value":row["y1"]}],
              title="NIFTY 50 TOTAL RETURNS - PERIODS ARE NOT A PRICE SERIES")
    elif page==6:
        _bars(c,group(["Nifty 50","Nifty Bank","Nifty 500"]),
              title="OCTOBER 2026: BANK VS BROAD INDICES")
    elif page==7:
        _bars(c,group(["Nifty 500","Nifty 500 Equal Weight"]),
              title="NIFTY 500 WEIGHTED VS EQUAL WEIGHT - BREADTH PROXY ONLY")
    elif page==8:
        _bars(c,[{"name":r["name"].replace("Nifty ",""),"value":r["m1"]}
                 for r in pack["sectors"]],
              title="SECTOR INDEX RETURNS - OCTOBER 2026")
    elif page in (9,10,11):
        name={9:"Nifty 50",10:"Nifty Midcap 150",11:"Nifty Smallcap 250"}[page]
        r=indices[name]
        _tiles(c,[
            ("1-month TRI return",f'{r["m1"]:+.2f}%',"October 2026"),
            ("1-year volatility",f'{r["vol"]:.2f}%',"Annualised"),
            ("Index P/E",f'{r["pe"]:.2f}x',"As at 31 Oct"),
            ("Dividend yield",f'{r["dy"]:.2f}%',"As at 31 Oct")],
            title=name.upper()+" - VALUATION & RISK SNAPSHOT")
    elif page==12:
        _bars(c,[{"name":r["name"],"value":r["usd_revenue_yoy_pct"]}
                 for r in pack["issuer_results"]],
              title="ISSUER Q2 FY27 REVENUE GROWTH YOY - USD REPORTED")
    elif page==13:
        m={x["name"]:x for x in pack["macro"]}
        _tiles(c,[
            ("Policy repo rate",f'{m["RBI policy repo rate"]["value"]:.2f}%',"Oct MPC"),
            ("CPI inflation",f'{m["October CPI YoY"]["value"]:.2f}%',"Oct reading"),
            ("Nifty 50 1M",f'{indices["Nifty 50"]["m1"]:+.2f}%',"October TRI"),
            ("Smallcap 250 1M",f'{indices["Nifty Smallcap 250"]["m1"]:+.2f}%',"October TRI")],
            title="STRATEGY LAB - OBSERVED INPUTS, NOT TRADE SIGNALS")
    elif page==14:
        _events(c,pack["calendar"])
    elif page==15:
        _bars(c,group(["Nifty 50","Nifty 500","Nifty Midcap 150","Nifty Smallcap 250"]),
              title="OCTOBER RISK MAP - INDEX RETURN DISPERSION")
    elif page==16:
        _tiles(c,[
            ("Official index tables","31 Oct","NSE Indices"),
            ("CPI release","12 Nov","MoSPI/PIB"),
            ("RBI resolution","07 Oct","RBI"),
            ("Issuer Q2 reports","Oct","Issuer IR")],
            title="DATED PRIMARY SOURCES - SOURCE REGISTER")
