"use strict";
/**
 * Dashboard evidence brief. All values come from the selected dated OHLCV
 * series; never fill missing fundamental data or infer trading recommendations.
 */
(() => {
 const root=document.getElementById("dashboardEvidence");
 const web=window.MSKWeb;
 if(!root||!web?.features)return;
 const F=web.features;
 const $=id=>document.getElementById(id);
 const fmt=(n,d=2)=>Number.isFinite(n)?Number(n).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d}):"Unavailable";
 function node(tag,text,cls){
   const el=document.createElement(tag);
   if(text!==undefined)el.textContent=String(text);
   if(cls)el.className=cls;
   return el;
 }
 function cell(container,label,value,note){
   const box=node("article",undefined,"dash-evidence-metric");
   box.append(node("div",label,"dash-evidence-label"),node("strong",value,"dash-evidence-value"));
   if(note)box.append(node("p",note,"dash-evidence-note"));
   container.append(box);
 }
 function action(parent,label,tab){
   const btn=node("button",label,"btn ghost dash-evidence-action");
   btn.type="button";
   btn.addEventListener("click",()=>window.MSKDashboard?.go(tab,{scroll:false}));
   parent.append(btn);
 }
 function status(parent,content,warning){
   parent.append(node("p",content,warning?"dash-evidence-warning":"dash-evidence-status"));
 }
 function render(){
   root.replaceChildren();
   root.setAttribute("aria-busy","false");
   const top=node("header",undefined,"dash-evidence-head");
   const title=node("div");
   title.append(node("div","STOCK RESEARCH / EVIDENCE BRIEF","eyebrow"),
     node("h2","Snapshot & data confidence"));
   top.append(title);
   const label=node("span","NOT LIVE","dash-evidence-pill");
   top.append(label);root.append(top);
   const insight=node("div",undefined,"dash-evidence-grid");
   const notes=node("div",undefined,"dash-evidence-notes");
   const actions=node("div",undefined,"dash-evidence-actions");
   root.append(insight,notes,actions);
   const evidence=F.getResearch?.();
   const symbol=String($("ticker")?.value||"").trim().toUpperCase();
   const source=web.state.kind;
   const entry=F.library?.get(symbol);
   const bars=entry?.bars;
   const usable=evidence&&Array.isArray(bars)&&bars.length>=50&&
     bars.at(-1)?.date===evidence.session && entry.source===source;
   if(!usable){
     cell(insight,"Selected symbol",symbol||"None");
     cell(insight,"Historical close","Unavailable");
     cell(insight,"Session / data source","Not verified");
     cell(insight,"Fundamentals","Not verified");
     status(notes,"No matching source-dated research is loaded for the selected symbol. Search an NSE company or import a source-dated CSV. Synthetic examples are for interface testing only.",true);
     action(actions,"Import market data","overview");
     return;
   }
   const last=bars.at(-1),prev=bars.at(-2);
   const close=Number(last.close),prevClose=Number(prev?.close);
   const change=Number.isFinite(prevClose)&&prevClose>0?(close/prevClose-1)*100:null;
   const recent=bars.slice(-Math.min(60,bars.length));
   const low=Math.min(...recent.map(x=>Number(x.low)));
   const high=Math.max(...recent.map(x=>Number(x.high)));
   const kind=source==="tej_eod"?"NSE historical EOD via TejHQ":source==="uploaded"?"User-uploaded CSV":source==="synthetic"?"Synthetic demo":"Unverified";
   label.textContent=source==="synthetic"?"SYNTHETIC":source==="tej_eod"?"HISTORICAL EOD":source==="uploaded"?"USER CSV":"UNVERIFIED";
   label.dataset.kind=source;
   cell(insight,"Selected company",symbol,"Market: NSE symbol; company name not independently confirmed");
   cell(insight,"Historical close","₹"+fmt(close),
     change===null?"Daily change unavailable":"Daily close change "+(change>=0?"+":"")+fmt(change)+"%");
   cell(insight,"Trading session",evidence.session,kind+" • "+bars.length+" candles");
   cell(insight,"Historical range ("+recent.length+" sessions)","₹"+fmt(low)+" – ₹"+fmt(high),"Observed lows and highs, not price targets");
   cell(insight,"EMA 20 / EMA 50","₹"+fmt(evidence.ema20)+" / ₹"+fmt(evidence.ema50),
     "Historical trend: "+evidence.trend);
   cell(insight,"ADX 14 / Volume",fmt(evidence.adx,1)+" / "+fmt(evidence.multiple)+"x",
     "Trend strength: "+evidence.adxLabel+" • Volume: "+evidence.volumeLabel);
   const financial=evidence.fundamental;
   cell(insight,"Company financial statements",financial?.asof||"Not provided",
     financial?"User-supplied: "+financial.source:"No independently verified company filings available");
   cell(insight,"Research confidence",source==="tej_eod"?"Historical price data only":source==="synthetic"?"Demo / not investable":"Unverified user data",
     "No independently tested prediction or investment recommendation");
   if(source==="tej_eod")
     status(notes,"Historical end-of-day series, not a live quote. The provider's data are raw/unadjusted; corporate actions and redistribution rights have not been independently verified.",true);
   else if(source==="synthetic")
     status(notes,"All market figures above are simulated. No real stock performance, investment accuracy or index coverage can be inferred.",true);
   else
     status(notes,"Imported prices and any company financial figures are user-declared and have not been independently verified.",true);
   if(!financial)status(notes,"Fundamental analysis is incomplete until source-dated company statements are supplied.",true);
   action(actions,"Review technicals","technical");
   action(actions,"Check fundamentals","fundamental");
   action(actions,"Open eight-page research report","pdf");
   root.dataset.symbol=symbol;
   root.dataset.source=source;
   root.dataset.asof=evidence.session;
 }
 // Use the same browser-local market library shared by the nine pages.
 document.addEventListener("msk:stock-ready",render);
 document.addEventListener("msk:import-done",render);
 $("ticker")?.addEventListener("input",render);
 // Other modules can explicitly refresh an evidence brief after an import.
 window.MSKDashboardEvidence={render};
 render();
})();
