"use strict";
/**
 * MSK NSE research symbol viewer.
 *
 * TradingView's LEGACY dynamically injected embed silently displayed its
 * default NASDAQ:AAPL even when our webpage said NSE:BEL. In financial
 * research a wrong-symbol chart is a critical integrity failure.
 *
 * Modern official TradingView Web Components take the symbol via an explicit
 * DOM attribute. The only provider-rendered prices are inside that isolated
 * provider component, never copied into MSK indicators, trading signals, CSV
 * or AI. A direct provider link remains available if the widget cannot load.
 *
 * Required module imports in index.html:
 * https://widgets.tradingview-widget.com/w/en/tv-advanced-chart.js
 * https://widgets.tradingview-widget.com/w/en/tv-technical-analysis.js
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
   main.append(text("strong","NSE:"+symbol+" · Third-party chart"));
   main.append(text("p","Charts and delayed/live quotes are supplied and labelled by TradingView. No MSK price, ADX, volume, pattern or trade signal is calculated without an independent OHLCV dataset."));
   const action=link(symbol,"View NSE:"+symbol+" directly");
   action.classList.add("provider-cta");
   card.append(main,action);
   return card;
 }
 function symbolWidget(tag,symbol,attrs={}){
   const element=document.createElement(tag);
   element.setAttribute("symbol","NSE:"+symbol);
   element.setAttribute("theme","dark");
   element.setAttribute("locale","en");
   element.setAttribute("data-expected-symbol","NSE:"+symbol);
   for(const [key,val] of Object.entries(attrs))element.setAttribute(key,String(val));
   element.style.display="block";
   element.style.width="100%";
   return element;
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
   // Unlike the legacy script's AAPL fallback, the new web component accepts
   // an explicit symbol property/attribute. No innerHTML or script injection.
   const element=symbolWidget("tv-advanced-chart",symbol,{
     interval:"1D",timezone:"Asia/Kolkata",style:"1",
     "allow-symbol-change":"false","hide-volume":"false"
   });
   element.classList.add("msk-tv-advanced-chart");
   mount.append(element,providerCard(symbol));
   const hover=$("hoverBar");
   hover.replaceChildren(text("span","NSE:"+symbol+" provider viewer · Daily candles · No MSK-derived trade signal. "));
   hover.append(link(symbol));
 }
 function clearExternal(){
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
   return true;
 }
 function choose(symbol,auto){
   const code=normalize(symbol);
   if(features.library.has(code)){
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
   const intro="NSE:"+symbol+" · Price data shown only by TradingView. MSK has no independently sourced historical OHLCV for this stock.";
   if(tab==="overview"||tab==="chart"){
     paragraph(parent,intro,"warning");
     parent.append(providerCard(symbol));
     paragraph(parent,"To calculate MSK EMA20/50, ADX14, volume ratio and chart patterns, import a dated OHLCV CSV for NSE:"+symbol+". This external widget is not an OHLC data feed.");
     return true;
   }
   if(tab==="technical"){
     paragraph(parent,"TradingView technical ratings (their own analytics, not MSK recommendations). Check the symbol and period displayed by the provider.","warning");
     const container=text("div",undefined,"provider-data-widget");
     container.id="externalTechnicalRating";
     const widget=symbolWidget("tv-technical-analysis",symbol,{"interval":"1D"});
     widget.classList.add("msk-tv-technical");
     container.append(widget);
     parent.append(container);
     parent.append(link(symbol));
     paragraph(parent,"MSK indicators and BUY/SELL confirmation are unavailable until OHLCV is imported.");
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