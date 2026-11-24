"use strict";
/* Index navigation is explicitly separate from stock screening and financial ratios.
   Do not manufacture index OHLC, volume, constituent breadth or trading signals. */
(() => {
  const ticker=document.getElementById("ticker");
  if(!ticker)return;
  const indexLinks={
    NIFTY:{label:"NIFTY 50",official:"https://www.nseindia.com/market-data/live-market-indices"},
    BANKNIFTY:{label:"NIFTY BANK",official:"https://www.nseindia.com/market-data/live-market-indices"}
  };
  const normalize=x=>{
    const s=String(x||"").toUpperCase().replace(/[^A-Z0-9]/g,"");
    if(s==="NIFTY"||s==="NIFTY50")return "NIFTY";
    if(s==="BANKNIFTY"||s==="NIFTYBANK")return "BANKNIFTY";
    return "";
  };
  const wrap=document.createElement("div");
  wrap.id="mskIndexChooser";
  wrap.setAttribute("aria-label","Index research shortcuts");
  wrap.style.cssText="display:flex;flex-wrap:wrap;gap:8px;margin:8px 0";
  const panel=document.createElement("section");
  panel.id="mskIndexResearch";
  panel.hidden=true;
  panel.style.cssText="background:#f1f7fb;border:1px solid #bad3e1;border-radius:12px;padding:16px;margin:12px 0;max-width:980px";
  panel.setAttribute("aria-live","polite");
  const target=ticker.closest(".ticker-input")||ticker.parentElement;
  target.after(wrap,panel);
  function openIndex(key){
    const record=indexLinks[key];if(!record)return false;
    ticker.value=key;
    panel.replaceChildren();
    const heading=document.createElement("h3");heading.textContent=record.label+" — index research";
    const info=document.createElement("p");
    info.textContent="Index recognised. MSK does not currently have a licensed index OHLCV feed, live index quotation, index-level technical signals or complete constituent screening. Do not interpret individual equity prices or screening results as index data.";
    const source=document.createElement("a");
    source.href=record.official;source.target="_blank";source.rel="noopener noreferrer";
    source.textContent="View official NSE index market data";
    const note=document.createElement("p");
    note.textContent="Stock Screener covers only the actual stock symbols loaded. NIFTY 50 and NIFTY BANK constituent scans require a dated, verified member universe; index names alone cannot generate breadth.";
    panel.append(heading,info,source,note);
    panel.hidden=false;
    panel.scrollIntoView?.({behavior:"smooth",block:"nearest"});
    return true;
  }
  for(const [key,record] of Object.entries(indexLinks)){
    const b=document.createElement("button");b.type="button";b.className="btn ghost";
    b.textContent=record.label;b.dataset.index=key;
    b.addEventListener("click",()=>openIndex(key));
    wrap.append(b);
  }
  // Prevent stock-search handlers from presenting a generic equity response
  // for known index aliases. Non-index stock searches remain unchanged.
  ticker.addEventListener("keydown",event=>{
    if(event.key==="Enter"){
      const key=normalize(ticker.value);
      if(key){event.preventDefault();event.stopImmediatePropagation();openIndex(key);}
    }
  },true);
  document.getElementById("analyzeSymbol")?.addEventListener("click",event=>{
    const key=normalize(ticker.value);
    if(key){event.preventDefault();event.stopImmediatePropagation();openIndex(key);}
  },true);
  window.MSKIndexSearch={normalize,openIndex};
})();
