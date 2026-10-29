"use strict";
/* Source-aware report: eight intentionally separate print pages, no estimates. */
(function(){
 const e=(tag,klass,text)=>{const n=document.createElement(tag);if(klass)n.className=klass;if(text!==undefined)n.textContent=String(text);return n;};
 const val=(x,d=2)=>Number.isFinite(x)?Number(x).toLocaleString("en-IN",{maximumFractionDigits:d,minimumFractionDigits:d}):"Unavailable";
 function render(host){
   host.replaceChildren();
   const engine=window.MSKWeb,features=engine?.features;
   const evidence=features?.getResearch?.()||null;
   const selected=(document.getElementById("ticker")?.value||"").trim().toUpperCase();
   const symbol=evidence?.symbol||selected||"Not selected";
   const series=evidence?features.library.get(evidence.symbol)?.bars||[]:[];
   const financial=evidence?.fundamental||features?.statements?.get(symbol)||null;
   const dates=evidence?.session||"Unavailable";
   const type=evidence?.kind==="tej_eod"?"TejHQ / NSE historical EOD (unadjusted)":evidence?.kind==="uploaded"?"User-uploaded CSV (unverified)":evidence?.kind==="synthetic"?"SYNTHETIC TEST DATA": "No historical market source";
   const root=e("article","msk-report-document");root.id="mskReportDocument";root.setAttribute("aria-label","Eight-page source-aware research report");
   const row=(table,label,value)=>{const tr=e("tr");tr.append(e("th","",label),e("td","",value));table.append(tr);};
   const table=(page,values)=>{const t=e("table","msk-report-table");for(const [k,v] of values)row(t,k,v);page.append(t);};
   const para=(page,txt)=>page.append(e("p","",txt));
   function page(index,title,subtitle){
     const s=e("section","msk-report-page");s.dataset.page=String(index);
     s.append(e("div","msk-report-kicker","MSK WEB ADVISORY • SOURCE-AWARE RESEARCH"));
     s.append(e("h2","",index+". "+title));
     s.append(e("p","msk-report-subtitle",subtitle));
     s.append(e("div","msk-report-subject",symbol+"  |  "+dates+"  |  "+type));
     root.append(s);return s;
   }
   const absent="Unavailable — dated, credible company financial statements not loaded.";
   let p=page(1,"Company overview","Identity and limits of the evidence");
   table(p,[["Selected NSE symbol",symbol],["Company legal name",absent],["Business model",absent],["Sector and industry",absent],["Market data session",dates],["Price feed",type]]);
   para(p,"A ticker and historical prices do not establish issuer identity, corporate actions or business fundamentals. This report does not give trading advice.");
   p=page(2,"Financial performance","Source-dated annual and quarterly company results");
   table(p,[["Revenue (latest period)",absent],["Net profit (latest period)",absent],["Operating margin",absent],["Operating cash flow",absent],["Financial reporting date",financial?.asof||"Unavailable"],["Financial statement source",financial?.source||"Unavailable"]]);
   para(p,"Do not infer financial statements or growth rates from OHLCV candles. Upload dated filings before treating any company-level figure as evidence.");
   p=page(3,"Fundamental ratios","Uploaded statements only; not independently audited");
   table(p,[["P/E",financial?val(financial.pe):absent],["ROE",financial&&Number.isFinite(financial.roe_percent)?val(financial.roe_percent)+"%":absent],["Revenue growth",financial&&Number.isFinite(financial.revenue_growth_percent)?val(financial.revenue_growth_percent)+"%":absent],["Market capitalisation (₹)",financial?val(financial.market_cap_inr,0):absent],["Debt / equity",financial&&Number.isFinite(financial.debt_equity_ratio)?val(financial.debt_equity_ratio)+"x":absent],["Source status",financial?"USER-SUPPLIED, NOT INDEPENDENTLY VERIFIED":"NO COMPANY FILINGS"]]);
   p=page(4,"Technical analysis","Completed historical sessions only");
   table(p,[["Historical close (₹)",evidence?val(evidence.close):"Unavailable"],["Trend",evidence?.trend||"Unavailable"],["EMA20 (₹)",evidence?val(evidence.ema20):"Unavailable"],["EMA50 (₹)",evidence?val(evidence.ema50):"Unavailable"],["ADX14",evidence?val(evidence.adx,1):"Unavailable"],["Relative volume vs preceding 20",evidence?val(evidence.multiple)+"x":"Unavailable"]]);
   para(p,"These are past observations, not predictions. A strong ADX does not determine direction. No independently validated win probability exists.");
   p=page(5,"Chart and patterns","Historical price levels, not trade entries or targets");
   const prev=series.slice(-21,-1);
   const lo=prev.length===20?Math.min(...prev.map(x=>x.low)):null;
   const hi=prev.length===20?Math.max(...prev.map(x=>x.high)):null;
   table(p,[["Price sessions loaded",series.length],["Last 20 prior-session lows (₹)",val(lo)],["Last 20 prior-session highs (₹)",val(hi)],["Observed pattern",evidence?.pattern||"Unavailable"],["Recent 60-session high (₹)",evidence?val(evidence.high60):"Unavailable"],["Recent 60-session low (₹)",evidence?val(evidence.low60):"Unavailable"]]);
   para(p,"The displayed highs/lows are descriptive historical extrema, not validated support/resistance, stop losses or forecast targets. Pattern finder detects only existing coded patterns.");
   p=page(6,"Sector and peer comparison","Do not imply index coverage without membership verification");
   table(p,[["Sector membership","Unavailable — no validated issuer-to-sector reference loaded"],["Comparable company set","Unavailable"],["Peer valuation rankings","Unavailable"],["Sector relative performance","Unavailable"],["Index constituent coverage","Unavailable"]]);
   para(p,"The browser may screen uploaded symbols, but that set must not be represented as a complete Nifty index or industry universe.");
   p=page(7,"Historical risk analysis","Risk facts from available data; no future price target");
   let drawdown=null;
   if(series.length>1){let peak=0,worst=0;for(const bar of series){peak=Math.max(peak,bar.close);if(peak>0)worst=Math.min(worst,(bar.close/peak-1)*100);}drawdown=worst;}
   table(p,[["Sample length",series.length+" daily bars"],["Observed peak-to-trough close drawdown",drawdown===null?"Unavailable":val(drawdown,1)+"%"],["Last-day turnover and liquidity","Unavailable — requires validated shares traded and price"],["ATR14","Unavailable in current report engine"],["Corporate-action adjustment",evidence?.kind==="tej_eod"?"Unadjusted feed":"Not verified"],["Forward returns / win rate","Not measured"]]);
   para(p,"Historical drawdown depends on sample start and price adjustments. Past volatility or returns cannot guarantee future outcomes.");
   p=page(8,"Research summary and sources","Evidence completeness and independent verification");
   table(p,[["Stock",symbol],["Market source",type],["As-of session",dates],["Financial source",financial?.source||"No dated financial source"],["Fundamentals complete","No independent audit provided"],["Technical observations",evidence?"Historical-only":"Unavailable"],["Recommendation / price target","NOT PROVIDED"],["Report status","RESEARCH ONLY"]]);
   para(p,"Educational and informational research only. Not a SEBI-registered recommendation. No buy/sell signal, price target, stop loss or future return is implied.");
   para(p,"Source and licensing notice: TejHQ prices, if used, are NSE bhavcopy-derived, raw and not corporate-action adjusted. Public/commercial redistribution rights have not been verified. User uploads are not source-audited.");
   host.append(root);
   const controls=e("div","msk-report-actions");const btn=e("button","btn","Print eight-page report / Save PDF");btn.type="button";
   btn.addEventListener("click",()=>{document.body.classList.add("msk-report-printing");try{window.print();}finally{setTimeout(()=>document.body.classList.remove("msk-report-printing"),1000);}});
   controls.append(btn);host.prepend(controls);
   return {pages:root.querySelectorAll(".msk-report-page").length,symbol};
 }
 window.MSKReport={render};
})();
