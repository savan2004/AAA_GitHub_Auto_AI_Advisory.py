"use strict";
/* MSK market-first homepage trial. No fabricated live prices, index levels, or news. */
(() => {
  const root=document.querySelector(".research-search");
  if(!root)return;
  const section=document.createElement("section");
  section.id="marketFirst";
  section.className="market-first";
  section.setAttribute("aria-label","Indian stock market snapshot");
  section.innerHTML=`
    <div class="market-first-title"><div><span class="eyebrow">TODAY'S INDIAN MARKET</span><h2>Market overview</h2><p>Prices, breadth and headlines must show a dated source before publication.</p></div><span class="market-first-status" id="marketDataTrust">LIVE QUOTES NOT CONNECTED</span></div>
    <div class="market-index-grid">
      <article><small>NIFTY 50</small><strong>—</strong><span>Live index feed unavailable</span><a href="https://www.nseindia.com/market-data/live-market-indices" target="_blank" rel="noopener noreferrer">Official NSE index ↗</a></article>
      <article><small>BANK NIFTY</small><strong>—</strong><span>Live index feed unavailable</span><a href="https://www.nseindia.com/market-data/live-market-indices" target="_blank" rel="noopener noreferrer">Official NSE index ↗</a></article>
      <article><small>SENSEX</small><strong>—</strong><span>Live index feed unavailable</span><a href="https://www.bseindia.com/indices/IndexArchiveData.html" target="_blank" rel="noopener noreferrer">Official BSE index ↗</a></article>
      <article><small>INDIA VIX</small><strong>—</strong><span>Volatility feed unavailable</span><a href="https://www.nseindia.com/market-data/live-market-indices" target="_blank" rel="noopener noreferrer">Official NSE data ↗</a></article>
    </div>
    <div class="market-two">
      <article class="market-first-panel"><h3>Market Breadth</h3><p id="trialBreadthStatus">Index constituents and live breadth unavailable. Import multiple stock candles for source-labelled sample breadth.</p><div class="market-breadth-grid"><div><strong id="trialAdvances">—</strong><span>Advances</span></div><div><strong id="trialDeclines">—</strong><span>Declines</span></div><div><strong id="trialUnchanged">—</strong><span>Unchanged</span></div></div><button id="trialOpenBreadth" type="button" class="btn ghost">View Breadth Analysis</button></article>
      <article class="market-first-panel"><h3>Market-Moving News</h3><p>Verified headlines require a dated licensed feed. No speculative or stale headlines are displayed.</p><ul><li><a href="https://www.rbi.org.in/" target="_blank" rel="noopener noreferrer">RBI — official announcements ↗</a></li><li><a href="https://www.sebi.gov.in/" target="_blank" rel="noopener noreferrer">SEBI — official updates ↗</a></li><li><a href="https://www.nseindia.com/companies-listing/corporate-filings-announcements" target="_blank" rel="noopener noreferrer">NSE — company announcements ↗</a></li></ul></article>
    </div>
    <div class="market-first-hint"><strong>Next: Company research</strong><span>Search a NSE symbol below; Technical, Fundamentals, Chart and other actions use the selected company.</span></div>`;
  root.parentNode.insertBefore(section,root);
  const $=id=>document.getElementById(id);
  function refreshBreadth(){
    const rows=window.MSKWeb?.features?.results?.()||[];
    const valid=rows.filter(v=>v?.q?.date&&Number.isFinite(v.change));
    if(valid.length<2)return;
    const date=valid.map(v=>v.q.date).sort().at(-1);
    const same=valid.filter(v=>v.q.date===date);
    if(same.length<2)return;
    $("trialAdvances").textContent=same.filter(v=>v.change>0).length;
    $("trialDeclines").textContent=same.filter(v=>v.change<0).length;
    $("trialUnchanged").textContent=same.filter(v=>v.change===0).length;
    const sources=new Set(same.map(v=>v.entry?.source));
    const label=sources.size===1&&sources.has("synthetic")?"SYNTHETIC DEMO": "LOADED SAMPLE — NOT VERIFIED NSE CONSTITUENTS";
    $("trialBreadthStatus").textContent=label+" • "+same.length+" stocks dated "+date+". Not NIFTY or BANK NIFTY breadth.";
  }
  $("trialOpenBreadth").addEventListener("click",()=>{
    if(window.MSKDashboard?.go)window.MSKDashboard.go("breadth");
    else document.querySelector('[data-tab="breadth"]')?.click();
  });
  document.addEventListener("msk:stock-ready",refreshBreadth);
  document.addEventListener("msk:import-done",refreshBreadth);
  refreshBreadth();
})();