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
 for(const f of ["config.js","app.js","features.js"]){
   w.eval(fs.readFileSync(path.join(root,f),"utf8"));
 }
 return w;
}
function clickTab(w,tab){const el=w.document.querySelector('[data-tab="'+tab+'"]');assert.ok(el,"missing button "+tab);el.click();return w.document.getElementById("detailBody");}
test("no keys or fabricated prices appear when website first opens",()=>{
 const w=build();assert.match(w.document.getElementById("lastPrice").textContent,/Unavailable/);
 assert.match(w.document.getElementById("backendIndicator").textContent,/offline/);
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
 assert.equal(w.document.getElementById("ticker").value,"RELIANCE");
 assert.equal(F.choose("INFY",false),true);
 assert.equal(w.document.getElementById("ticker").value,"INFY");
 assert.match(w.document.getElementById("chartTitle").textContent,/INFY/);
 assert.equal(F.choose("UNKNOWN",false),false);
 assert.match(w.document.getElementById("dataLabel").textContent,/no loaded observations/);
 w.close();
});
test("financial statement button consumes source-dated values and does not invent ratios",()=>{
 const w=build();const F=w.MSKWeb.features;
 w.document.getElementById("loadSample").click();
 clickTab(w,"fundamental");
 assert.match(w.document.getElementById("detailBody").textContent,/No financial statements imported/);
 F.importFundamentals("symbol,asof,source,pe,roe_percent,revenue_growth_percent,market_cap_inr,debt_equity_ratio\nRELIANCE,2026-09-30,User-uploaded filing,21.2,11.3,7.5,3000000000000,0.48");
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
