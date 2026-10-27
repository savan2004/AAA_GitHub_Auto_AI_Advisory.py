/* Real DOM-level click-through regression for the public free MSK website.
   Uses jsdom; stubs only canvas pixels and browser print/download APIs. */
const {JSDOM}=require("jsdom");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {test}=require("node:test");
const root=path.join(__dirname,"..");
function build(){
 const html=fs.readFileSync(path.join(root,"index.html"),"utf8");
 const dom=new JSDOM(html,{url:"https://msk-web-advisory-preview.onrender.com/",runScripts:"outside-only",pretendToBeVisual:true});
 const w=dom.window;
 const drawing={setTransform(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){},setLineDash(){}};
 w.HTMLCanvasElement.prototype.getContext=function(){return drawing};
 w.HTMLCanvasElement.prototype.getBoundingClientRect=function(){return {left:0,top:0,width:920,height:390}};
 Object.defineProperty(w.HTMLCanvasElement.prototype,"clientWidth",{get(){return 920}});
 Object.defineProperty(w.HTMLCanvasElement.prototype,"clientHeight",{get(){return 390}});
 w.print=()=>{};
 w.alert=(msg)=>{throw Error("Unexpected browser alert: "+msg)};
 w.URL.createObjectURL=()=> "blob:https://test/abc";
 w.URL.revokeObjectURL=()=>{};
 for(const f of ["config.js","app.js","features.js","market_widget.js"]){
   w.eval(fs.readFileSync(path.join(root,f),"utf8"));
 }
 return w;
}
function clickTab(w,tab){const el=w.document.querySelector('[data-tab="'+tab+'"]');assert.ok(el,"missing button "+tab);el.click();return w.document.getElementById("detailBody");}
test("website opens with prominently labelled fictional demo, not a fake live quote",()=>{
 const w=build();assert.match(w.document.getElementById("dataLabel").textContent,/SYNTHETIC/);
 assert.equal(w.document.getElementById("ticker").value,"DEMO01");
 assert.match(w.document.getElementById("backendIndicator").textContent,/Checking research API|offline|Neon API/);
 assert.ok(w.MSKWeb.features);w.close();
});
test("all menu buttons respond after ten-stock synthetic demo",()=>{
 const w=build();w.document.getElementById("loadSample").click();
 const F=w.MSKWeb.features;
 assert.equal(F.library.size,10);
 assert.equal(w.document.getElementById("symbolCount").textContent,"10 SYMBOLS");
 assert.match(w.document.getElementById("dataLabel").textContent,/SYNTHETIC/);
 assert.match(w.document.getElementById("lastPrice").textContent,/₹/);
 assert.ok(Number(w.document.getElementById("chartCanvas").width)>0);
 for(const label of ["overview","technical","fundamental","chart","screener","breadth","pdf","ai"]){
    const panel=clickTab(w,label);
    assert.ok(panel.textContent.length>25,"empty tab "+label);
 }
 let pane=clickTab(w,"breadth");
 assert.match(pane.textContent,/Advances/);
 assert.match(pane.textContent,/Declines/);
 assert.match(pane.textContent,/Coverage/);
 pane=clickTab(w,"screener");
 assert.equal(pane.querySelectorAll("tbody tr").length,10);
 const sel=pane.querySelector("#trendFilter");
 sel.value="Uptrend";sel.dispatchEvent(new w.Event("change",{bubbles:true}));
 assert.ok(pane.querySelectorAll("tbody tr").length<=10);
 pane=clickTab(w,"pdf");
 assert.match(pane.textContent,/Print/);
 assert.equal(w.document.getElementById("ticker").value,"DEMO01");
 assert.equal(F.choose("DEMO03",false),true);
 assert.equal(w.document.getElementById("ticker").value,"DEMO03");
 assert.match(w.document.getElementById("chartTitle").textContent,/DEMO03/);
 assert.equal(F.choose("BEL",false),true);
 assert.equal(F.getExternalSymbol(),"BEL");
 assert.match(w.document.getElementById("dataLabel").textContent,/NSE:BEL/);
 assert.equal(w.document.getElementById("lastPrice").textContent,"No MSK quote");
 assert.equal(w.document.getElementById("signal").textContent,"NOT ASSESSED");
 assert.equal(w.document.body.classList.contains("market-external"),true);
 assert.equal(w.document.getElementById("chartCanvas").hidden,true);
 assert.equal(w.document.querySelector("#externalNseChart tv-advanced-chart"),null);
 assert.equal(w.document.querySelector("#externalNseChart script"),null);
 assert.match(w.document.querySelector("#externalNseChart").textContent,/DATA ACCESS RESTRICTED/);
 assert.match(w.document.querySelector("#externalNseChart").textContent,/NSE:BEL/);
 assert.doesNotMatch(w.document.getElementById("analysisLines").textContent,/DEMO03/);
 w.document.getElementById("loadSample").click();
 assert.equal(F.getExternalSymbol(),null);
 assert.equal(w.document.getElementById("chartCanvas").hidden,false);
 assert.equal(w.document.getElementById("ticker").value,"DEMO01");
 assert.equal(w.document.getElementById("externalNseChart"),null);
 w.close();
});
test("financial statement button consumes source-dated values and does not invent ratios",()=>{
 const w=build();const F=w.MSKWeb.features;
 w.document.getElementById("loadSample").click();
 clickTab(w,"fundamental");
 assert.match(w.document.getElementById("detailBody").textContent,/No financial statements imported/);
 F.importFundamentals("symbol,asof,source,pe,roe_percent,revenue_growth_percent,market_cap_inr,debt_equity_ratio\nDEMO01,2026-09-30,User-uploaded filing,21.2,11.3,7.5,3000000000000,0.48");
 const pane=clickTab(w,"fundamental");
 assert.match(pane.textContent,/21.20/);
 assert.match(pane.textContent,/User-uploaded filing/);
 assert.match(pane.textContent,/2026-09-30/);
 assert.match(F.offlineAnswer("Explain ROE and valuation"),/21.20|ROE/);
 w.close();
});
test("real CSV import supports many symbols, user symbols and legitimate price candles",()=>{
 const w=build();const F=w.MSKWeb.features;const lines=["symbol,timestamp,open,high,low,close,volume"];
 for(const name of ["ABC","XYZ"]){for(let i=0;i<80;i++){
  const d=new Date(Date.UTC(2026,0,1+i)).toISOString().slice(0,10);
  const base=name==="ABC"?100:250,c=base+i*.5;
  lines.push(name+","+d+","+(c-1)+","+(c+3)+","+(c-3)+","+c+","+(15000+i));
 }}
 F.importBars(lines.join("\n"),"user-candles.csv");
 assert.equal(F.library.size,2);
 assert.equal(F.choose("XYZ",false),true);
 assert.match(w.document.getElementById("chartTitle").textContent,/XYZ/);
 let pan=clickTab(w,"breadth");assert.match(pan.textContent,/2 uploaded/);
 assert.match(w.document.getElementById("dataLabel").textContent,/USER CSV|UNVERIFIED/);
 w.close();
});
test("ask AI never fakes generative replies; answers are local and research-bound",()=>{
 const w=build();
 w.document.getElementById("loadSample").click();
 clickTab(w,"ai");
 const input=w.document.getElementById("chatInput");
 input.value="Explain market trend and volume";w.document.getElementById("chatForm").dispatchEvent(new w.Event("submit",{cancelable:true,bubbles:true}));
 const txt=w.document.getElementById("chatHistory").textContent;
 assert.match(txt,/LOCAL RULE-BASED RESEARCH/);
 assert.match(txt,/SIMULATED|SYNTHETIC/);
 assert.match(txt,/NOT GENERATIVE AI/);
 assert.doesNotMatch(txt,/Sure shot buy|Guaranteed/);
 w.close();
});

test("BEL search button and research tabs refuse restricted embed without demo prices",()=>{
 const w=build(),doc=w.document;
 const input=doc.getElementById("ticker");
 input.value="BEL";doc.getElementById("analyzeSymbol").click();
 assert.equal(w.MSKWeb.features.getExternalSymbol(),"BEL");
 assert.match(doc.getElementById("chartTitle").textContent,/NSE:BEL/);
 assert.equal(doc.body.classList.contains("market-external"),true);
 assert.equal(doc.getElementById("signal").textContent,"NOT ASSESSED");
 assert.equal(doc.getElementById("lastPrice").textContent,"No MSK quote");
 assert.equal(doc.getElementById("adx").textContent,"—");
 assert.equal(doc.getElementById("vol").textContent,"—");
 assert.match(doc.getElementById("dataLabel").textContent,/no imported MSK market data/);
 assert.ok(doc.querySelector("#externalNseChart a[href='https://in.tradingview.com/symbols/NSE-BEL/']"));
 for(const name of ["overview","technical","fundamental","chart","screener","breadth","pdf","ai"]){
   const detail=clickTab(w,name);
   assert.match(detail.textContent,/NSE:BEL|TradingView|unavailable|Unavailable|third-party|data|OHLCV|MSK research PDF/,name);
   assert.doesNotMatch(detail.textContent,/DEMO01.*₹/,name);
 }
 assert.equal(doc.querySelector("#externalFundamentals"),null,
  "No unverified legacy financial widget");
 clickTab(w,"technical");
 assert.equal(doc.querySelector("#externalTechnicalRating"),null);
 assert.match(doc.getElementById("detailBody").textContent,/cannot display this NSE symbol/);
 clickTab(w,"fundamental");
 assert.match(doc.getElementById("detailBody").textContent,/Check official financial disclosures/);
 assert.equal(doc.querySelector("#externalFundamentals"),null);
 assert.match(w.MSKWeb.features.offlineAnswer("Analyze BEL with buy target"),/no licensed OHLCV or filings/);
 input.value="INVALID$TICKER";doc.getElementById("analyzeSymbol").click();
 assert.equal(w.MSKWeb.features.getExternalSymbol(),"BEL","invalid text must not select arbitrary untrusted symbols");
 w.close();
});
test("Real CSV import replaces external BEL viewer and restores accurate indicators",()=>{
 const w=build(),f=w.MSKWeb.features;
 f.choose("BEL",false);assert.equal(f.getExternalSymbol(),"BEL");
 const lines=["symbol,timestamp,open,high,low,close,volume"];
 for(let i=0;i<66;i++){const d=new Date(Date.UTC(2026,0,i+1)).toISOString().slice(0,10),p=150+i*.25;
   lines.push(["BEL",d,p,p+2,p-2,p+1,12000+i].join(","));
 }
 f.importBars(lines.join("\n"),"BEL-unverified-history.csv");
 assert.equal(f.getExternalSymbol(),null);
 assert.equal(w.document.getElementById("chartCanvas").hidden,false);
 assert.equal(w.document.getElementById("externalNseChart"),null);
 assert.equal(w.document.body.classList.contains("market-external"),false);
 assert.match(w.document.getElementById("chartTitle").textContent,/BEL/);
 assert.match(w.document.getElementById("priceMeta").textContent,/user CSV/);
 assert.notEqual(w.document.getElementById("adx").textContent,"—");
 w.close();
});

test("restricted NSE symbols show honest stock-specific source links, never default Apple widgets",()=>{
 const w=build(),doc=w.document,F=w.MSKWeb.features;
 F.choose("BEL",false);
 let widget=doc.querySelector("tv-advanced-chart");
 assert.equal(widget,null);
 assert.equal(doc.querySelector(".nse-unavailable")?.getAttribute("data-symbol"),"NSE:BEL");
 assert.equal(doc.querySelector("script[src*='embed-widget-advanced-chart.js']"),null);
 assert.equal(doc.querySelector("script[src*='embed-widget-financials.js']"),null);
 assert.ok(doc.body.classList.contains("market-external"));
 assert.ok(doc.getElementById("explain").hidden);
 F.choose("INFY",false);
 widget=doc.querySelector("tv-advanced-chart");
 assert.equal(widget,null);
 assert.equal(doc.querySelector(".nse-unavailable")?.getAttribute("data-symbol"),"NSE:INFY");
 assert.equal(doc.querySelectorAll("tv-advanced-chart").length,0);
 assert.doesNotMatch(doc.getElementById("dataLabel").textContent,/NSE:BEL/);
 const panel=clickTab(w,"technical");
 assert.equal(panel.querySelector("tv-technical-analysis"),null);
 assert.equal(doc.querySelector(".nse-unavailable")?.getAttribute("data-symbol"),"NSE:INFY");
 F.choose("DEMO02",false);
 assert.equal(F.getExternalSymbol(),null);
 assert.equal(doc.querySelector("tv-advanced-chart"),null);
 assert.equal(doc.body.classList.contains("market-external"),false);
 assert.equal(doc.getElementById("explain").hidden,false);
 w.close();
});
