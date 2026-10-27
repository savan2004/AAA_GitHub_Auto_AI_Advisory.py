"use strict";
/* Provider-hosted NSE chart; does NOT fetch, copy, scrape or calculate from exchange data.
   TradingView embeds retain their branding and may be delayed or unavailable.
   The MSK quant engine only runs on explicitly imported OHLCV, never a demo
   of another ticker or data scraped from third-party iframe.
*/
(() => {
 const app=window.MSKWeb;
 if(!app?.features)throw Error("Load app.js and features.js before market_widget.js");
 const $=id=>document.getElementById(id);
 const features=app.features;
 const oldChoose=features.choose;
 const oldRender=features.renderPanel;
 const oldAnswer=features.offlineAnswer;
 let selected=null;
 const getSymbol=x=>String(x||"").toUpperCase().trim().replace(/\.NS$/,"");
 const valid=x=>/^[A-Z][A-Z0-9&._-]{0,20}$/.test(x) && !/^DEMO\d+/.test(x);
 const path=x=>"https://in.tradingview.com/symbols/NSE-"+encodeURIComponent(x)+"/";
 const el=(tag,txt,className)=>{const x=document.createElement(tag);if(txt!==undefined)x.textContent=txt;if(className)x.className=className;return x;};
 function realLink(symbol,extension=""){
   const a=el("a","Open "+symbol+" on TradingView ↗","tv-source-link");
   a.href=path(symbol)+extension;a.target="_blank";a.rel="noopener noreferrer nofollow";return a;
 }
 function embed(host,widget,options,symbol,height){
   host.replaceChildren();
   const outer=el("div",undefined,"tradingview-widget-container");
   outer.style.width="100%";outer.style.height=height+"px";
   const holder=el("div",undefined,"tradingview-widget-container__widget");
   holder.style.width="100%";holder.style.height="calc(100% - 26px)";
   outer.append(holder);
   const footer=el("div",undefined,"tv-attribution");
   footer.append(realLink(symbol));
   const by=el("span"," • Data and charts by TradingView • Feed may be delayed");
   footer.append(by);outer.append(footer);
   host.append(outer);
   // TradingView's documented HTML embed requires a real script element.
   // Inserting a <script> via innerHTML would not execute. This script
   // config is constructed ONLY from a validated, uppercase NSE symbol.
   const script=document.createElement("script");
   script.type="text/javascript";script.async=true;
   script.src="https://s3.tradingview.com/external-embedding/"+widget;
   script.textContent=JSON.stringify(options);
   outer.append(script);
 }
 function chart(symbol){
   const wrap=$("chartCanvas")?.parentElement;
   if(!wrap)return;
   $("chartCanvas").hidden=true;
   $("chartEmpty").hidden=true;
   $("chartEmpty").style.display="none";
   wrap.style.minHeight="548px";
   let mount=$("externalNseChart");
   if(!mount){mount=el("div",undefined,"external-nse-chart");mount.id="externalNseChart";wrap.append(mount);}
   embed(mount,"embed-widget-advanced-chart.js",{
     autosize:true,symbol:"NSE:"+symbol,interval:"D",timezone:"Asia/Kolkata",
     theme:"dark",style:"1",locale:"en",backgroundColor:"#0b1424",
     gridColor:"rgba(46,60,79,0.38)",allow_symbol_change:false,hide_top_toolbar:false,
     hide_side_toolbar:true,hide_legend:false,hide_volume:false,details:false,
     withdateranges:true,save_image:true,calendar:false,studies:[],
   },symbol,548);
   $("hoverBar").replaceChildren(
     el("span","Provider-hosted NSE chart. Quote freshness and exchange delays are shown by TradingView; our indicators are unavailable without imported OHLCV. ")
   );
   $("hoverBar").append(realLink(symbol));
 }
 function reset(){
   const mount=$("externalNseChart");
   if(mount)mount.remove();
   const wrap=$("chartCanvas")?.parentElement;
   if(wrap)wrap.style.minHeight="";
   $("chartCanvas").hidden=false;
 }
 function external(symbol){
   if(!valid(symbol))return false;
   selected=symbol;
   $("ticker").value=symbol;
   // Critical: clear prior demo/import candles, metrics, old markers and analysis.
   app.state.data=[];app.state.calc=null;app.state.kind="external";
   app.state.file="";
   app.state.tab=app.state.tab||"overview";
   for(const [id,value] of Object.entries({
     lastPrice:"Unavailable",priceMeta:"View third-party NSE widget — not verified MSK quote",
     trend:"Not calculated",adx:"—",adxLabel:"Requires dated OHLCV",
     vol:"—",volLabel:"Requires previous 20 candles",
     chartTitle:"NSE:"+symbol+" • External Candlestick Chart",
     analysisTitle:symbol+" • Data Source Status",signal:"WAIT",
     analysisNote:"External TradingView chart is for viewing only. No OHLCV has been licensed/imported into MSK; no BUY/SELL/ADX/Volume result can be derived here."
   }))$(id).textContent=value;
   const lines=$("analysisLines");lines.replaceChildren();
   for(const [a,b] of [
       ["Stock","NSE:"+symbol],["Pattern","Not calculated"],
       ["ADX","Not available"],["Volume","Not available"],
       ["Signal","WAIT — no MSK data"],["Quality","Unverified"]]){
     const d=el("div"),t=el("span",a),v=el("b",b);d.append(t,v);lines.append(d);
   }
   $("dataLabel").textContent="NSE:"+symbol+" • TradingView embedded third-party chart. NO licensed MSK OHLCV feed; no invented price or trading signal.";
   $("dataLabel").classList.add("hasData");
   $("dataLabel").style.borderColor="#5dd1e4";
   chart(symbol);
   app.renderTab();
   return true;
 }
 function choose(symbol,auto){
   const s=getSymbol(symbol);
   if(features.library.has(s)){
     selected=null;reset();
     return oldChoose(s,auto);
   }
   if(auto && !s && features.library.size){selected=null;reset();return oldChoose(s,true);}
   if(!valid(s)){
     $("dataLabel").textContent="Invalid NSE symbol. Enter a ticker such as BEL, INFY or TCS (letters and numbers only).";
     return false;
   }
   return external(s);
 }
 function panel(tab,container){
   if(!selected)return oldRender(tab,container);
   if(tab==="ai"){
     container.append(el("p","Ask educational questions here. MSK has no historical OHLCV for NSE:"+selected+", so numerical market analysis and BUY/SELL advice are unavailable until you import that symbol's sourced data.","warning"));
     container.append(el("p","Third-party chart data is not passed to our AI engine."));
     container.append(realLink(selected));return true;
   }
   if(tab==="technical"||tab==="overview"||tab==="chart"){
     container.append(el("p","NSE:"+selected+" • Live/delayed chart hosted by TradingView. The MSK indicators, ADX14, volume multiple and BUY/SELL signals are intentionally unavailable without imported OHLCV.","warning"));
     container.append(realLink(selected));
     if(tab==="technical"){
       const widget=el("div");widget.id="externalTechnicalRating";widget.className="provider-data-widget";container.append(widget);
       embed(widget,"embed-widget-technical-analysis.js",{
         interval:"1D",width:"100%",isTransparent:false,height:440,
         symbol:"NSE:"+selected,showIntervalTabs:true,displayMode:"single",
         locale:"en",colorTheme:"dark"
       },selected,455);
       container.append(el("p","TradingView's technical rating is its own calculation, not an MSK Buy/Sell recommendation."));
     }
     container.append(el("p","To calculate MSK EMA20/50, ADX14, breakout and relative volume: import ≥50 daily OHLCV candles for this symbol. This widget does not supply downloadable calculation data."));
     return true;
   }
   if(tab==="fundamental"){
     container.append(el("p","Provider-hosted fundamental figures, subject to TradingView coverage and data freshness. These are not extracted into MSK or independently audited.","warning"));
     const mount=el("div");mount.id="externalFundamentals";mount.className="provider-data-widget";container.append(mount);
     embed(mount,"embed-widget-financials.js",{
       isTransparent:false,largeChartUrl:"",displayMode:"regular",
       width:"100%",height:570,colorTheme:"dark",symbol:"NSE:"+selected,locale:"en"
     },selected,585);
     container.append(realLink(selected,"financials-overview/"));return true;
   }
   if(tab==="screener"||tab==="breadth"){
     container.append(el("p","Selected: NSE:"+selected+". A single external stock widget cannot supply full-universe screener or NSE market breadth. Import sourced multi-symbol CSV to run these calculations.","warning"));
     container.append(el("p","Never combine DEMO01–DEMO10 synthetic statistics with real NSE:"+selected+" data."));
     return true;
   }
   if(tab==="pdf"){
     container.append(el("p","Research PDF with MSK computed indicators is unavailable without actual OHLCV. Print this provider-hosted reference page if useful; embedded iframe may not print fully.","warning"));
     container.append(realLink(selected));return true;
   }
   return false;
 }
 function offlineAnswer(q){
   if(!selected)return oldAnswer(q);
   const lower=String(q||"").toLowerCase();
   if(/what is|meaning|explain|define|क्या है|શું છે/.test(lower)){
     if(/\badx\b/.test(lower))return "ADX indicates trend strength; its value for NSE:"+selected+" is unavailable in the MSK dataset. The TradingView chart may offer a third-party indicator. Local education only — not generative AI.";
     if(/\broe\b/.test(lower))return "ROE compares profits with shareholders' equity. Actual ROE for NSE:"+selected+" needs a dated financial statement; look under the third-party Fundamentals panel. Local education only.";
   }
   return "NSE:"+selected+" is selected. Its third-party TradingView chart can be viewed on the website, but MSK does not have validated historical OHLCV/filings for this symbol. Price, pattern, ADX, targets and recommendations are not calculated. Import a dated CSV to enable MSK research. This is local rule-based guidance, not generative AI.";
 }
 features.choose=choose;
 features.renderPanel=panel;
 features.offlineAnswer=offlineAnswer;
 features.getExternalSymbol=()=>selected;
 app.marketWidget={clearExternal:()=>{selected=null;reset();},showExternal:external};
 const input=$("ticker"),action=$("analyzeSymbol");
 // Capture before the older demo-only listeners to prevent the stale
 // "no loaded observations" error from overwriting the new result.
 action.addEventListener("click",event=>{event.stopImmediatePropagation();choose(input.value,false);},true);
 input.addEventListener("keydown",event=>{
   if(event.key==="Enter"){event.preventDefault();event.stopImmediatePropagation();choose(input.value,false);}
 },true);
 input.addEventListener("change",event=>{
   event.stopImmediatePropagation();
   const s=getSymbol(input.value);
   if(s&&s!==selected)choose(s,false);
 },true);
 // Restore DEMO canvas when user explicitly requests synthetic demonstration.
 $("loadSample").addEventListener("click",()=>{selected=null;reset();},true);
 const params=new URL(window.location.href).searchParams;
 if(params.has("symbol")){
   const s=getSymbol(params.get("symbol"));
   if(valid(s))choose(s,false);
 }
})();
