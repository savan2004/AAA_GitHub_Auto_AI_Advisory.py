"""Visual-only enhancement for a verified MSK November 2026 issue.

Independent original editorial artwork and compact source-backed diagrams.
All page text, financial facts, dates and source links must already have
passed the magazine's existing primary-source verification. No network,
Telegram, payment integration or publication approval is added here.
"""
from __future__ import annotations
from bs4 import BeautifulSoup
import html as html_lib


def art_direct_november(html_text: str, logo_src: str) -> str:
    """Return a print-styled version of the original 16-page, source-verified HTML.

    This module is deliberately edition-scoped: figures and captions here are
    ONLY October 2026 figures included in the Nov 2026 verified research pack.
    A later issue MUST NOT re-use it unless the dated data is rebuilt.
    """
    soup=BeautifulSoup(html_text,'html.parser')
    pages=soup.select('section.page')
    if len(pages)!=16 or 'NIFTY RISES. SMALL CAPS FALL.' not in pages[0].get_text(' ',strip=True):
        raise ValueError('This art direction applies only to verified MSK November 2026 (16 pages)')
    for im in soup.select('section.page img'):
        im['src']=logo_src

    # SVG is original art. It depicts a stylised market split rather than a price
    # chart; no axes, levels or trading signals are invented.
    cover_motif='''<svg class="hero-artwork" viewBox="0 0 950 825" xmlns="http://www.w3.org/2000/svg" aria-label="Original geometric illustration, not a price chart">
    <defs>
     <linearGradient id="goldline" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f3d582"/><stop offset="1" stop-color="#db7f35"/></linearGradient>
     <linearGradient id="jade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#26e0b1"/><stop offset="1" stop-color="#069f96"/></linearGradient>
     <linearGradient id="maroon" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#e89675"/><stop offset="1" stop-color="#aa4f63"/></linearGradient>
     <pattern id="grid" width="38" height="38" patternUnits="userSpaceOnUse"><path d="M38 0H0V38" fill="none" stroke="#5d8d8c" stroke-width="1" opacity=".24"/></pattern>
     <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="14"/></filter>
    </defs>
    <circle cx="685" cy="356" r="316" fill="none" stroke="#3e7e77" stroke-width="1.4" opacity=".5"/>
    <circle cx="685" cy="356" r="262" fill="none" stroke="#598982" stroke-width="1" opacity=".4"/>
    <circle cx="685" cy="356" r="208" fill="none" stroke="#467c73" stroke-width="1" opacity=".34"/>
    <rect x="290" y="66" width="635" height="630" fill="url(#grid)" opacity=".6"/>
    <!-- Architectural polygon collage / original abstract visual, not a financial plot -->
    <path d="M290 655 L440 570 L495 496 L565 526 L630 389 L705 414 L778 206 L915 70" stroke="url(#goldline)" stroke-width="20" fill="none" stroke-linejoin="round" opacity=".22" filter="url(#glow)"/>
    <path d="M290 655 L440 570 L495 496 L565 526 L630 389 L705 414 L778 206 L915 70" stroke="url(#goldline)" stroke-width="9" fill="none" stroke-linejoin="round" stroke-linecap="square"/>
    <path d="M392 245 L492 312 L585 281 L640 395 L720 420 L810 572 L914 647" stroke="url(#maroon)" stroke-width="13" fill="none" stroke-linejoin="round" stroke-linecap="square" opacity=".86"/>
    <path d="M816 202 L915 70 L876 83" stroke="url(#goldline)" stroke-width="9" fill="none"/>
    <path d="M881 651 L914 647 L912 614" stroke="url(#maroon)" stroke-width="9" fill="none"/>
    <g opacity=".25" fill="#d3eae0">
    <rect x="280" y="680" width="44" height="94"/><rect x="339" y="651" width="56" height="123"/><rect x="411" y="692" width="44" height="82"/>
    <rect x="469" y="669" width="43" height="105"/><rect x="528" y="633" width="65" height="141"/>
    <rect x="609" y="691" width="53" height="83"/><rect x="678" y="662" width="49" height="112"/>
    <rect x="743" y="624" width="59" height="150"/><rect x="818" y="679" width="36" height="95"/>
    </g>
    </svg>'''

    cover=pages[0]
    cover['class']=['page','mag-cover']
    source_url=cover.select_one('.cover-source a')['href']
    cover.clear()
    fragment=f'''
    <div class="cover-head"><div class="cover-logo"><img src="{logo_src}" alt="MSK AI BOT — Analysis | AI Intelligence | Market Insights"></div>
     <div class="cover-date">THE INDIAN MARKET REVIEW<br><strong>NOVEMBER 2026  /  ISSUE 11</strong></div></div>
    <div class="cover-bleed"></div>
    <div class="cover-visual">{cover_motif}</div>
    <div class="cover-copy">
      <div class="cover-label"><span>THE BIG MARKET STORY</span><span>OCTOBER DATA / NOVEMBER ISSUE</span></div>
      <div class="cover-kicker">THE MARKET<br>DIVIDE<span class="dash">.</span></div>
      <h1><span>NIFTY RISES.</span><span>SMALL CAPS FALL.</span></h1>
      <p class="cover-deck">Nifty 50 finished October higher while smaller companies fell. Here's what the official numbers mean for Indian investors.</p>
    </div>
    <div class="cover-number-panel"><div class="cover-number gain"><small>NIFTY 50 · OCT 2026 TRI</small><strong>+1.95%</strong><span>A stronger headline index</span></div>
    <div class="cover-number loss"><small>NIFTY SMALLCAP 250 · OCT 2026 TRI</small><strong>−5.78%</strong><span>A very different broader-market story</span></div></div>
    <div class="cover-content-strip"><div><b>01 / MARKETS</b><span>Index divergence &amp; RBI</span></div><div><b>02 / SECTORS</b><span>IT shines; metal lags</span></div><div><b>03 / COMPANIES</b><span>Three results, two currencies</span></div></div>
    <div class="cover-footer"><span>ORIGINAL DATA: <a href="{html_lib.escape(source_url,quote=True)}">NSE INDICES / OCTOBER 2026 DASHBOARD</a></span><span>PRIVATE EDITORIAL REVIEW / NOT FOR DISTRIBUTION</span></div>
    <div class="publication"><span class="locked">UNAPPROVED EDITORIAL DRAFT — PRIVATE REVIEW ONLY</span><strong>01 / 16</strong></div>
    '''
    cover.append(BeautifulSoup(fragment,'html.parser'))

    # Editorial header system: legibility and premium hierarchy, no copied layouts.
    for n,page in enumerate(pages[1:],start=2):
        page['class']=list(page.get('class',[]))+['editorial-v4',f'edition-{n:02d}']
        top=page.select_one('.masthead')
        if top:
            mast=BeautifulSoup(f'<div class="spread-spine"><span>MSK / INSIGHT LETTER</span><strong>{n:02d}</strong></div>','html.parser')
            top.insert_after(mast)
        source=page.select_one('.full-source')
        if source:
            source.insert_before(BeautifulSoup('<div class="page-accent-mark" aria-hidden="true">◆&nbsp;SOURCE-LED RESEARCH &nbsp; / &nbsp; MARKET REVIEW</div>','html.parser'))

    # Cover visuals across multiple sections; all are typographic and/or only
    # use exact verified values already included in the original November proof.
    def visual(html, n, top, height, kind='mint'):
        page=pages[n-1]
        panel=BeautifulSoup(f'<div class="visual-story {kind}" style="top:{top}mm;height:{height}mm">{html}</div>','html.parser')
        page.append(panel)

    # An oversized sector/type label motif distinguishes every editorial page.
    chapter_tags={3:'THE COVER STORY',4:'MACRO & RBI',5:'NIFTY 50',6:'BANK NIFTY',7:'MARKET BREADTH',8:'SECTOR ROTATION',9:'LARGE CAPS',10:'MID CAPS',11:'SMALL CAPS',12:'COMPANY RESEARCH',13:'STRATEGY LAB',14:'WHAT COMES NEXT',15:'INVESTOR PLAYBOOK',16:'OUR RESEARCH METHOD'}
    for n,title in chapter_tags.items():
        page=pages[n-1]
        page.insert(0,BeautifulSoup(f'<div class="vertical-folio" aria-hidden="true">{html_lib.escape(title)}</div>','html.parser'))

    # Compact original charts, not copied from NSE or from online graphic packs.
    # Conservative ranges chosen to reflect source values; axes are explicitly named.
    visual('''<div class="visual-stamp"><span>THE REAL MESSAGE</span><strong>NOT ALL STOCKS<br>MOVE WITH NIFTY.</strong><small>October 2026 NSE index total returns · Keep periods matched.</small></div>''',3,224,14,'poster')
    visual('''<div class="visual-mini-header">FOUR DIFFERENT NUMBERS / TWO DIFFERENT MONTHS</div>
    <div class="mini-4"><div><b>RBI REPO</b><strong>5.50%</strong><small>7 OCT</small></div><div><b>CPI</b><strong>6.09%</strong><small>OCT</small></div><div><b>FOOD CPI</b><strong>8.94%</strong><small>OCT</small></div><div><b>IIP</b><strong>6.7%</strong><small>SEP</small></div></div>
    <p class="mini-foot">Different indicators and months. Do not assume these numbers predict share prices.</p>''',4,203,35)
    # Page 5 period returns already visible; no overlay near the risk-note region.
    visual('''<div class="visual-mini-header">SAME MONTH, DIFFERENT INDEX RESULTS</div>
    <div class="bar-grid"><span>Bank Nifty</span><div class="track"><i style="width:5.1%"></i></div><b>+0.10%</b></div>
    <div class="bar-grid"><span>Nifty 50</span><div class="track"><i style="width:100%"></i></div><b>+1.95%</b></div>
    <div class="visual-mini-foot">Common 0% to 1.95% scale · October 2026 NSE TRI</div>''',6,202,37)
    visual('''<div class="visual-mini-header">HOW INDEX WEIGHTS CHANGE THE PICTURE</div>
    <div class="difference"><div class="diff-big">4.37 <small>pp</small></div><p>The equal-weight Nifty 500 fell more than the market-cap-weighted Nifty 500. This is not a verified advance/decline count.</p></div>''',7,192,42,'cream')
    visual('''<div class="visual-mini-header">SECTOR RESULTS ARE NOT STOCK TIPS</div>
    <div class="sector-chips"><span class="strong">IT <b>+18.06%</b></span><span class="muted">AUTO <b>−3.31%</b></span><span class="muted">METAL <b>−7.51%</b></span></div>''',8,211,28,'cream')
    visual('''<div class="feature-note"><span class="note-icon">01</span><div><b>COMPANY SPOTLIGHT</b><strong>TCS quarterly sales: Rs 73,188 crore</strong><p>Reported consolidated revenue in its original NSE filing for Q2 FY27. This does not by itself determine whether the share is attractively valued.</p></div></div>''',9,188,43,'cream')
    visual('''<div class="feature-note"><span class="note-icon">20%</span><div><b>GROWTH IS NOT THE SAME AS RETURN</b><strong>Persistent Systems · Q2 FY27</strong><p>US-dollar revenue grew 20.0% year-on-year. Reported revenue was $487.6 million, and EBIT operating margin was 15.0%.</p></div></div>''',10,190,43,'cream')
    visual('''<div class="visual-mini-header">SMALL-CAP INVESTOR CHECK / THREE NON-NEGOTIABLE QUESTIONS</div>
    <div class="question-bar"><span>01 <b>Can you sell easily?</b></span><span>02 <b>Is debt manageable?</b></span><span>03 <b>Is cash flow real?</b></span></div>''',11,211,24,'cream')
    visual('''<div class="visual-mini-header">CURRENCY MATTERS / NEVER RANK INR AND USD DIRECTLY</div>
    <div class="currency-ribbon"><div><small>TCS / INR</small><b>Rs 73,188 crore</b></div><div><small>PERSISTENT / USD</small><b>$487.6m</b></div><div><small>COFORGE / USD</small><b>$652.2m</b></div></div>''',12,205,32,'cream')
    visual('''<div class="visual-mini-header">A SCENARIO IS NOT A PRICE TARGET</div>
    <div class="dual-path"><div class="path-red"><b>HIGHER INFLATION</b><span>Watch borrowing costs and debt.</span></div><div class="path-green"><b>EASING INFLATION</b><span>Still check earnings and valuations.</span></div></div>''',13,189,44,'cream')
    visual('''<div class="visual-mini-header">WATCH THE RELEASE / VERIFY THE ORIGINAL FIGURES</div>
    <div class="date-pills"><span><b>30 NOV</b>GDP + IIP</span><span><b>2–4 DEC</b>RBI MPC</span><span><b>14 DEC</b>CPI</span></div>''',14,188,45)
    visual('''<div class="visual-mini-header">INVESTOR RULE / SOURCE FIRST, DECISION LATER</div>
    <div class="playbook-rail"><span>COMPANY RESULTS</span><em>→</em><span>VALUATION</span><em>→</em><span>RISKS</span><em>→</em><span>YOUR DECISION</span></div>''',15,214,26)
    # Don't clutter page 16: the sources and original disclaimer are the final visual.

    CSS=r''' 
    @page{size: A4;margin:0}
    @font-face{font-family:'MSK Sans';src:local('Liberation Sans')}
    @font-face{font-family:'MSK Serif';src:local('Liberation Serif')}
    .mag-cover{background:#071e27;color:#fff;overflow:hidden;isolation:isolate}
    .mag-cover .cover-head{position:absolute;top:0;left:0;right:0;height:31mm;background:#f8f8f0;z-index:5;display:flex;align-items:center;justify-content:space-between;padding:4mm 12mm 4mm 14mm;border-bottom:3mm solid #08a48f}
    .mag-cover .cover-logo img{height:22mm;width:70mm;object-fit:contain;object-position:left center}
    .mag-cover .cover-date{text-align:right;font:600 8pt/1.65 'Liberation Sans',Arial,sans-serif;letter-spacing:.11em;color:#35545a}
    .mag-cover .cover-date strong{font-weight:bold;color:#0c6f67}
    .mag-cover .cover-bleed{position:absolute;inset:31mm 0 0 0;background:linear-gradient(130deg,#061d25 0%,#0b383c 55%,#031b23 100%)}
    .mag-cover .cover-visual{position:absolute;top:40mm;left:0;right:0;height:211mm;opacity:.86}
    .mag-cover .hero-artwork{width:100%;height:100%;display:block}
    .mag-cover .cover-copy{position:absolute;left:16mm;right:10mm;top:43mm;z-index:3}
    .mag-cover .cover-label{display:flex;justify-content:space-between;max-width:183mm;color:#d6c08c;border-bottom:1px solid #718879;padding-bottom:3mm;font:bold 8pt 'Liberation Sans',sans-serif;letter-spacing:.1em}
    .mag-cover .cover-kicker{font:900 39pt/.94 'Liberation Sans',Arial,sans-serif;letter-spacing:-.055em;margin:6mm 0 6mm;max-width:118mm;color:#f1e9d5}
    .mag-cover .cover-kicker .dash{color:#f6b957}
    .mag-cover h1{font:900 20.5pt/1.25 'Liberation Sans',Arial,sans-serif;margin:3mm 0 5mm;max-width:137mm;letter-spacing:.025em}
    .mag-cover h1 span{display:block;padding:1.3mm 2.5mm;margin:1.5mm 0;background:rgba(8,32,40,.86);width:max-content;max-width:100%;border-left:3px solid #09b292}
    .mag-cover h1 span+span{border-left-color:#e3956b;color:#ffbc94}
    .mag-cover .cover-deck{font:11pt/1.48 'Liberation Sans',sans-serif;color:#e0efe4;max-width:115mm;margin:5mm 0;background:rgba(2,23,29,.77);padding:4mm 4.5mm}
    .mag-cover .cover-number-panel{position:absolute;left:16mm;right:16mm;bottom:61mm;z-index:4;display:flex;gap:4mm}
    .mag-cover .cover-number{width:50%;padding:4mm 5mm 5mm;min-height:37mm;background:rgba(7,29,37,.95);border-top:2.3mm solid #10ae9b}
    .mag-cover .cover-number.loss{border-top-color:#e88574}
    .mag-cover .cover-number small{display:block;font-size:7.7pt;color:#d5ddd3;font-weight:bold;letter-spacing:.08em}
    .mag-cover .cover-number strong{display:block;font:900 29pt 'Liberation Sans',Arial,sans-serif;color:#a7f1c6;margin:2mm 0 .5mm;letter-spacing:-.02em}
    .mag-cover .cover-number.loss strong{color:#f2a49a}
    .mag-cover .cover-number span{font-size:8pt;color:#d5e1da;display:block}
    .mag-cover .cover-content-strip{position:absolute;left:16mm;right:16mm;bottom:22mm;display:flex;border-top:1px solid #82b5ae;padding-top:5mm;gap:3mm;z-index:5}
    .mag-cover .cover-content-strip>div{width:33%;padding-right:2mm}
    .mag-cover .cover-content-strip b{display:block;color:#d9b57b;font-size:9pt;letter-spacing:.05em}
    .mag-cover .cover-content-strip span{display:block;font-size:7.7pt;color:#daeae1;line-height:1.3;margin-top:2mm}
    .mag-cover .cover-footer{position:absolute;left:16mm;right:16mm;bottom:13.3mm;display:flex;justify-content:space-between;font-size:6.7pt;color:#b0ccc5}
    .mag-cover .cover-footer a{color:#c5dfd8;text-decoration:underline}
    .mag-cover .publication{color:#c8e5d8;border-color:#416f66}
    .mag-cover .publication strong{color:#f5ba72}
    .editorial-v4{background:#fcfbf6;color:#163543}
    .editorial-v4 .masthead{border-bottom:2px solid #0ca18e;height:27mm}
    .editorial-v4 .masthead img{height:17mm;max-width:78mm;width:70mm;object-fit:contain}
    .editorial-v4 .spread-spine{position:absolute;top:10.5mm;right:17mm;color:#61817f;display:flex;align-items:center;gap:3mm;font-size:7.2pt;letter-spacing:.05em;opacity:0}
    .editorial-v4 .vertical-folio{position:absolute;right:4.6mm;top:77mm;writing-mode:vertical-rl;text-orientation:mixed;font-size:7pt;letter-spacing:.18em;color:#73908c;opacity:.82}
    .editorial-v4 .chapter-ribbon{border-left:4.5mm solid #d4b06c;background:#0a303b;padding:7mm 8mm}
    .editorial-v4 .chapter-ribbon small{color:#e8b96a}
    .editorial-v4 .chapter-ribbon h1{font-size:26.5pt;letter-spacing:-.03em}
    .editorial-v4 .chapter-ribbon p{font-size:9.8pt}
    .editorial-v4 .feature-title{font:bold 28.5pt/1.05 'Liberation Serif',Georgia,serif;color:#0e3440;letter-spacing:-.03em}
    .editorial-v4 .feature-deck{font-size:10.7pt;color:#455e62}
    .editorial-v4 .number-wall .number{border-top:3px solid #0baf96}
    .editorial-v4 .number-wall .number.dark{border-top-color:#dfb16a}
    .editorial-v4 .chapter-ribbon, .editorial-v4 .huge-inset,.editorial-v4 .split-visual {background:#092b39}
    .editorial-v4 .number-wall .number h2,.editorial-v4 .huge-inset strong,.editorial-v4 .hero-pair .fig{letter-spacing:-.03em}
    .editorial-v4 .publication{border-top:1px solid #95b9ae}
    .editorial-v4 .publication .locked{font-weight:bold;color:#9b5652}
    .editorial-v4 .full-source{border-top:1px solid #8eaaa0}
    .editorial-v4 .page-accent-mark{position:absolute;bottom:38mm;left:17mm;font-size:6.6pt;letter-spacing:.1em;color:#4d7972;opacity:.8}
    .editorial-v4 .bare-risk{z-index:5;border-left:2px solid #a8554d;border-top:1px solid #c6a597;background:#faf1e8}
    .visual-story{position:absolute;left:17mm;right:17mm;z-index:2;border-radius:0;box-sizing:border-box;overflow:hidden;background:#0c3740;color:#f7f8ed;padding:3.5mm 5mm}
    .visual-story.cream{background:#eaf1ec;color:#143741;border-left:1.5mm solid #d8ac6c}
    .visual-story.slim{padding:2mm 5mm;background:#e9f2ec;color:#19444a}
    .visual-story.poster{background:#13363c;border-left:2mm solid #d4b26d;padding:1mm 4mm}
    .visual-mini-header{font-weight:bold;font-size:8.1pt;letter-spacing:.09em;color:#e8c486;margin-bottom:2.7mm}
    .visual-story.cream .visual-mini-header,.visual-story.slim .visual-mini-header{color:#12685f}
    .visual-story:not(.cream) .feature-note b,.visual-story:not(.cream) .feature-note strong{color:#e9f4ed}
    .visual-story:not(.cream) .feature-note p{color:#e5eee9}
    .mini-4{display:flex;gap:2.5mm;margin:0;align-items:stretch}
    .mini-4>div{width:25%;border-top:2px solid #c9ad7a;padding:2mm 2.7mm 1mm;background:#e8f0e9;color:#14383c}
    .mini-4 b{display:block;font-size:7pt;color:#487974}
    .mini-4 strong{display:block;font:bold 17pt 'Liberation Sans',sans-serif;color:#11776a;margin:1mm 0}
    .mini-4 small{display:block;font-size:7pt;color:#61746c}
    .mini-foot{font-size:8.2pt;color:#d0e6dd;line-height:1.2;margin:2mm 0 0}
    .mini-inline{display:flex;justify-content:space-between;gap:3mm}
    .mini-inline span{font-size:9pt;font-weight:bold}
    .mini-inline b{margin-left:2mm;font-size:12pt}
    .bar-grid{display:flex;align-items:center;gap:3mm;height:8mm;font-size:8pt}
    .bar-grid>span{width:26mm}
    .bar-grid .track{width:105mm;background:#e0eee7;height:4mm}
    .bar-grid .track i{display:block;height:100%;background:#08aa98}
    .bar-grid>b{font-size:11pt;color:#eec380}
    .visual-mini-foot{font-size:7pt;color:#bcd7ce;margin-top:2mm}
    .difference{display:flex;align-items:center;gap:8mm}
    .diff-big{font:900 31pt 'Liberation Sans',sans-serif;color:#b88e4f;white-space:nowrap}
    .diff-big small{font-size:11pt}
    .difference p{font:9.2pt/1.4 'Liberation Sans',sans-serif;max-width:116mm}
    .sector-chips{display:flex;gap:4mm}
    .sector-chips span{flex:1;background:#fff;padding:3mm 4mm;font-size:9pt;color:#39635f}
    .sector-chips b{display:block;font-size:17pt;color:#128778;margin-top:1mm}
    .sector-chips .muted b{color:#bc5853}
    .feature-note{display:flex;align-items:center;gap:6mm;height:100%}
    .feature-note .note-icon{font:bold 30pt 'Liberation Serif',Georgia,serif;color:#b79052;border-right:1px solid #b8cfc5;min-width:39mm}
    .feature-note div{flex:1}
    .feature-note b{display:block;font-size:8pt;letter-spacing:.08em;color:#147569}
    .feature-note strong{display:block;font: bold 16pt/1.2 'Liberation Serif',Georgia,serif;margin:2mm 0;color:#12383f}
    .feature-note p{font-size:9pt;line-height:1.35;margin:0;color:#435f5f}
    .question-bar{display:flex;gap:3mm}
    .question-bar span{width:33%;font-size:9pt;line-height:1.4;padding:2mm;border-top:1px solid #a5bfb6}
    .question-bar b{color:#0c6a61}
    .currency-ribbon{display:flex;gap:3mm}
    .currency-ribbon>div{flex:1;background:#fff;border-top:2px solid #0c9a8d;padding:2.5mm 2mm}
    .currency-ribbon small{display:block;font-size:7.2pt;color:#65827c}
    .currency-ribbon b{display:block;font-size:11pt;margin-top:2mm;color:#18474a}
    .dual-path{display:flex;gap:4mm}
    .dual-path>div{width:50%;padding:4mm;background:#f3e2db;color:#753d3d}
    .dual-path .path-green{background:#dcefe2;color:#166655}
    .dual-path b{display:block;font-size:11pt}
    .dual-path span{font-size:9pt;display:block;margin-top:4mm}
    .date-pills{display:flex;gap:4mm;margin-top:5mm}
    .date-pills span{flex:1;padding:6mm 3mm;background:#dae9e3;color:#1b4948;text-align:center;font-size:10pt}
    .date-pills b{display:block;font-size:15pt;color:#026b64}
    .playbook-rail{display:flex;align-items:center;justify-content:space-between;gap:2mm}
    .playbook-rail span{font-size:9pt;font-weight:bold;color:#0f5953}
    .playbook-rail em{font-style:normal;color:#efbd6c;font-size:13pt}
    .visual-story:not(.cream) .playbook-rail span{color:#f3ede0}
    .visual-stamp{display:flex;align-items:center;justify-content:space-between;gap:4mm;height:100%}
    .visual-stamp span{font-size:7pt;color:#d5c399;font-weight:bold;max-width:25mm}
    .visual-stamp strong{font-size:10pt;letter-spacing:.04em;color:#fff;line-height:1.2}
    .visual-stamp small{font-size:7pt;color:#d4e2dc;max-width:53mm}
    /* Accent for issue sections, the entire issue has an unmistakably coherent identity. */
    .edition-03 .chapter-ribbon,.edition-08 .chapter-ribbon,.edition-12 .chapter-ribbon,.edition-16 .chapter-ribbon{background:#08313b}
    .edition-08 .chapter-ribbon{border-left-color:#18b79c}
    .edition-12 .chapter-ribbon{border-left-color:#eb9974}
    .edition-16 .chapter-ribbon{border-left-color:#bba071}
    .edition-02 .contents-splash h1{font-size:39pt;letter-spacing:-.035em}
    .edition-02 .contents-grid{margin-top:10mm}
    '''
    style=soup.new_tag('style')
    style.string=CSS
    soup.head.append(style)

    return str(soup)
