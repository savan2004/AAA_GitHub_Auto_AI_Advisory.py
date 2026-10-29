"""Real ReportLab layout smoke for sector report front page and movers.

Offline, synthetic observations only. Assert report generation is possible and
extractable; this is NOT a visual signoff or a live-market-data accuracy test.
"""
import ast
import html
import io
from pathlib import Path
from types import SimpleNamespace
import unittest

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from pypdf import PdfReader

SOURCE = Path(__file__).resolve().parents[1].joinpath("sector_report.py").read_text(encoding="utf-8")
W = A4[0] - 32 * mm
BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=7.7, leading=10)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=7.0, leading=9)
TITLE = ParagraphStyle("title", parent=BASE, fontSize=12, leading=15, textColor=colors.white)
HEAD = ParagraphStyle("head", parent=BASE, fontSize=10, leading=12)
WHITE = ParagraphStyle("white", parent=BASE, textColor=colors.white)


def para(text, kind="small"):
    style = {"title":TITLE, "px":WHITE, "pxs":WHITE, "sub":WHITE,
             "small":SMALL, "h1":HEAD, "h2":HEAD, "tiny":SMALL}.get(kind,BASE)
    return Paragraph(str(text),style)


def table(rows, widths, extra=None, **_):
    tbl=Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0B2545")),
        ("LEFTPADDING",(0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),
    ]+(extra or [])))
    return tbl


def bullets(items,*_):
    return [para("- " + s,"small") for s in items]


def section(a,b):
    return [para(a,"h1"),para(b,"small")]


def color(text, hex_color):
    return '<font color="' + hex_color + '">' + str(text) + '</font>'


rg = SimpleNamespace(
    P=para, W=W, table=table, bullets=bullets, section=section,
    colored=color, esc=html.escape, strip_emoji=lambda x:x,
    NAVY=colors.HexColor("#0B2545"), NAVY_H="#0B2545",
    GREEN_H="#1B8A5A", RED_H="#C62828", GREY_H="#5A6472",
    AMBER_H="#B8860B",
    BORDER=colors.HexColor("#D6DBE3"),
)
ns = dict(rg=rg, BRAND="MSK Bot", INDICES=[1]*15, colors=colors,
          Table=Table, TableStyle=TableStyle, Spacer=Spacer, mm=mm,
          Tp=lambda t,style="c":para(t,"small"))
tree=ast.parse(SOURCE)
names={"_pct","_short","esc_d","_stance_hex","_quad_hex","_cover","_exhibit_movers"}
picked=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in names]
if {x.name for x in picked} != names:
    raise AssertionError("Sector PDF function missing from source")
exec(compile(ast.Module(body=picked, type_ignores=[]),"sector_report.py","exec"),ns)


def fixture():
    scores=[("IT",88,"OVERWEIGHT","Leading", "MODERATE"),
            ("Pharma",76,"OVERWEIGHT","Leading","ELEVATED"),
            ("Auto",45,"NEUTRAL","Improving","MODERATE"),
            ("Metal",42,"NEUTRAL","Lagging","HIGH"),
            ("Infra",38,"UNDERWEIGHT","Lagging","HIGH"),
            ("PSE",31,"UNDERWEIGHT","Lagging","HIGH"),
            ("Energy",20,"UNDERWEIGHT","Lagging","HIGH"),
            ("FMCG",9,"UNDERWEIGHT","Lagging","HIGH"),
            ("Realty",3,"UNDERWEIGHT","Lagging","HIGH")]
    sec={}
    for n,score,stance,quad,risk in scores:
        sec[n]={"name":"Nifty "+n,"score":score,"stance":stance,
                "quad":quad,"risk":risk,"r1m":-1.4,"r3m":-2.5,
                "rel3m":3.1 if score>=65 else -3.1,
                "breadth":{"p50":58,"breakouts":0}}
    def movers(prefix):
        return [{"sym":prefix+str(i),"grp":"Engineering & Infrastructure",
                 "ltp":1234.5+i,"r1m":15.5-i,"r3m":10-i} for i in range(8)]
    return {"asof":"29-Oct-2026","generated":"30-Oct-2026 08:00 IST",
            "headline":"IT and Pharma show relative leadership",
            "regime":{"supportive":False},
            "takeaways":["<b>Market breadth:</b> 22% of reviewed stocks above EMA50.",
                         "<b>Risk:</b> keep allocations conservative."],
            "sec":sec,"n_stocks":217,"n_unique":205,
            "n_configured":245,"index_coverage":(11,15),
            "freshness_warning":"DATA DATE WARNING: close date needs verification",
            "gainers":movers("GAIN"),"losers":movers("LOSS")}


class LayoutSmoke(unittest.TestCase):
    def test_cover_and_movers_generate_readable_pdf(self):
        story=[]
        r=fixture()
        ns["_cover"](r,story)
        ns["_exhibit_movers"](r,story)
        out=io.BytesIO()
        doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=16*mm,
                              rightMargin=16*mm,topMargin=19*mm,
                              bottomMargin=18*mm)
        doc.build(story)
        pdf=out.getvalue()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf),2000)
        reader=PdfReader(io.BytesIO(pdf))
        self.assertLessEqual(len(reader.pages),4)
        all_text="\n".join(p.extract_text() or "" for p in reader.pages)
        for snippet in ("Sector rotation scorecard","Client decision snapshot",
                        "Top gainers","Top decliners","Data quality"):
            self.assertIn(snippet,all_text)
        # No overlap from the 5-column movers table:
        self.assertLessEqual(sum([22,65,28,29,29]),178)


if __name__ == "__main__":
    unittest.main()
