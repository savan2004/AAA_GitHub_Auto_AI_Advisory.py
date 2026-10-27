"use strict";
/* Working no-cost browser features. Never treat uploads or synthetic bars as NSE-verified quotes.
   Values, including financial ratios, stay inside this browser. */
(function(){
 const core=window.MSKWeb;
 if(!core)throw Error("MSK main chart engine is missing");
 const $=id=>document.getElementById(id);
 const library=new Map();
 const statements=new Map();
 let active="";
 let universeKind="none";
 const ui={ticker:$("ticker"),bar:$("dataLabel"),search:$("loadSample"),csv:$("csvFile")};
 const fmt=(x,n=2)=>Number.isFinite(x)?Number(x).toLocaleString("en-IN",{minimumFractionDigits:n,maximumFractionDigits:n}):"Unavailable";
 const cleanSymbol=s=>String(s||"").trim().toUpperCase().replace(/\.NS$/,"").replace(/[^A-Z0-9&._-]/g,"").slice(0,22);
 const N=x=>{if(x===undefined||x===null||String(x).trim()==="")return null;const z=Number(String(x).replace(/,/g,""));return Number.isFinite(z)?z:null;};
 function text(tag,s,cls){const e=document.createElement(tag);e.textContent=s;if(cls)e.className=cls;return e;}
 function p(parent,s,cls){const n=text("p",s,cls);parent.append(n);return n;}
 function btn(label,run,cls="btn"){const b=text("button",label,cls);b.type="button";b.addEventListener("click",run);return b;}
 function table(parent,columns,rows){
   const t=document.createElement("table"),h=document.createElement("thead"),tr=document.createElement("tr");
   for(const title of columns)tr.append(text("th",title));h.append(tr);t.append(h);
   const body=document.createElement("tbody");for(const row of rows){const rr=document.createElement("tr");for(const value of row){const cell=text("td",String(value));rr.append(cell);}body.append(rr);}t.append(body);parent.append(t);return t;
 }
 function status(message,error=false){
   ui.bar.textContent=message;
   ui.bar.style.borderColor=error?"#fc6f85":"#32dec6";
   ui.bar.classList.add("hasData");
 }
 function readDelimited(csv){
   const raw=csv.replace(/^\uFEFF/,"").replace(/\r\n?/g,"\n").split("\n").filter(s=>s.trim().length);
   if(raw.length>25000)throw Error("CSV exceeds maximum 25,000 rows.");
   function cols(line){const items=[];let buff="",quoted=false;
     for(let i=0;i<line.length;i++){const x=line[i];if(x==='"'){if(quoted&&line[i+1]==='"'){buff+='"';i++;}else quoted=!quoted;}else if(x===","&&!quoted){items.push(buff.trim());buff="";}else buff+=x;}
     items.push(buff.trim());return items;}
   const names=cols(raw.shift()).map(x=>x.toLowerCase().replace(/\s/g,"").replace(/^["']|["']$/g,""));
   const result=raw.map(line=>{const vals=cols(line),o={};for(let i=0;i<names.length;i++)o[names[i]]=vals[i]||"";return o;});
   return {names,rows:result};
 }
 function normalizedOHLC(row){
   const dateStr=row.timestamp||row.date||row.session;
   // Strict ISO date convention avoids locale-specific parsing surprises.
   const m=/^(\d{4})-(\d\d)-(\d\d)/.exec(dateStr||"");
   if(!m||Number.isNaN(Date.parse(m[0]+"T00:00:00Z")))throw Error("Invalid timestamp; use YYYY-MM-DD.");
   const o={date:m[0],symbol:cleanSymbol(row.symbol||row.ticker||row.stock||ui.ticker.value)};
   for(const k of ["open","high","low","close","volume"]){o[k]=N(row[k]);if(o[k]===null)throw Error("Missing or invalid "+k);}
   if(!o.symbol||o.open<=0||o.high<=0||o.low<=0||o.close<=0||o.volume<0||o.high<Math.max(o.open,o.close)||o.low>Math.min(o.open,o.close))throw Error("OHLC / volume invalid.");
   return o;
 }
 function importBars(raw,file){
   if(raw.length>2200000)throw Error("2 MB price CSV upload limit.");
   const data=readDelimited(raw);
   if(!["timestamp","date","session"].some(k=>data.names.includes(k))||!["open","high","low","close","volume"].every(k=>data.names.includes(k)))throw Error("CSV needs timestamp/open/high/low/close/volume columns, optionally symbol.");
   const tmp=new Map();
   for(const source of data.rows){const v=normalizedOHLC(source);
     if(!tmp.has(v.symbol))tmp.set(v.symbol,[]);
     tmp.get(v.symbol).push(v);
   }
   if(tmp.size>80)throw Error("Up to 80 symbols per upload.");
   const accepted=new Map();
   for(const [symbol,rows] of tmp){
     rows.sort((a,b)=>a.date.localeCompare(b.date));
     for(let i=1;i<rows.length;i++)if(rows[i].date===rows[i-1].date)throw Error("Duplicate date for "+symbol);
     if(rows.length<50)throw Error(symbol+" requires at least 50 bars.");
     const sample=rows.slice(-500).map(({symbol:ignore,...b})=>b);
     accepted.set(symbol,{bars:sample,source:"uploaded",file:file});
   }
   library.clear();for(const [k,v] of accepted)library.set(k,v);
   universeKind="uploaded";
   setupSymbols();
   status("Imported "+accepted.size+" symbol(s) and "+[...accepted.values()].reduce((s,x)=>s+x.bars.length,0)+" daily candles from user CSV. SOURCE UNVERIFIED.");
   choose(ui.ticker.value,true);
 }
 function setupSymbols(){
   let sel=$("tickerChoices");if(!sel){sel=document.createElement("datalist");sel.id="tickerChoices";document.body.append(sel);ui.ticker.setAttribute("list",sel.id);}
   sel.replaceChildren();for(const symbol of library.keys()){const opt=document.createElement("option");opt.value=symbol;sel.append(opt);}
   let list=$("symbolCount");if(!list){list=text("span","", "chip");list.id="symbolCount";ui.search.after(list);}
   list.textContent=library.size+" SYMBOL"+(library.size===1?"":"S");
 }
 function choose(symbol,autoselect){
   const s=cleanSymbol(symbol);
   if(!library.has(s)){
     if(autoselect && library.size) return choose(library.keys().next().value,false);
     status(s+": no loaded observations. Import a CSV containing this symbol; search is NOT a live NSE data feed.",true);
     return false;
   }
   active=s;ui.ticker.value=s;
   const entry=library.get(s);
   core.dataLoaded(entry.bars,entry.source,entry.file+" • "+s);
   return true;
 }
 function startDemo(){
   library.clear();statements.clear();
   const syms=["RELIANCE","TCS","INFY","HDFCBANK","ITC","LT","SUNPHARMA","MARUTI","NTPC","HINDUNILVR"];
   const original=core.demo();
   for(let k=0;k<syms.length;k++){
     const factor=[1,2.13,0.93,0.61,.21,2.94,1.36,10.1,.26,1.8][k];
     const bars=original.map((v,j)=>{
       // Deterministically different illustrative trajectories per symbol, NOT sourced prices.
       const adjustment=(1+.013*Math.sin(j*.13+k)+.003*k*Math.cos(j*.11))*factor;
       const a=[v.open,v.high,v.low,v.close].map(x=>x*adjustment);
       const o=a[0],cl=a[3],hi=Math.max(a[1],o,cl),lo=Math.min(a[2],o,cl);
       return {date:v.date,open:o,high:hi,low:lo,close:cl,volume:Math.round(v.volume*(.7+k*.11))};
     });
     library.set(syms[k],{bars,source:"synthetic",file:"SIMULATED TEST UNIVERSE — NOT NSE QUOTES"});
   }
   universeKind="synthetic";setupSymbols();choose("RELIANCE",true);
   status("SYNTHETIC: 10 TEST companies. NOT real stocks, prices, financial results, screeners or market breadth.");
 }
 function fundamentalsImport(raw){
   if(raw.length>1100000)throw Error("Fundamentals CSV limit: 1 MB.");
   const d=readDelimited(raw),need=["symbol","asof","source","pe","roe_percent","revenue_growth_percent","market_cap_inr","debt_equity_ratio"];
   if(!need.every(x=>d.names.includes(x)))throw Error("Use columns: "+need.join(", "));
   const temp=new Map();
   for(const row of d.rows){
     const key=cleanSymbol(row.symbol),date=row.asof;
     if(!key||!/^\d{4}-\d\d-\d\d$/.test(date)||!row.source?.trim())throw Error("Each fundamental row requires symbol, YYYY-MM-DD asof and source.");
     const info={symbol:key,asof:date,source:row.source.slice(0,160)};
     for(const field of need.slice(3))info[field]=N(row[field]);
     if(info.pe!==null&&(info.pe<0||info.pe>2000))throw Error("P/E out of plausible range.");
     if(info.debt_equity_ratio!==null&&(info.debt_equity_ratio<0||info.debt_equity_ratio>1000))throw Error("D/E ratio must be in ratio units (x), not Yahoo percent points.");
     if(info.market_cap_inr!==null&&info.market_cap_inr<0)throw Error("Negative market cap.");
     temp.set(key,info);
   }
   for(const [k,v] of temp)statements.set(k,v);
   status("Imported fundamentals for "+temp.size+" companies. Source is USER-DECLARED, not audited by MSK.");
   core.renderTab();
 }
 function results(){
   const out=[];for(const [symbol,entry] of library){
     const q=core.compute(entry.bars);const last=entry.bars.at(-1),prev=entry.bars.at(-2);
     out.push({symbol,entry,q,change:prev.close?(last.close/prev.close-1)*100:0,
       sma20:entry.bars.length>=20?entry.bars.slice(-20).reduce((s,x)=>s+x.close,0)/20:null});
   }return out;
 }
 function csvDownload(rows,filename){
   const csv=rows.map(row=>row.map(c=>'"'+String(c??"").replace(/"/g,'""')+'"').join(",")).join("\n");
   const a=document.createElement("a"),blob=new Blob([csv],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob);
   a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 function barList(parent,title,columns,rows){p(parent,title);if(rows.length)table(parent,columns,rows);else p(parent,"No matched companies.","warning");}
 function fundamentalTab(panel){
   p(panel,"Upload optional financial-statement CSV. All figures are supplied by you, with a source and reporting date. Price-only history cannot produce P/E, ROE or revenue growth.","warning");
   const inp=document.createElement("input");inp.type="file";inp.accept=".csv,text/csv";inp.id="fundCSV";inp.setAttribute("aria-label","Import financial statement CSV");panel.append(inp);
   inp.addEventListener("change",async e=>{try{if(e.target.files?.[0])fundamentalsImport(await e.target.files[0].text());}catch(error){status(error.message,true);}});
   const t=statements.get(active);
   if(t){
     p(panel,"Company "+active+" • Report date "+t.asof+" • User-supplied source: "+t.source);
     table(panel,["Financial measure","Reported value"],[
       ["P/E",fmt(t.pe)],["Return on equity",t.roe_percent===null?"Unavailable":fmt(t.roe_percent)+"%"],
       ["Revenue growth",t.revenue_growth_percent===null?"Unavailable":fmt(t.revenue_growth_percent)+"%"],
       ["Market capitalization",t.market_cap_inr===null?"Unavailable":"₹"+fmt(t.market_cap_inr,0)],
       ["Debt/equity",t.debt_equity_ratio===null?"Unavailable":fmt(t.debt_equity_ratio)+"x"]]);
   }else p(panel,"No financial statements imported for "+(active||"this company")+". No fabricated ratios will be shown.","warning");
   p(panel,"Financial CSV header: symbol,asof,source,pe,roe_percent,revenue_growth_percent,market_cap_inr,debt_equity_ratio");
   panel.append(btn("Download Blank Fundamentals Template",()=>csvDownload([["symbol","asof","source","pe","roe_percent","revenue_growth_percent","market_cap_inr","debt_equity_ratio"],[active||"INFY","2026-09-30","Annual report page URL","","","","",""]],"msk-fundamentals-template.csv"),"btn ghost"));
 }
 function screenerTab(panel){
   const list=results();if(!list.length){p(panel,"Upload one or more symbols in price CSV, or run the synthetic test universe.","warning");return;}
   p(panel,"This screener analyses the "+list.length+" UPLOADED "+(universeKind==="synthetic"?"SYNTHETIC TEST":"USER-SUPPLIED")+" series, not live NSE constituents.");
   const select=document.createElement("select");select.id="trendFilter";select.setAttribute("aria-label","Filter trend");
   for(const v of ["Any trend","Uptrend","Downtrend","Sideways"]){let o=document.createElement("option");o.value=v;o.textContent=v;select.append(o);}
   select.style.cssText="background:#0b1525;color:white;border:1px solid #30455d;border-radius:8px;padding:10px;margin-right:10px";
   panel.append(select);
   const min=document.createElement("input");min.id="minADX";min.type="number";min.min="0";min.max="100";min.value="0";min.setAttribute("aria-label","Minimum ADX");min.style.cssText="width:90px;padding:10px;background:#0b1525;color:white;border:1px solid #30455d";panel.append(min);
   const view=document.createElement("div");panel.append(view);
   function update(){
     const q=list.filter(x=>(select.value==="Any trend"||x.q.trend===select.value)&&((x.q.adx||0)>=Number(min.value||0)));
     view.replaceChildren();barList(view,q.length+" matching stocks",["Stock","Close (₹)","Trend","ADX","Vol x","Signal","Date"],
       q.map(x=>[x.symbol,fmt(x.q.last),x.q.trend,fmt(x.q.adx,1),fmt(x.q.ratio),x.q.signal,x.q.date]));
   }select.onchange=update;min.oninput=update;update();
   panel.append(btn("Export Screener CSV",()=>csvDownload([
      ["symbol","asof","close","trend","adx14","volume_multiple","signal","data_source"],
      ...list.map(x=>[x.symbol,x.q.date,x.q.last,x.q.trend,x.q.adx,x.q.ratio,x.q.signal,x.entry.source])
   ],"msk-screener-export.csv")));
 }
 function breadthTab(panel){
   const all=results();
   if(all.length<2){p(panel,"Breadth requires MULTIPLE stock series aligned to the same trading date. Import multi-symbol CSV or test with the synthetic universe.","warning");return;}
   const asof=[...new Set(all.map(x=>x.q.date))].sort().at(-1);
   const valid=all.filter(x=>x.q.date===asof);
   const adv=valid.filter(x=>x.change>0).length,dec=valid.filter(x=>x.change<0).length,flat=valid.length-adv-dec;
   const above20=valid.filter(x=>x.sma20!==null&&x.q.last>x.sma20).length;
   p(panel,"Universe: "+valid.length+"/"+all.length+" uploaded instruments sharing latest date "+asof+". "+(universeKind==="synthetic"?"SIMULATED / NOT REAL MARKET BREADTH.":"Not independently verified as Nifty or NSE constituent membership."),"warning");
   table(panel,["Observation","Calculated value"],[
     ["Advances",adv],["Declines",dec],["Unchanged",flat],
     ["Advance / decline ratio",dec?fmt(adv/dec,2):"Undefined (zero decliners)"],
     ["Above own 20-day moving average",valid.length?fmt(above20/valid.length*100,1)+"%":"Unavailable"],
     ["Coverage",valid.length+" of "+all.length+" uploaded stocks"]]);
   p(panel,"Advances are based on each stock's most recent close versus its previous close. Mixed session dates are excluded.");
   panel.append(btn("Export Breadth CSV",()=>csvDownload([
      ["symbol","asof","change_percent","above_sma20","source"],
      ...valid.map(x=>[x.symbol,x.q.date,x.change,x.q.last>x.sma20,x.entry.source])
   ],"msk-breadth-"+asof+".csv")));
 }
 function currentEvidence(){
   const row=library.get(active);if(!row)return null;
   const q=core.compute(row.bars);return {symbol:active,kind:row.source,session:q.date,
     close:q.last,trend:q.trend,adx:q.adx,adxLabel:q.adxLabel,
     multiple:q.ratio,volumeLabel:q.volLabel,pattern:q.pattern,signal:q.signal,
     quality:q.quality,ema20:q.e20.at(-1),ema50:q.e50.at(-1),
     fundamental:statements.get(active)||null};
 }
 function offlineAnswer(question){
   const raw=String(question||"").slice(0,550),q=raw.toLowerCase(),e=currentEvidence();
   const suffix="\n\nLOCAL RULE-BASED RESEARCH • NOT GENERATIVE AI. No provider connection is configured.";
   if(/what is|meaning|define|explain|क्या है|શું છે/.test(q)){
     if(/\badx\b/.test(q))return "ADX measures TREND STRENGTH, not direction. Below 20 is weak; 20–25 building; 25–35 strong; above 35 very strong."+suffix;
     if(/\broe\b/.test(q))return "ROE = net income / average shareholder equity. Use the company's financial filings and period; OHLC candles cannot supply this."+suffix;
     if(/\bpe\b|\bp\/e\b/.test(q))return "P/E compares current share price to earnings per share. Earnings definitions, reporting date and adjusted results matter."+suffix;
     if(/volume|vol/.test(q))return "Relative volume compares a candle's volume with the preceding 20 sessions. 1.5x is above normal, 2x high, 3x explosive."+suffix;
   }
   if(!e)return "No company evidence loaded. Choose SYNTHETIC TEST to explore, or upload OHLCV CSV for your stock. Stock search alone is not a live feed."+suffix;
   const caution=e.kind==="synthetic"?"SYNTHETIC DATA — NOT NSE QUOTES":"User-uploaded price data; original source not independently verified.";
   const evidence="Stock "+e.symbol+" ("+e.session+"): "+caution+"\nLast close ₹"+fmt(e.close)+
     "; trend "+e.trend+"; EMA20 ₹"+fmt(e.ema20)+"; EMA50 ₹"+fmt(e.ema50)+
     "; ADX "+fmt(e.adx,1)+" ("+e.adxLabel+"); Volume "+fmt(e.multiple)+"x ("+e.volumeLabel+").\n"+
     "Pattern "+e.pattern+". Signal "+e.signal+" / "+e.quality+".";
   if(/fundamental|roe|valuation|profit|earnings|debt|revenue|\bpe\b|\bp\/e\b/.test(q)){
     if(!e.fundamental)return evidence+"\nFinancial results NOT IMPORTED: P/E, ROE, debt/equity and earnings cannot be inferred from price bars."+suffix;
     const f=e.fundamental;return evidence+"\nUSER-SUPPLIED financial statement "+f.asof+"; source "+f.source+"; P/E "+fmt(f.pe)+
       "; ROE "+fmt(f.roe_percent)+"%; Debt/equity "+fmt(f.debt_equity_ratio)+"x."+suffix;
   }
   if(/breadth|advance|decline|screener|scan|universe/.test(q))return evidence+
     "\n"+library.size+" uploaded symbols in local screener; use the Breadth or Screener button for full cross-sectional calculations."+suffix;
   if(/buy|sell|target|entry|stop|trade|support|resistance|pattern|risk|chart|trend|adx|volume|analyse|analysis|view|outlook/.test(q)){
     return evidence+"\nRisk check: no issuer filings, audited price feed, confirmed timestamp licensing, paper-trade verification, or tested execution edge. No price target is inferred."+suffix;
   }
   return evidence+"\nAsk about trend, ADX, volume, risk, fundamental imports, screening or breadth. Real generative analysis requires a secured server-side AI connection."+suffix;
 }
 function renderPanel(tab,panel){
   if(tab==="fundamental"){fundamentalTab(panel);return true;}
   if(tab==="screener"){screenerTab(panel);return true;}
   if(tab==="breadth"){breadthTab(panel);return true;}
   if(tab==="ai"){p(panel,"Local evidence Q&A works now without login or Telegram. Generative Groq/Gemini requires a server-only API and credentials. Neither market data nor secrets are sent by local Q&A.","warning");return true;}
   return false;
 }
 const button=btn("Analyze Symbol",()=>choose(ui.ticker.value,false),"btn");
 button.id="analyzeSymbol";ui.ticker.closest(".ticker-input").after(button);
 const help=text("small","To import several stocks, include optional SYMBOL column in the OHLCV CSV. Search shows only loaded symbols.","muted");
 ui.csv.closest(".ticker-input").append(help);
 const sampleBtn=ui.search;
 // Capture to replace old single-symbol event handler and offer a useful test universe.
 sampleBtn.addEventListener("click",event=>{event.stopImmediatePropagation();startDemo();},true);
 ui.csv.addEventListener("change",async event=>{
   event.stopImmediatePropagation();const file=event.target.files?.[0];if(!file)return;
   try{importBars(await file.text(),file.name);}catch(error){status("CSV import failed: "+error.message,true);}
 },true);
 ui.ticker.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();choose(ui.ticker.value,false);}});
 ui.ticker.addEventListener("change",()=>{if(library.has(cleanSymbol(ui.ticker.value)))choose(ui.ticker.value,false);});
 core.features={renderPanel,offlineAnswer,getResearch:currentEvidence,startDemo,importBars,importFundamentals:fundamentalsImport,choose,library,statements,results};
 $("chatHistory").firstElementChild.textContent="Ask about indicators or uploaded stock research. Local evidence answers work without paid AI; generative AI is currently not connected.";
 $("chatInput").placeholder="Ask about trend, ADX, volume, fundamentals...";
 core.renderTab();
})();
