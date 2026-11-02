"use strict";
/* MSK Web Advisory — browser-local portfolio audit. No broker account, DB writes,
   Telegram integrations or credentials. Never infer missing prices or returns. */
(() => {
 const core=window.MSKWeb, F=core?.features;
 if(!core||!F) return;
 const $=id=>document.getElementById(id);
 const fmt=(x,d=2)=>Number.isFinite(x)?x.toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d}):"Unavailable";
 const escapeCSV=v=>'"'+String(v??"").replace(/"/g,'""')+'"';
 let audit=null, busy=false, generation=0;
 const safeSymbol=s=>/^[A-Z][A-Z0-9&._-]{0,20}$/.test(s) && !/^DEMO\d+$/.test(s);
 function downloadCSV(rows,name){
   const blob=new Blob([rows.map(row=>row.map(escapeCSV).join(",")).join("\n")],{type:"text/csv;charset=utf-8"});
   const url=URL.createObjectURL(blob),a=document.createElement("a");
   a.href=url;a.download=name;document.body.append(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1500);
 }
 function note(node,t,cls){const p=document.createElement("p");p.textContent=t;if(cls)p.className=cls;node.append(p);return p;}
 function button(label,handler,cls="btn"){
   const b=document.createElement("button");b.type="button";b.textContent=label;b.className=cls;b.addEventListener("click",handler);return b;
 }
 function isActualEOD(entry){
   const b=entry?.bars?.at(-1);
   const age=b?(Date.now()-Date.parse(b.date+"T00:00:00Z"))/86400000:Infinity;
   return entry?.source==="tej_eod"&&safeSymbol(entry.symbol||"BEL")&&
     Number.isFinite(b?.close)&&b.close>0&&
     /^\d{4}-\d{2}-\d{2}$/.test(b.date)&&age>=-1&&age<=14;
 }
 async function providerPrice(symbol){
   if(!safeSymbol(symbol))return {error:"Invalid NSE symbol"};
   const loaded=F.library.get(symbol);
   if(loaded?.source==="tej_eod"&&isActualEOD({...loaded,symbol})){
     return {price:loaded.bars.at(-1).close,date:loaded.bars.at(-1).date,source:"TejHQ NSE EOD (loaded)",status:"historical"};
   }
   const now=Date.now(),from=new Date(now-260*86400000).toISOString().slice(0,10),to=new Date(now).toISOString().slice(0,10);
   const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),11000);
   try{
     const response=await fetch("https://api.tejhq.dev/v1/ohlcv/nse/"+encodeURIComponent(symbol)+"?from="+from+"&to="+to,
       {mode:"cors",credentials:"omit",headers:{accept:"application/json"},signal:ctl.signal});
     if(!response.ok)throw Error("Provider HTTP "+response.status);
     const data=await response.json();
     if(data?.meta?.symbol!==symbol||String(data.meta.exchange).toUpperCase()!=="NSE"||
        !Array.isArray(data.data)||data.data.length<50||data.data.length>400)
       throw Error("Provider symbol, exchange or history mismatch");
     const bars=data.data,dates=new Set();
     for(const b of bars){
       const d=b.date,o=Number(b.open),h=Number(b.high),l=Number(b.low),c=Number(b.close),v=Number(b.volume);
       if(!/^\d{4}-\d{2}-\d{2}$/.test(d)||dates.has(d)||![o,h,l,c,v].every(Number.isFinite)||
          Math.min(o,h,l,c)<=0||h<Math.max(o,c,l)||l>Math.min(o,c,h)||v<0)
         throw Error("Invalid dated OHLCV");
       dates.add(d);
     }
     const last=[...bars].sort((a,b)=>a.date.localeCompare(b.date)).at(-1);
     const age=(Date.now()-Date.parse(last.date+"T00:00:00Z"))/86400000;
     if(age< -1||age>14)throw Error("Stale or future EOD session");
     return {price:Number(last.close),date:last.date,source:"TejHQ NSE historical EOD (unadjusted)",status:"historical"};
   }catch(e){return {error:e.name==="AbortError"?"Price request timed out":String(e.message).slice(0,100)};}
   finally{clearTimeout(timer);}
 }
 function compute(holdings,quotes){
   const entries=holdings.map((h,i)=>{
     const quote=quotes.get(h.symbol)||{},cost=h.quantity*h.buy_price;
     const valid=Number.isFinite(quote.price)&&quote.price>0&&quote.date;
     return {...h,cost,price:valid?quote.price:null,date:valid?quote.date:null,
       source:valid?quote.source:"Unavailable",value:valid?h.quantity*quote.price:null,
       gain:valid?h.quantity*(quote.price-h.buy_price):null,error:valid?null:(quote.error||"Price unavailable")};
   });
   const invested=entries.reduce((s,x)=>s+x.cost,0);
   const priced=entries.filter(x=>x.value!==null),missing=entries.filter(x=>x.value===null);
   const dates=[...new Set(priced.map(x=>x.date))];
   const commonDate=missing.length===0&&dates.length===1?dates[0]:null;
   const knownCost=priced.reduce((s,x)=>s+x.cost,0);
   const knownValue=priced.reduce((s,x)=>s+x.value,0);
   const maxWeight=Math.max(...entries.map(x=>x.cost/invested*100),0);
   const warnings=[];
   if(missing.length)warnings.push("Prices unavailable for "+missing.length+" holding(s); no complete portfolio valuation or P&L.");
   if(dates.length>1)warnings.push("Different EOD dates; do not combine into a same-day portfolio snapshot.");
   if(maxWeight>=25)warnings.push("Single-stock invested-cost concentration is "+fmt(maxWeight,1)+"% (25%+).");
   if(entries.length<5)warnings.push("Fewer than five holdings; diversification may be limited.");
   warnings.push("Sector weights, corporate actions, taxes, dividends and transaction costs are not verified.");
   return {entries,invested,knownCost,knownValue,priced:priced.length,missing:missing.length,
     commonDate,totalValue:commonDate?knownValue:null,
     totalGain:commonDate?knownValue-invested:null,
     totalReturn:commonDate?(knownValue/invested-1)*100:null,
     maxWeight,warnings,createdAt:new Date().toISOString()};
 }
 async function run(status,runButton){
   const data=window.MSKPortfolio;
   if(!data?.rows?.length||busy)return;
   busy=true;const runID=++generation;
   audit=null;runButton.disabled=true;
   const symbols=[...new Set(data.rows.map(x=>x.symbol))],quotes=new Map();
   status.textContent="Analyzing "+symbols.length+" NSE holding(s). Historical data only; no orders.";
   let cursor=0,processed=0;
   async function worker(){
     while(cursor<symbols.length&&runID===generation){
       const symbol=symbols[cursor++];
       const price=await providerPrice(symbol);
       if(runID!==generation)return;
       quotes.set(symbol,price);processed++;
       status.textContent="Audit progress: "+processed+"/"+symbols.length+" symbols checked.";
     }
   }
   try{
     await Promise.all(Array.from({length:Math.min(3,symbols.length)},()=>worker()));
     if(runID!==generation)return;
     audit=compute(data.rows,quotes);
     status.textContent="Audit generated. "+audit.priced+"/"+audit.entries.length+" historical prices available. "+
       (audit.commonDate?"Same-date coverage complete.":"Incomplete or mixed-date valuation; no aggregate P&L.");
   }finally{
     busy=false;
     core.renderTab();
   }
 }
 function csv(){
   if(!audit)return;
   const rows=[["symbol","quantity","buy_price_inr","invested_cost_inr","historical_eod_inr",
     "eod_date","historical_value_inr","historical_unrealized_gain_inr","cost_weight_percent","price_source","status"]];
   for(const x of audit.entries){
     rows.push([x.symbol,x.quantity,x.buy_price,x.cost,x.price,x.date,x.value,x.gain,
       (x.cost/audit.invested*100).toFixed(2),x.source,x.error||"historical, not live"]);
   }
   rows.push([],["Audit date UTC",audit.createdAt],["Total invested INR",audit.invested],
     ["Common-date EOD",audit.commonDate||"UNAVAILABLE"],["Total portfolio value INR",audit.totalValue],
     ["Total historical unrealized gain INR",audit.totalGain],["Return percent",audit.totalReturn],
     ["Source note","TejHQ NSE historical raw/unadjusted EOD; licensing and corporate actions unverified"],
     ...audit.warnings.map(w=>["WARNING",w]));
   downloadCSV(rows,"msk-portfolio-audit-"+audit.createdAt.slice(0,10)+".csv");
 }
 function printReport(){
   if(!audit)return;
   const overlay=document.createElement("section");overlay.id="mskPortfolioPrint";
   overlay.setAttribute("aria-label","MSK portfolio audit print report");
   const h=document.createElement("h1");h.textContent="MSK Portfolio Audit";overlay.append(h);
   note(overlay,"Generated "+audit.createdAt+" UTC • Historical research only • Not live or investment advice");
   note(overlay,"Holdings: "+audit.entries.length+" • Priced: "+audit.priced+
     " • Invested: ₹"+fmt(audit.invested)+" • Same-date valuation: "+
     (audit.totalValue===null?"Unavailable":"₹"+fmt(audit.totalValue)));
   if(audit.totalGain!==null)note(overlay,"Historical unrealized gain/loss: ₹"+fmt(audit.totalGain)+
     " ("+fmt(audit.totalReturn)+"%) • EOD "+audit.commonDate);
   const tab=document.createElement("table");const heading=document.createElement("tr");
   for(const label of ["Stock","Qty","Cost ₹","EOD ₹","Date","Value ₹","Gain/Loss ₹","Status"]){
     const cell=document.createElement("th");cell.textContent=label;heading.append(cell);
   }tab.append(heading);
   for(const r of audit.entries){
     const tr=document.createElement("tr");
     for(const item of [r.symbol,r.quantity,fmt(r.cost),r.price===null?"—":fmt(r.price),r.date||"—",
       r.value===null?"—":fmt(r.value),r.gain===null?"—":fmt(r.gain),r.error||"Historical EOD"]){
       const td=document.createElement("td");td.textContent=item;tr.append(td);
     }tab.append(tr);
   }overlay.append(tab);
   const subtitle=document.createElement("h2");subtitle.textContent="Risk & data-quality observations";overlay.append(subtitle);
   for(const w of audit.warnings)note(overlay,"• "+w);
   note(overlay,"Source: TejHQ/NSE historical EOD; data licensing, corporate actions, financial ratios and tax treatment not independently verified.");
   document.body.append(overlay);document.body.classList.add("msk-portfolio-printing");
   try{window.print();}finally{setTimeout(()=>{
     document.body.classList.remove("msk-portfolio-printing");overlay.remove();
   },1500);}
 }
 function panel(parent){
   parent.replaceChildren();
   note(parent,"Private portfolio research. Upload holdings CSV, run an audit to fetch available historical NSE closes, then download the audit. No file sent to MSK servers.","warning");
   const portfolio=window.MSKPortfolio;
   const actions=document.createElement("div");actions.className="msk-portfolio-actions";parent.append(actions);
   actions.append(button(portfolio?.rows?.length?"Replace Holdings CSV":"1. Upload Holdings CSV",
     ()=>window.MSKImport?.open("portfolio")));
   actions.append(button("2. Import Historical Prices CSV",
     ()=>window.MSKImport?.open("price"),"btn ghost"));
   actions.append(button("3. Import Financial Ratios CSV",
     ()=>window.MSKImport?.open("fundamental"),"btn ghost"));
   if(!portfolio?.rows?.length){note(parent,"CSV format: symbol,quantity,buy_price. Upload via the Import Center and confirm import.");return true;}
   const status=note(parent,"Portfolio loaded: "+portfolio.rows.length+
     " holdings. Select Run Portfolio Audit to collect dated EOD quotes.","msk-portfolio-status");
   const runButton=button("Run Portfolio Audit",()=>run(status,runButton));actions.append(runButton);
   runButton.disabled=busy;
   if(!audit){
     note(parent,"Holdings "+portfolio.rows.length+" imported • Original invested cost ₹"+fmt(portfolio.invested)+".");
     const wrap=document.createElement("div");wrap.className="msk-portfolio-table";
     const table=document.createElement("table"),head=document.createElement("thead"),headrow=document.createElement("tr");
     for(const label of ["Stock","Quantity","Buy Price ₹","Invested Cost ₹","Audit Result"]){
       const th=document.createElement("th");th.textContent=label;headrow.append(th);
     }head.append(headrow);table.append(head);
     const tbody=document.createElement("tbody");
     for(const row of portfolio.rows){
       const tr=document.createElement("tr");
       for(const value of [row.symbol,row.quantity,fmt(row.buy_price),fmt(row.quantity*row.buy_price),"Not calculated"]){
         const td=document.createElement("td");td.textContent=value;tr.append(td);
       }tbody.append(tr);
     }table.append(tbody);wrap.append(table);parent.append(wrap);
     note(parent,"No aggregate P&L is calculated until the audit validates every historical close on one common date.","warning");
     return true;
   }
   const title=document.createElement("h3");title.textContent="Portfolio audit results";parent.append(title);
   note(parent,"Holdings: "+audit.entries.length+" | Historical price coverage: "+audit.priced+
     "/"+audit.entries.length+" | Invested ₹"+fmt(audit.invested)+
     " | Largest invested-cost holding "+fmt(audit.maxWeight,1)+"%");
   if(audit.commonDate){
     note(parent,"Common EOD "+audit.commonDate+" | Historical value ₹"+fmt(audit.totalValue)+
       " | Historical unrealized gain/loss ₹"+fmt(audit.totalGain)+" ("+fmt(audit.totalReturn)+"%)");
   }else note(parent,"Portfolio total value/P&L withheld because prices are missing or dated differently.","warning");
   const wrap=document.createElement("div");wrap.className="msk-portfolio-table";
   const table=document.createElement("table"),tr=document.createElement("tr");
   for(const v of ["Stock","Qty","Cost ₹","Cost weight","EOD ₹","Session","Value ₹","Gain/Loss ₹","Status"]){
     const th=document.createElement("th");th.textContent=v;tr.append(th);
   }table.append(tr);
   for(const x of audit.entries){const row=document.createElement("tr");
     for(const val of [x.symbol,x.quantity,fmt(x.cost),fmt(x.cost/audit.invested*100,1)+"%",
       x.price===null?"Unavailable":fmt(x.price),x.date||"Missing",x.value===null?"Unavailable":fmt(x.value),
       x.gain===null?"Unavailable":fmt(x.gain),x.error||"Historical EOD"]){
       const td=document.createElement("td");td.textContent=val;row.append(td);
     }table.append(row);
   }wrap.append(table);parent.append(wrap);
   const risk=document.createElement("h3");risk.textContent="Audit findings & limitations";parent.append(risk);
   for(const w of audit.warnings)note(parent,"• "+w);
   actions.append(button("Download Audit CSV",csv,"btn"));
   actions.append(button("Save Audit PDF (Print)",printReport,"btn ghost"));
   note(parent,"CSV downloads directly. PDF uses your browser Print → Save as PDF. Uploaded holdings are kept in this browser session only.");
   return true;
 }
 const original=F.renderPanel;
 F.renderPanel=(tab,container)=>tab==="portfolio"?panel(container):original(tab,container);
 document.addEventListener("msk:portfolio-import",()=>{generation++;audit=null;busy=false;});
 window.MSKPortfolioAudit={getAudit:()=>audit,compute,providerPrice,download:csv,print:printReport};
})();
