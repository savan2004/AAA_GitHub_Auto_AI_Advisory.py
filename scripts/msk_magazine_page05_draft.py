#!/usr/bin/env python3
"""Generate a single, fail-closed provisional Design 3 Page 05 PDF from its manifest."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

NAVY=HexColor('#132B46'); TEAL=HexColor('#007D84'); GRAY=HexColor('#536A78'); PALE=HexColor('#EEF5F6'); RED=HexColor('#B44C3D'); AMBER=HexColor('#B67A12')


def generate_page05(manifest_path: Path, output_path: Path) -> str:
    m=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if m.get('page') != 5 or m.get('locked_title') != 'Nifty 50 Technical Outlook':
        raise ValueError('Manifest is not the locked Design 3 Page 05')
    if m.get('magazine_assembly_allowed') is True:
        raise ValueError('This provisional secondary-source renderer cannot accept a magazine-assembly gate')
    reports=m['close_reports']; o=m['reported_ohlc_secondary']; levels=m['provisional_levels_secondary']
    values=[float(x['value']) for x in reports]
    agreement=sorted(set(values))
    if len(agreement)<2:
        raise ValueError('Expected conflicting close reports; draft must display source reconciliation')
    output_path=Path(output_path); output_path.parent.mkdir(parents=True,exist_ok=True)
    W,H=A4; x=17*mm; w=W-34*mm
    c=canvas.Canvas(str(output_path),pagesize=A4,invariant=1)
    c.setTitle('MSK 2026-11 Page 05 — Nifty 50 Technical Outlook (Provisional Draft)')
    c.setAuthor('MSK Monthly Magazine — Unapproved Research Draft')
    c.setFillColor(TEAL); c.rect(x,H-24*mm,19*mm,2.1*mm,fill=1,stroke=0)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',17); c.drawString(x,H-35*mm,'NIFTY 50 TECHNICAL OUTLOOK')
    c.setFillColor(GRAY); c.setFont('Helvetica',8.5); c.drawString(x,H-42*mm,'NOVEMBER 2026  |  DESIGN 3: LINE-BENCHMARK  |  RESEARCH DRAFT')
    y=H-80*mm; h=30*mm
    c.setFillColor(PALE); c.roundRect(x,y,w,h,3*mm,fill=1,stroke=0)
    c.setFillColor(RED); c.setFont('Helvetica-Bold',9); c.drawString(x+4*mm,y+h-7*mm,'DATA STATUS: CLOSE VALUE DISAGREES ACROSS SECONDARY REPORTS')
    c.setFillColor(NAVY); c.setFont('Helvetica',8.2)
    c.drawString(x+4*mm,y+h-13*mm,'Official history page found; direct request failed because runner DNS cannot resolve www.niftyindices.com.')
    c.drawString(x+4*mm,y+h-19*mm,'No primary-source bytes/hash. Figures below are provisional secondary reports, NOT accepted evidence.')
    card_y=y-25*mm; card_h=17*mm; gap=3*mm; card_w=(w-gap)/2
    grouped={}
    for row in reports: grouped.setdefault(float(row['value']),[]).append(row['source'])
    for idx,val in enumerate(agreement[:2]):
        sources=grouped[val]; color=TEAL if idx==0 else AMBER
        cx=x+idx*(card_w+gap)
        c.setFillColor(white); c.setStrokeColor(color); c.setLineWidth(1); c.roundRect(cx,card_y,card_w,card_h,2*mm,fill=1,stroke=1)
        c.setFillColor(color); c.setFont('Helvetica-Bold',6.6); c.drawString(cx+3*mm,card_y+card_h-4.2*mm,(' / '.join(sources))[:40])
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',14); c.drawString(cx+3*mm,card_y+5*mm,f'{val:,.2f}')
        c.setFillColor(GRAY); c.setFont('Helvetica',6.5); c.drawRightString(cx+card_w-3*mm,card_y+5.2*mm,'secondary report only')
    chart_top=card_y-8*mm; chart_h=47*mm; chart_y=chart_top-chart_h
    c.setFillColor(PALE); c.roundRect(x,chart_y,w,chart_h,3*mm,fill=1,stroke=0)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',8.2); c.drawString(x+4*mm,chart_top-5*mm,'5-SESSION CLOSES — SECONDARY REPORTS ONLY')
    hist=m['close_history_secondary']; plot_x=x+14*mm; plot_w=w-28*mm; plot_y=chart_y+9*mm; plot_h=28*mm
    vals_hist=[float(r['close']) for r in hist[:-1]]+list(map(float,hist[-1]['close_disputed']))
    lo=min(vals_hist)-40; hi=max(vals_hist)+40
    px=[plot_x+i*(plot_w/4) for i in range(5)]
    def yy(v): return plot_y+(v-lo)/(hi-lo)*plot_h
    c.setStrokeColor(HexColor('#D2E0E4')); c.setLineWidth(.5)
    for v in [25200,25400,25600,25800]:
        if lo<=v<=hi:
            c.line(plot_x,yy(v),plot_x+plot_w,yy(v)); c.setFillColor(GRAY); c.setFont('Helvetica',6.3); c.drawRightString(plot_x-1.5*mm,yy(v)-1.5,f'{v:,}')
    c.setStrokeColor(TEAL); c.setLineWidth(1.7)
    for i in range(3): c.line(px[i],yy(float(hist[i]['close'])),px[i+1],yy(float(hist[i+1]['close'])))
    c.line(px[3],yy(float(hist[3]['close'])),px[4],yy(sum(map(float,hist[4]['close_disputed']))/2))
    for i,row in enumerate(hist[:-1]):
        val=float(row['close']); c.setFillColor(TEAL); c.circle(px[i],yy(val),1.5*mm,fill=1,stroke=0)
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',6.4); c.drawCentredString(px[i],yy(val)+3*mm,f'{val:,.2f}')
    lo_close,hi_close=map(float,hist[-1]['close_disputed'])
    c.setStrokeColor(AMBER); c.setLineWidth(1.3); c.line(px[4],yy(lo_close),px[4],yy(hi_close))
    c.setFillColor(TEAL); c.circle(px[4],yy(lo_close),1.5*mm,fill=1,stroke=0)
    c.setFillColor(AMBER); c.circle(px[4],yy(hi_close),1.5*mm,fill=1,stroke=0)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',6.5); c.drawCentredString(px[4],yy(hi_close)+3*mm,'CLOSE DISPUTED')
    for i,row in enumerate(hist):
        c.setFillColor(GRAY); c.setFont('Helvetica',6.6); c.drawCentredString(px[i],chart_y+4*mm,row['date'])
    analysis_y=chart_y-7*mm; analysis_h=35*mm
    c.setFillColor(PALE); c.roundRect(x,analysis_y-analysis_h,w,analysis_h,3*mm,fill=1,stroke=0)
    c.setFillColor(TEAL); c.setFont('Helvetica-Bold',8.2); c.drawString(x+4*mm,analysis_y-5*mm,'WHAT THE REPORTED SESSION SHAPE SUGGESTS')
    gap_pct=float(o['open_gap_pct']); gap_points=float(o['open_gap_vs_20nov_close_points'])
    distance=min(o['distance_close_above_low_points']),max(o['distance_close_above_low_points'])
    lines=[
      f"23 Nov reportedly opened {gap_points:.2f} points (+{gap_pct:.2f}%) above 20 Nov close, then reversed from {o['high']:,.2f} high to {o['low']:,.2f} low.",
      f"Reported range: {o['range_points']:.2f} points. Disputed closes sit near the low ({distance[0]:.2f}–{distance[1]:.2f} points above it).",
      f"Provisional levels quoted by Univest: support {' / '.join(f'{v:,}' for v in levels['support'])}; resistance {' / '.join(f'{v:,}' for v in levels['resistance'])}.",
      'Interpretation: gap-up failure and near-low finish suggest short-term pressure; confirm only after official OHLC/indicators are checked.'
    ]
    c.setFillColor(NAVY); c.setFont('Helvetica',7.5); ty=analysis_y-10*mm
    for line in lines: c.drawString(x+4*mm,ty,'• '+line); ty-=5.6*mm
    src_y=analysis_y-analysis_h-5*mm
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',7); c.drawString(x,src_y,'SOURCES (SECONDARY / CONFLICT) & OFFICIAL VALIDATION PATH')
    links=[(row['source']+' 23 Nov close report',row['url']) for row in reports]
    links += [('NSE official historical index data',m['primary_source_url']),('Nifty Indices official historical page',m['official_niftyindices_page_url'])]
    for i,(label,url) in enumerate(links):
        c.setFillColor(TEAL); c.setFont('Helvetica',6.5); yy0=src_y-(i+1)*3.7*mm
        c.drawString(x,yy0,f'{i+1}. {label}')
        c.linkURL(url,(x,yy0-1*mm,x+w,yy0+2.5*mm),relative=0,thousands=0)
    c.setStrokeColor(TEAL); c.setLineWidth(.6); c.line(17*mm,15*mm,W-17*mm,15*mm)
    c.setFillColor(TEAL); c.setFont('Helvetica-Bold',6.8); c.drawString(17*mm,10.2*mm,'UNAPPROVED • SECONDARY DATA ONLY • NOT FOR DISTRIBUTION')
    c.setFillColor(GRAY); c.setFont('Helvetica',7); c.drawRightString(W-17*mm,10.2*mm,'MSK AI BOT | 2026-11 | 05/16')
    c.showPage(); c.save()
    return hashlib.sha256(output_path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,default=Path('docs/msk_magazine/research/2026-11/page05_source_reconciliation.json'))
    parser.add_argument('--output',type=Path,default=Path('magazine_artifacts/2026-11/Page05_Nifty_Technical_Outlook_DRAFT.pdf'))
    args=parser.parse_args()
    sha=generate_page05(args.manifest,args.output)
    print(f'Created {args.output} | SHA-256 {sha}')

if __name__=='__main__': main()
