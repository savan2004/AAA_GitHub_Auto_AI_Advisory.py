"use strict";
/**
 * MSK NSE research symbol viewer.
 *
 * TradingView's LEGACY dynamically injected embed silently displayed its
 * default NASDAQ:AAPL even when our webpage said NSE:BEL. In financial
 * research a wrong-symbol chart is a critical integrity failure.
 *
 * Official widget data FAQs confirm restricted exchange symbols can only
 * be displayed on TradingView itself. This module refuses to show a
 * wrong-symbol chart and links to the legitimate provider instead.
 */
(() => {
 const app=window.MSKWeb;
 if(!app?.features)throw Error("Load MSK app.js and features.js before market_widget.js");
 const $=id=>document.getElementById(id);
 const features=app.features;
 const originalChoose=features.choose;
 const originalPanel=features.renderPanel;
 const originalAnswer=features.offlineAnswer;
 let selected=null;
 let requestRevision=0;
 let requestController=null;
 const EOD_PROVIDER="https://api.tejhq.dev";
 const MAX_STALENESS_DAYS=14;
 // This preview is non-commercial and unindexed; TejHQ does not grant rights
 // to commercially redistribute the underlying exchange bhavcopy.
 const sourceLabel=s=>"TejHQ NSE bhavcopy • "+s+" • historical unadjusted EOD";
 const normalize=input=>String(input||"").toUpperCase().trim().replace(/^NSE:/,"").replace(/\.NS$/,"");
 const valid=input=>/^[A-Z][A-Z0-9&._-]{0,20}$/.test(input)&&!/^DEMO\d+$/.test(input);
 const text=(tag,content,cls)=>{const node=document.createElement(tag);if(content!==undefined)node.textContent=content;if(cls)node.className=cls;return node;};
 function tradingViewURL(symbol,suffix=""){return "https://in.tradingview.com/symbols/NSE-"+encodeURIComponent(symbol)+"/"+suffix;}
 function link(symbol,title="Open verified symbol page on TradingView"){
   const a=text("a",title+" ↗","tv-source-link");
   a.href=tradingViewURL(symbol);a.target="_blank";a.rel="noopener noreferrer nofollow";return a;
 }
 function providerCard(symbol){
   const card=text("div",undefined,"provider-disclosure");
   const main=text("div",undefined,"provider-disclosure-main");
   main.append(text("strong","NSE:"+symbol+" · Source and Research"));
   main.append(text("p","Historical NSE end-of-day chart loads from TejHQ where available. TradingView is an independent source link. Real-time intraday prices are NOT available."));
   const action=link(symbol,"View NSE:"+symbol+" directly");
   action.classList.add("provider-cta");
   card.append(main,action);
   return card;
 }
 function plot(symbol){
   const wrap=$("chartCanvas").parentElement;
   $("chartCanvas").hidden=true;
   $("chartEmpty").hidden=true;
   $("chartEmpty").style.display="none";
   wrap.classList.add("nse-provider-chart-wrap");
   let mount=$("externalNseChart");
   if(!mount){mount=text("div",undefined,"external-nse-chart");mount.id="externalNseChart";wrap.append(mount);}
   mount.replaceChildren();
   // Real Chromium found TradingView refusing NSE:BEL with:
   // "This symbol is only available on TradingView".
   // Official docs explain that some exchange symbols CANNOT be embedded on
   // ANY timeframe. Do not replace it with NASDAQ:AAPL or another chart.
   const empty=text("div",undefined,"nse-unavailable");
   empty.setAttribute("data-symbol","NSE:"+symbol);
   empty.append(text("div","DATA ACCESS RESTRICTED","nse-unavailable-tag"));
   empty.append(text("div","NSE:"+symbol,"nse-unavailable-symbol"));
   empty.append(text("h3","Real candles require a permitted data source"));
   empty.append(text("p","TradingView does not permit this NSE symbol inside third-party website widgets. Showing Apple, another stock, or invented candles would be misleading. The correct stock can be viewed on TradingView."));
   const action=link(symbol,"View actual NSE:"+symbol+" chart");
   action.classList.add("provider-cta","nse-open-btn");
   empty.append(action);
   empty.append(text("p","MSK chart and indicators will activate automatically if you import ≥50 dated daily OHLCV candles for this stock.","nse-import-note"));
   mount.append(empty);
   const hover=$("hoverBar");
   hover.replaceChildren(text("span","NSE:"+symbol+" • Chart unavailable in embedded free provider. "));
   hover.append(link(symbol));
 }

 async function fetchEOD(symbol,revision){
   // TejHQ keyless EOD API. Official free docs: /v1/ohlcv/nse/{SYMBOL}
   // Dates/data are supplied by provider, not generated from the symbol.
   const now=new Date();
   const end=now.toISOString().slice(0,10);
   const from=new Date(now.getTime()-260*86400000).toISOString().slice(0,10);
   const url=EOD_PROVIDER+"/v1/ohlcv/nse/"+encodeURIComponent(symbol)+
        "?from="+from+"&to="+end;
   const ctrl=new AbortController();requestController=ctrl;
   const timeout=setTimeout(()=>ctrl.abort(),13000);
   try{
     const response=await fetch(url,{
       mode:"cors",credentials:"omit",headers:{accept:"application/json"},
       signal:ctrl.signal
     });
     if(!response.ok)throw Error("TejHQ EOD HTTP "+response.status);
     const payload=await response.json();
     if(payload?.meta?.symbol!==symbol||
       String(payload.meta.exchange||"").toUpperCase()!=="NSE"||
       !Array.isArray(payload.data))throw Error("Provider symbol/exchange mismatch");
     const records=payload.data;
     if(records.length<50)throw Error("Provider has fewer than 50 completed daily candles");
     if(records.length>400)throw Error("Unexpectedly large OHLCV payload");
     const bars=records.map(r=>{
       const bar={date:r.date,open:Number(r.open),high:Number(r.high),
         low:Number(r.low),close:Number(r.close),volume:Number(r.volume)};
       if(!/^\d{4}-\d{2}-\d{2}$/.test(bar.date)||
          ![bar.open,bar.high,bar.low,bar.close,bar.volume].every(Number.isFinite)||
          Math.min(bar.open,bar.high,bar.low,bar.close)<=0||
          bar.volume<0||bar.high<Math.max(bar.open,bar.close,bar.low)||
          bar.low>Math.min(bar.open,bar.close,bar.high))
         throw Error("Provider returned invalid OHLCV candle");
       return bar;
     }).sort((a,b)=>a.date.localeCompare(b.date));
     for(let i=1;i<bars.length;i++){
       if(bars[i].date===bars[i-1].date)throw Error("Duplicate trading session");
     }
     const last=bars.at(-1);
     const age=(Date.now()-Date.parse(last.date+"T00:00:00Z"))/86400000;
     if(age < -1||age>MAX_STALENESS_DAYS)
       throw Error("Historical EOD provider is stale or future dated: "+last.date);
     if(selected!==symbol||requestRevision!==revision)return;
     features.library.clear(); // Never include DEMO rows in a real-symbol screener/breadth.
     features.library.set(symbol,{
       bars,source:"tej_eod",file:sourceLabel(symbol),
       importedAt:Date.now(),asof:last.date
     });
     clearExternal();
     originalChoose(symbol,false);
     $("dataLabel").setAttribute("data-eod-provider","TejHQ");
     $("dataLabel").setAttribute("data-market-asof",last.date);
     $("hoverBar").textContent="NSE:"+symbol+" • TejHQ / NSE bhavcopy • "+bars.length+
       " historical EOD sessions • latest "+last.date+". Hover candles for OHLCV.";
   }catch(err){
     if(selected!==symbol||requestRevision!==revision||!window.document?.body)return;
     const message=err?.name==="AbortError"?"Data source timed out":
        String(err?.message||"Data source unavailable").slice(0,180);
     const mount=$("externalNseChart");
     if(mount){
       const label=mount.querySelector(".nse-unavailable-tag");
       if(label)label.textContent="SOURCE NOT AVAILABLE";
       const h=mount.querySelector(".nse-unavailable h3");
       if(h)h.textContent="Historical NSE data could not be loaded";
       const info=mount.querySelector(".nse-unavailable p");
       if(info)info.textContent="TejHQ free EOD feed could not be verified: "+message+
         ". No fallback prices were substituted. Open this instrument at its source or import dated OHLCV.";
     }
     $("dataLabel").textContent="NSE:"+symbol+
       " • Historical data fetch failed safely: "+message+" • No price or signal shown.";
   }finally{
     clearTimeout(timeout);
     if(requestController===ctrl)requestController=null;
   }
 }
 function clearExternal(){
   requestRevision++;
   if(requestController){requestController.abort();requestController=null;}
   selected=null;
   document.body.classList.remove("market-external");
   const mount=$("externalNseChart");if(mount)mount.remove();
   const wrap=$("chartCanvas").parentElement;
   wrap.classList.remove("nse-provider-chart-wrap");
   $("chartCanvas").hidden=false;
   $("explain").hidden=false;
   $("explain").textContent="Draft Research Summary";
 }
 function external(symbol){
   if(!valid(symbol))return false;
   selected=symbol;
   document.body.classList.remove("market-eod");
   document.body.classList.add("market-external");
   $("ticker").value=symbol;
   // CRITICAL isolation of previously loaded demo/CSV values.
   app.state.data=[];
   app.state.calc=null;
   app.state.kind="external";
   app.state.file="";
   const changes={
     lastPrice:"No MSK quote",priceMeta:"Provider chart displayed separately",
     trend:"Not assessed",adx:"—",adxLabel:"Requires OHLCV",
     vol:"—",volLabel:"Requires OHLCV",
     chartTitle:"NSE:"+symbol+" · Daily Candlestick Chart",
     analysisTitle:"Research Data Status",
     signal:"NOT ASSESSED",
     analysisNote:"MSK indicators and BUY/SELL signals require sourced daily OHLCV candles, not a TradingView embed."
   };
   for(const [id,value] of Object.entries(changes))$(id).textContent=value;
   const rows=$("analysisLines");rows.replaceChildren();
   for(const [name,value] of [
     ["Instrument","NSE:"+symbol],
     ["Price feed","TradingView widget only"],
     ["MSK indicators","Not calculated"],
     ["Fundamentals","Provider link / user CSV"],
     ["Trading signal","Not assessed"]
   ]){
     const row=text("div");row.append(text("span",name),text("b",value));rows.append(row);
   }
   $("explain").hidden=true;
   $("dataLabel").textContent="NSE:"+symbol+" selected. TradingView's own chart; no imported MSK market data. Verify the symbol inside the chart before using any figures.";
   $("dataLabel").style.borderColor="#5dd1e4";
   $("dataLabel").classList.add("hasData");
   plot(symbol);
   app.renderTab();
   const info=$("externalNseChart")?.querySelector(".nse-unavailable h3");
   if(info)info.textContent="Loading dated NSE end-of-day candles…";
   const paragraph=$("externalNseChart")?.querySelector(".nse-unavailable p");
   if(paragraph)paragraph.textContent="Checking TejHQ keyless historical bhavcopy for NSE:"+symbol+
     ". This is NOT a live or intraday feed.";
   const revision=++requestRevision;
   // Fully async: no live provider data is assumed until response validates.
   fetchEOD(symbol,revision);
   return true;
 }
 function choose(symbol,auto){
   const code=normalize(symbol);
   if(features.library.has(code)){
     const cached=features.library.get(code);
     if(cached?.source==="tej_eod"&&Date.now()-(cached.importedAt||0)>10*60*1000){
       return external(code);
     }
     clearExternal();
     return originalChoose(code,auto);
   }
   if(auto&&!code&&features.library.size){clearExternal();return originalChoose("",true);}
   if(!valid(code)){
     $("dataLabel").textContent="Invalid NSE ticker. Enter BEL, INFY, TCS or another exchange-listed symbol.";
     return false;
   }
   return external(code);
 }
 function paragraph(parent,value,cls){parent.append(text("p",value,cls));}
 function panel(tab,parent){
   if(!selected)return originalPanel(tab,parent);
   const symbol=selected;
   const intro="NSE:"+symbol+" · Historical TejHQ end-of-day feed is loading or unavailable. No MSK price or signal is displayed until validated.";
   if(tab==="overview"||tab==="chart"){
     paragraph(parent,intro,"warning");
     parent.append(providerCard(symbol));
     paragraph(parent,"To calculate MSK EMA20/50, ADX14, volume ratio and chart patterns, import a dated OHLCV CSV for NSE:"+symbol+". This external widget is not an OHLC data feed.");
     return true;
   }
   if(tab==="technical"){
     paragraph(parent,"TradingView's free external widget cannot display this NSE symbol. MSK has no historical OHLCV to calculate a real rating, indicator or breakout.","warning");
     parent.append(link(symbol,"View this stock's chart and technicals at source"));
     paragraph(parent,"Import dated OHLCV CSV with ≥50 candles for actual EMA20/EMA50, ADX14 and volume analysis in the MSK Technical button.");
     return true;
   }
   if(tab==="fundamental"){
     paragraph(parent,"No independently audited financial statements are connected to this MSK website. Never infer P/E, ROE, debt or revenue growth from candle data.","warning");
     const holder=text("div",undefined,"financial-next-step");
     holder.append(text("h3","Check official financial disclosures"));
     paragraph(holder,"Review the company's dated exchange filings and TradingView's provider-hosted financial page. Financial information is not copied into MSK.");
     const a=link(symbol,"Open financial overview for NSE:"+symbol);
     a.href=tradingViewURL(symbol,"financials-overview/");
     holder.append(a);
     parent.append(holder);
     return true;
   }
   if(tab==="screener"||tab==="breadth"){
     paragraph(parent,"A single external stock chart cannot calculate a multi-company screening universe or NSE market breadth. Import source-dated multi-stock CSV first.","warning");
     paragraph(parent,"No fictional DEMO01–DEMO10 metrics are mixed with NSE:"+symbol+".");
     return true;
   }
   if(tab==="pdf"){
     paragraph(parent,"A complete MSK research PDF cannot be generated from an iframe alone. The original company reports, earnings dates and OHLCV are not available to MSK.","warning");
     parent.append(link(symbol,"See source chart"));
     return true;
   }
   if(tab==="ai"){
     paragraph(parent,"Ask educational questions. Generative AI is NOT connected, and MSK has no source-dated NSE:"+symbol+" evidence for a personal investment recommendation.","warning");
     parent.append(link(symbol));
     return true;
   }
   return false;
 }
 function answer(question){
   if(!selected)return originalAnswer(question);
   const q=String(question||"").toLowerCase();
   if(/what is|meaning|explain|define|क्या है|શું છે/.test(q)){
     if(/\badx\b/.test(q))return "ADX measures trend strength; MSK has no ADX value for NSE:"+selected+" without its dated OHLCV history. Educational guidance only, NOT generative AI.";
     if(/\broe\b/.test(q))return "ROE is net income divided by average shareholders' equity. MSK has no financial statements for NSE:"+selected+". Educational guidance only, NOT generative AI.";
   }
   return "NSE:"+selected+" selected. Its third-party chart is viewable above, but MSK has no licensed OHLCV or filings for this stock. BUY/SELL, target, ADX and valuation are intentionally NOT assessed. Import source-dated data for numerical analysis. Local rules only, NOT generative AI.";
 }
 features.choose=choose;
 features.renderPanel=panel;
 features.offlineAnswer=answer;
 features.getExternalSymbol=()=>selected;
 app.marketWidget={clearExternal,showExternal:external};
 const input=$("ticker"),analyze=$("analyzeSymbol");
 analyze.addEventListener("click",event=>{event.stopImmediatePropagation();choose(input.value,false);},true);
 input.addEventListener("keydown",event=>{
   if(event.key==="Enter"){event.preventDefault();event.stopImmediatePropagation();choose(input.value,false);}
 },true);
 input.addEventListener("change",event=>{
   event.stopImmediatePropagation();const code=normalize(input.value);
   if(code&&code!==selected)choose(code,false);
 },true);
 $("loadSample").addEventListener("click",()=>clearExternal(),true);
 const params=new URL(window.location.href).searchParams;
 const fromURL=params.get("symbol")||params.get("tvwidgetsymbol")?.replace(/^NSE:/i,"");
 if(fromURL&&valid(normalize(fromURL)))external(normalize(fromURL));
})();