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
        if urlsplit(row.get("url","")).scheme != "https":
            raise ResearchMetricError("Issuer source must use HTTPS")
        if row.get("name") == "TCS":
            if (row.get("reporting_currency") != "INR"
                    or row.get("reported_revenue_inr_crore") != 73188
                    or row.get("usd_comparable") is not False
                    or "nsearchives.nseindia.com/corporate/ixbrl/" not in row["url"]):
                raise ResearchMetricError("TCS requires official consolidated INR-only NSE figures")
        elif (row.get("reporting_currency") != "USD"
              or not isinstance(row.get("usd_revenue_yoy_pct"), (int, float))
              or isinstance(row.get("usd_revenue_yoy_pct"), bool)
              or not math.isfinite(row["usd_revenue_yoy_pct"])):
            raise ResearchMetricError("Invalid comparable USD issuer revenue growth")
    return pack


def _t(c,x,y,s,size=9,color=NAVY,bold=False):
    c.setFont("Helvetica-Bold" if bold else "Helvetica",size)
    c.setFillColor(HexColor(color))
    c.drawString(x,y,str(s))


def _rect(c,x,y,w,h,color):
    c.setFillColor(HexColor(color))
    c.rect(x,y,w,h,fill=1,stroke=0)


def _bars(c, rows, *, x=55, y=507, width=478, height=143,
          label_width=132, value_key="value", title="", suffix="%",
          max_rows=8):
    """Accurately scaled divergence plot with legible A4 labels.

    Bars share a zero baseline and one symmetrical scale; never use
    category-specific axes that exaggerate relative performance.
    """
    rows = rows[:max_rows]
    if not rows:
        raise ResearchMetricError("Cannot render unverified empty comparison")
    _t(c, x, y+height+4, title, 9.0, TEAL, True)
    magnitudes = [abs(float(r[value_key])) for r in rows]
    if not all(math.isfinite(v) for v in magnitudes):
        raise ResearchMetricError("Non-finite research chart value")
    scale = max([1.0] + magnitudes)
    left = x + label_width
    right = x + width - 58
    center = (left + right) / 2
    usable = (right - left) / 2 - 9
    row_h = (height - 22) / len(rows)
    c.setStrokeColor(HexColor("#A6B9BD"))
    c.setLineWidth(.8)
    c.line(center, y + 1, center, y + height - 17)
    for i, row in enumerate(rows):
        value = float(row[value_key])
        ry = y + height - 26 - (i + .5) * row_h
        # Fine horizontal separators are editorial guides, not metric marks.
        if i:
            c.setStrokeColor(HexColor("#D6E3E2"))
            c.setLineWidth(.4)
            c.line(x, ry + row_h/2 + 1, x + width, ry + row_h/2 + 1)
        _t(c, x, ry + 1, str(row["name"])[:27], 8.0, NAVY)
        bw = usable * abs(value) / scale
        color = TEAL if value >= 0 else NEG
        _rect(c, center if value >= 0 else center - bw, ry - 1.6,
              max(1.2, bw), 8.0, color)
        _t(c, right + 8, ry + 1, f"{value:+.2f}{suffix}", 8.0, color, True)


def _tiles(c, tiles, *, title, x=57, y=515, width=470, height=134):
    """Large numeric editorial figures with dates beside the measures."""
    _t(c,x,y+height+3,title,9.0,TEAL,True)
    cols = 2
    cw = (width - 11) / cols
    ch = (height - 23) / 2
    for i,(label,val,detail) in enumerate(tiles[:4]):
        col, row = i % 2, i // 2
        xx = x + col * (cw+11)
        yy = y + (1-row)*(ch+4)
        _rect(c,xx,yy,cw-2,ch,WHITE)
        _rect(c,xx,yy,3,ch,TEAL)
        _t(c,xx+12,yy+ch-15,label[:31],8.1,MUTED)
        _t(c,xx+12,yy+14,val,17.2,TEAL,True)
        _t(c,xx+120,yy+16,detail[:27],7.8,MUTED)


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
        return [{"name":name,"value":indices[name][key]} for name in names]
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
        _bars(c,[{"name":r["name"],"value":r["m1"]}
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
        # TCS original NSE filing is INR-only; comparing it against
        # USD YoY growth would silently mix currencies and conventions.
        _bars(c,[{"name":r["name"],"value":r["usd_revenue_yoy_pct"]}
                 for r in pack["issuer_results"] if r.get("reporting_currency") == "USD"],
              title="Q2 FY27 USD YOY GROWTH - TCS INR FILING EXCLUDED")
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
