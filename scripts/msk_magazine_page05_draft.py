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
    intraday=m['intraday_observation']
    if len(agreement)!=1:
        raise ValueError('Final-close sources disagree; renderer must fail closed until reconciliation')
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
    c.setFillColor(RED); c.setFont('Helvetica-Bold',9); c.drawString(x+4*mm,y+h-7*mm,'DATA STATUS: SECONDARY CLOSE REPORTS AGREE; OFFICIAL BYTES NOT VERIFIED')
    c.setFillColor(NAVY); c.setFont('Helvetica',8.2)
    c.drawString(x+4*mm,y+h-13*mm,'Official data fetch failed: runner DNS cannot resolve www.niftyindices.com (0 bytes).')
    c.drawString(x+4*mm,y+h-19*mm,'Secondary reports only; no source-byte SHA or independent review. Not accepted financial evidence.')
    card_y=y-25*mm; card_h=17*mm; gap=3*mm; card_w=(w-gap)/2
    close_value=agreement[0]; close_sources=len(reports); intraday_value=float(intraday['value'])
    for idx,(label,val,detail,color) in enumerate([
        (f'{close_sources} SECONDARY FINAL-CLOSE REPORTS',close_value,f"{reports[0]['change_points']:+.2f} pts | {reports[0]['change_pct']:+.2f}%",TEAL),
        (f"{intraday['time']} INTRADAY • NOT CLOSE",intraday_value,f"{intraday_value-close_value:+.2f} pts vs final close",AMBER)]):
        cx=x+idx*(card_w+gap)
        c.setFillColor(white); c.setStrokeColor(color); c.setLineWidth(1); c.roundRect(cx,card_y,card_w,card_h,2*mm,fill=1,stroke=1)
        c.setFillColor(color); c.setFont('Helvetica-Bold',6.6); c.drawString(cx+3*mm,card_y+card_h-4.2*mm,label[:40])
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',14); c.drawString(cx+3*mm,card_y+5*mm,f'{val:,.2f}')
        c.setFillColor(GRAY); c.setFont('Helvetica',6.5); c.drawRightString(cx+card_w-3*mm,card_y+5.2*mm,detail)
    chart_top=card_y-8*mm; chart_h=47*mm; chart_y=chart_top-chart_h
    c.setFillColor(PALE); c.roundRect(x,chart_y,w,chart_h,3*mm,fill=1,stroke=0)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',8.2); c.drawString(x+4*mm,chart_top-5*mm,'5-SESSION CLOSES — SECONDARY ONLY • ORANGE = 15:15 INTRADAY')
    hist=m['close_history_secondary']; plot_x=x+14*mm; plot_w=w-28*mm; plot_y=chart_y+9*mm; plot_h=28*mm
    vals_hist=[float(r['close']) for r in hist]+[intraday_value]
    lo=min(vals_hist)-40; hi=max(vals_hist)+40
    px=[plot_x+i*(plot_w/4) for i in range(5)]
    def yy(v): return plot_y+(v-lo)/(hi-lo)*plot_h
    c.setStrokeColor(HexColor('#D2E0E4')); c.setLineWidth(.5)
    for v in [25200,25400,25600,25800]:
        if lo<=v<=hi:
            c.line(plot_x,yy(v),plot_x+plot_w,yy(v)); c.setFillColor(GRAY); c.setFont('Helvetica',6.3); c.drawRightString(plot_x-1.5*mm,yy(v)-1.5,f'{v:,}')
    c.setStrokeColor(TEAL); c.setLineWidth(1.7)
    for i in range(3): c.line(px[i],yy(float(hist[i]['close'])),px[i+1],yy(float(hist[i+1]['close'])))
    c.line(px[3],yy(float(hist[3]['close'])),px[4],yy(float(hist[4]['close'])))
    for i,row in enumerate(hist):
        val=float(row['close']); c.setFillColor(TEAL); c.circle(px[i],yy(val),1.5*mm,fill=1,stroke=0)
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',6.4); c.drawCentredString(px[i],yy(val)+3*mm,f'{val:,.2f}')
    c.setFillColor(AMBER); c.circle(px[4],yy(intraday_value),1.5*mm,fill=1,stroke=0)
    # The orange marker is the 15:15 intraday quote; its label is in the card above.
    for i,row in enumerate(hist):
        c.setFillColor(GRAY); c.setFont('Helvetica',6.6); c.drawCentredString(px[i],chart_y+4*mm,row['date'])
    analysis_y=chart_y-7*mm; analysis_h=35*mm
    c.setFillColor(PALE); c.roundRect(x,analysis_y-analysis_h,w,analysis_h,3*mm,fill=1,stroke=0)
    c.setFillColor(TEAL); c.setFont('Helvetica-Bold',8.2); c.drawString(x+4*mm,analysis_y-5*mm,'WHAT THE REPORTED SESSION SHAPE SUGGESTS')
    gap_pct=float(o['open_gap_pct']); gap_points=float(o['open_gap_vs_20nov_close_points'])
    distance=min(o['distance_close_above_low_points']),max(o['distance_close_above_low_points'])
    lines=[
      f"23 Nov reportedly opened {gap_points:.2f} points (+{gap_pct:.2f}%) above 20 Nov close; high {o['high']:,.2f}, low {o['low']:,.2f}.",
      f"Final close {close_value:,.2f}, {distance[0]:.2f} points above the reported low. Secondary sources only.",
      f"15:15 intraday update {intraday_value:,.2f} ({intraday_value-close_value:.2f} points above final close); not a close quote.",
      f"Univest provisional levels: support {' / '.join(f'{v:,}' for v in levels['support'])}; resistance {' / '.join(f'{v:,}' for v in levels['resistance'])}."
    ]
    c.setFillColor(NAVY); c.setFont('Helvetica',7.5); ty=analysis_y-10*mm
    for line in lines: c.drawString(x+4*mm,ty,'• '+line); ty-=5.6*mm
    src_y=analysis_y-analysis_h-5*mm
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',7); c.drawString(x,src_y,'SOURCES (SECONDARY) & OFFICIAL VALIDATION PATH')
    links=[(row['source']+' 23 Nov final-close report',row['url']) for row in reports]
    links += [(intraday['source']+' 15:15 intraday (not close)',intraday['url']),('NSE official historical index data',m['primary_source_url']),('Nifty Indices official historical page',m['official_niftyindices_page_url'])]
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
