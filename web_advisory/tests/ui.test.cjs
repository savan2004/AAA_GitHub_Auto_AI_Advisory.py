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
 // Deterministic offline-provider default; individual live-feed tests override.
 w.fetch=async()=>({ok:false,status:503});
 for(const f of ["config.js","app.js","features.js","market_widget.js","dashboard.js","import_center.js"]){
   w.eval(fs.readFileSync(path.join(root,f),"utf8"));
 }
 return w;
}
function clickTab(w,tab){const el=w.document.querySelector('[data-tab="'+tab+'"]');assert.ok(el,"missing button "+tab);el.click();return w.document.getElementById("detailBody");}
test("website defaults to real BEL EOD request, with no invented quote before load",()=>{
 const w=build();assert.match(w.document.getElementById("dataLabel").textContent,/NSE:BEL/);
 assert.equal(w.document.getElementById("ticker").value,"BEL");
 assert.equal(w.document.getElementById("lastPrice").textContent,"No MSK quote");
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
 assert.match(w.document.querySelector("#externalNseChart").textContent,/SOURCE NOT AVAILABLE|DATA ACCESS RESTRICTED/);
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
 assert.match(doc.getElementById("dataLabel").textContent,/Historical data fetch failed safely|no imported MSK market data/);
 assert.ok(doc.querySelector("#externalNseChart a[href='https://in.tradingview.com/symbols/NSE-BEL/']"));
 for(const name of ["overview","technical","fundamental","chart","screener","breadth","pdf","ai"]){
   const detail=clickTab(w,name);
   assert.match(detail.textContent,/NSE:BEL|TradingView|unavailable|Unavailable|third-party|data|OHLCV|MSK research PDF|financial-statement CSV|Financial Statements|Upload one or more symbols|Breadth requires|No stock data loaded/,name);
   assert.doesNotMatch(detail.textContent,/DEMO01.*₹/,name);
 }
 assert.equal(doc.querySelector("#externalFundamentals"),null,
  "No unverified legacy financial widget");
 clickTab(w,"technical");
 assert.equal(doc.querySelector("#externalTechnicalRating"),null);
 assert.match(doc.getElementById("detailBody").textContent,/cannot display this NSE symbol/);
 clickTab(w,"fundamental");
 assert.match(doc.getElementById("detailBody").textContent,/Import Financial Statements \(CSV\)/);
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
 w.document.getElementById("loadSample").click(); // Demo must be opted in again; external NSE clears synthetic list.
 assert.equal(F.getExternalSymbol(),null);
 F.choose("DEMO02",false);
 assert.equal(doc.querySelector("tv-advanced-chart"),null);
 assert.equal(doc.body.classList.contains("market-external"),false);
 assert.equal(doc.getElementById("explain").hidden,false);
 w.close();
});

test("BEL TejHQ EOD response creates real historical candles and never mixes demo rows",async()=>{
 const w=build(),f=w.MSKWeb.features;
 const bars=[];
 for(let i=119;i>=0;i--){
   const date=new Date(Date.UTC(2026,9,26-i)).toISOString().slice(0,10);
   const v=350+(120-i)*.12;
   bars.push({date,open:v,high:v+2,low:v-2,close:v+.4,volume:700000+i*200});
 }
 let requested="";
 w.fetch=async(url)=>{
   requested=String(url);
   return {ok:true,json:async()=>({meta:{symbol:"BEL",exchange:"NSE",count:bars.length},data:bars})};
 };
 f.choose("BEL",false);
 await new Promise(resolve=>setTimeout(resolve,100));
 assert.match(requested,/api\.tejhq\.dev\/v1\/ohlcv\/nse\/BEL/);
 assert.equal(f.getExternalSymbol(),null);
 assert.equal(f.library.size,1,"Synthetic DEMO stocks must never mix with actual EOD");
 assert.equal(f.library.get("BEL").source,"tej_eod");
 assert.equal(w.document.getElementById("dataLabel").getAttribute("data-eod-provider"),"TejHQ");
 assert.equal(w.document.getElementById("dataLabel").getAttribute("data-market-asof"),"2026-10-26");
 assert.match(w.document.getElementById("dataLabel").textContent,/NSE END-OF-DAY.*NOT INTRADAY\/LIVE/);
 assert.equal(w.document.body.classList.contains("market-eod"),true);
 assert.equal(w.document.getElementById("chartCanvas").hidden,false);
 assert.equal(w.document.getElementById("externalNseChart"),null);
 assert.match(w.document.getElementById("lastPrice").textContent,/₹/);
 assert.match(w.document.getElementById("priceMeta").textContent,/NSE EOD.*not live/);
 assert.equal(w.document.getElementById("signal").textContent,"RESEARCH ONLY");
 assert.match(w.document.getElementById("adx").textContent,/\d/);
 assert.match(w.document.getElementById("vol").textContent,/x/);
 assert.match(f.offlineAnswer("Analyze BEL trend and volume"),/TejHQ.*NSE historical EOD/);
 w.close();
});
test("Wrong provider symbol cannot overwrite BEL with AAPL candles",async()=>{
 const w=build(),f=w.MSKWeb.features;
 w.fetch=async()=>({ok:true,json:async()=>({meta:{symbol:"AAPL",exchange:"NASDAQ"},data:[]})});
 f.choose("BEL",false);
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(f.getExternalSymbol(),"BEL");
 assert.equal(w.document.getElementById("lastPrice").textContent,"No MSK quote");
 assert.match(w.document.getElementById("dataLabel").textContent,/Provider symbol\/exchange mismatch/);
 assert.equal(w.document.body.classList.contains("market-eod"),false);
 assert.equal(w.document.querySelector("tv-advanced-chart"),null);
 w.close();
});
test("Stale NSE EOD provider history must fail closed",async()=>{
 const w=build(),f=w.MSKWeb.features;
 const bars=[];
 for(let i=0;i<75;i++){
   const date=new Date(Date.UTC(2020,0,1+i)).toISOString().slice(0,10);
   bars.push({date,open:100,high:103,low:97,close:101,volume:180000});
 }
 w.fetch=async()=>({ok:true,json:async()=>({meta:{symbol:"BEL",exchange:"NSE"},data:bars})});
 f.choose("BEL",false);
 await new Promise(resolve=>setTimeout(resolve,50));
 assert.equal(f.getExternalSymbol(),"BEL");
 assert.equal(w.document.getElementById("lastPrice").textContent,"No MSK quote");
 assert.match(w.document.getElementById("dataLabel").textContent,/stale or future dated/);
 w.close();
});

test("professional dashboard displays and routes ALL eight real research actions",()=>{
 const w=build(),d=w.document;
 assert.match(d.querySelector("h1").textContent,/Research Dashboard/);
 assert.equal(d.querySelectorAll(".nav[data-tab]").length,9);
 assert.equal(d.querySelectorAll(".action-card[data-go]").length,9);
 assert.equal(d.querySelectorAll(".watch-stock[data-stock]").length,4);
 assert.ok(d.getElementById("researchDetail"));
 assert.ok(d.getElementById("aiPanel"));
 assert.ok(d.querySelector('link[href="dashboard.css"]'));
 assert.ok(w.MSKDashboard);
 d.getElementById("loadSample").click();
 for(const tab of ["technical","fundamental","screener","pdf","chart","breadth","ai","portfolio","overview"]){
   const action=d.querySelector('.action-card[data-go="'+tab+'"]');
   action.click();
   assert.equal(w.MSKDashboard.activeTab(),tab);
   assert.equal(d.querySelector(".nav.active").dataset.tab,tab);
   assert.ok(d.getElementById("detailBody").textContent.length>12,"Inert module: "+tab);
 }
 d.querySelector('.watch-stock[data-stock="RELIANCE"]').click();
 assert.equal(d.getElementById("ticker").value,"RELIANCE");
 assert.match(d.getElementById("dataLabel").textContent,/NSE:RELIANCE/);
 assert.equal(d.getElementById("lastPrice").textContent,"No MSK quote");
 d.getElementById("mobileMenuToggle").click();
 assert.equal(d.body.classList.contains("menu-open"),true);
 assert.equal(d.getElementById("mobileMenuToggle").getAttribute("aria-expanded"),"true");
 d.getElementById("mobileScrim").click();
 assert.equal(d.body.classList.contains("menu-open"),false);
 w.close();
});
test("EOD source badge updates and stock watchlist actually loads issuer-specific provider",async()=>{
 const w=build(),d=w.document;
 const stock="TCS";
 const bars=[];for(let i=119;i>=0;i--){
   const date=new Date(Date.UTC(2026,9,26-i)).toISOString().slice(0,10),p=3000+i/5;
   bars.push({date,open:p,high:p+5,low:p-5,close:p+1,volume:10000+i*20});
 }
 w.fetch=async()=>({ok:true,json:async()=>({meta:{symbol:stock,exchange:"NSE"},data:bars})});
 d.querySelector('.watch-stock[data-stock="TCS"]').click();
 await new Promise(resolve=>setTimeout(resolve,110));
 assert.equal(d.getElementById("ticker").value,"TCS");
 assert.equal(w.MSKWeb.state.kind,"tej_eod");
 assert.equal(w.MSKWeb.features.library.size,1);
 assert.equal(d.getElementById("lastPrice").textContent.startsWith("₹"),true);
 assert.equal(d.getElementById("signal").textContent,"RESEARCH ONLY");
 assert.match(d.getElementById("feedTrustLabel").textContent,/HISTORICAL NSE EOD/);
 assert.equal(d.querySelector('.watch-stock[data-stock="TCS"]').classList.contains("selected"),true);
 w.close();
});

test("Import Center is discoverable, has 3 modes, and preview validates CSV and reports failure",async()=>{
 const w=build(),d=w.document,F=w.MSKWeb.features;
 assert.ok(d.getElementById("openImport"));
 d.getElementById("openImport").click();
 assert.equal(d.getElementById("importModal").hidden,false);
 assert.equal(d.querySelectorAll("[data-import-kind]").length,3);
 assert.equal(d.getElementById("confirmImport").disabled,true);
 w.MSKImport.readPreview("symbol,timestamp,open,high,low,close,volume\nAUDIT,2026-01-01,10,12,9,11,100");
 assert.match(d.getElementById("importRows").textContent,/AUDIT/);
 d.querySelector('[data-import-kind="fundamental"]').click();
 assert.match(d.getElementById("importKindTitle").textContent,/Financial/);
 d.querySelector('[data-import-kind="portfolio"]').click();
 assert.match(d.getElementById("importKindTitle").textContent,/Portfolio|holdings/);
 assert.throws(()=>w.MSKImport.readPreview("symbol,quantity\nAAA,5"),/Missing columns/);
 d.getElementById("closeImport").click();
 assert.equal(d.getElementById("importModal").hidden,true);
 w.close();
});
test("Private portfolio importer rejects wrong rows and does not invent prices",()=>{
 const w=build(),D=w.MSKImport;
 assert.throws(()=>D.holdings("symbol,quantity,buy_price\nBEL,-1,150"),/Invalid holdings/);
 const x=D.holdings("symbol,quantity,buy_price\nBEL,10,100\nTCS,4,3000");
 assert.equal(x.count,2);assert.equal(x.matched,0);
 assert.equal(w.MSKPortfolio.rows[0].estimated_value,null);
 assert.equal(w.MSKPortfolio.rows[0].source,"none");
 w.close();
});
test("Ask Research 3-month question discloses non-forecastable target and correct TejHQ source",async()=>{
 const w=build(),F=w.MSKWeb.features,bars=[];
 for(let i=119;i>=0;i--){
  const date=new Date(Date.UTC(2026,9,26-i)).toISOString().slice(0,10);
  const p=800+i*.4;
  bars.push({date,open:p,high:p+2,low:p-2,close:p+1,volume:10000+i});
 }
 w.fetch=async()=>({ok:true,json:async()=>({meta:{symbol:"SBIN",exchange:"NSE"},data:bars})});
 F.choose("SBIN",false);
 await new Promise(r=>setTimeout(r,120));
 const a=F.offlineAnswer("SBIN Upper Target for 3 month");
 assert.match(a,/NOT available|not available/i);
 assert.match(a,/TejHQ.*NSE historical EOD/);
 assert.match(a,/RESEARCH ONLY/);
 assert.doesNotMatch(a,/USER-UPLOADED CSV|guaranteed target/);
 w.close();
});

test("Portfolio tab is real, source-labelled and does not invent P&L",()=>{
 const w=build(),d=w.document;
 w.MSKImport.holdings("symbol,quantity,buy_price\nBEL,10,100\nRELIANCE,2,800");
 w.MSKDashboard.go("portfolio",{scroll:false});
 assert.equal(w.MSKDashboard.activeTab(),"portfolio");
 assert.match(d.getElementById("detailBody").textContent,/Holdings 2/);
 assert.match(d.getElementById("detailBody").textContent,/Not calculated|No aggregate P&L/);
 assert.match(d.querySelector(".heading-block h1").textContent,/Portfolio Health/);
 assert.equal(w.location.hash,"#portfolio");
 w.close();
});

test("CSV import button rejects unterminated quoted fields before enabling Import",()=>{
 const w=build(),d=w.document;
 d.getElementById("openImport").click();
 assert.equal(d.getElementById("importModal").hidden,false);
 assert.throws(
  ()=>w.MSKImport.readPreview('symbol,timestamp,open,high,low,close,volume\n"BEL,2026-01-01,10,12,9,11,100'),
  /unclosed quoted field/i
 );
 assert.equal(d.getElementById("confirmImport").disabled,true);
 d.getElementById("closeImport").click();
 assert.equal(d.getElementById("importModal").hidden,true);
 w.close();
});
