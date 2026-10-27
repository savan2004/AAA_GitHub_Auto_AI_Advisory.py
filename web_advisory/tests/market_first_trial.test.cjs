"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {JSDOM}=require("jsdom");
const root=path.join(__dirname,"..");

function setup(){
  const html=fs.readFileSync(path.join(root,"index.html"),"utf8");
  const dom=new JSDOM(html,{url:"https://msk-web-advisory-preview.onrender.com/",runScripts:"outside-only",pretendToBeVisual:true});
  const w=dom.window;
  w.HTMLCanvasElement.prototype.getContext=()=>({
    setTransform(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){},setLineDash(){}
  });
  Object.defineProperty(w.HTMLCanvasElement.prototype,"clientWidth",{get(){return 920}});
  Object.defineProperty(w.HTMLCanvasElement.prototype,"clientHeight",{get(){return 390}});
  w.URL.createObjectURL=()=>"blob:https://test.local/test";
  w.URL.revokeObjectURL=()=>{};
  w.print=()=>{};
  w.fetch=async()=>({ok:false,status:503});
  for(const f of ["config.js","app.js","features.js","market_widget.js","dashboard.js","import_center.js","market_first.js","company_journey.js"]){
    w.eval(fs.readFileSync(path.join(root,f),"utf8"));
  }
  return w;
}
function prices(symbol,count=80){
 const arr=["symbol,timestamp,open,high,low,close,volume"];
 for(let i=0;i<count;i++){
   const date=new Date(Date.UTC(2026,4,1+i)).toISOString().slice(0,10);
   const p=100+i*.7;
   arr.push([symbol,date,p,p+4,p-3,p+1,30000+i*50].join(","));
 }
 return arr.join("\n");
}
test("market overview precedes search and clearly disclaims missing feed",()=>{
 const w=setup();try{
   const overview=w.document.getElementById("marketFirst");
   const search=w.document.querySelector(".research-search");
   const company=w.document.getElementById("companyJourney");
   assert.ok(overview&&search&&company);
   assert.equal(overview.nextElementSibling,search);
   assert.ok(overview.compareDocumentPosition(company)&w.Node.DOCUMENT_POSITION_FOLLOWING);
   assert.match(overview.textContent,/NIFTY 50|BANK NIFTY/);
   assert.match(overview.textContent,/LIVE QUOTES NOT CONNECTED/);
   assert.match(overview.textContent,/RBI|SEBI/);
   assert.equal(w.document.getElementById("trialAdvances").textContent,"—");
 }finally{w.close();}
});
test("synthetic breadth is never sold as verified exchange breadth",()=>{
 const w=setup();try{
   w.document.getElementById("loadSample").click();
   assert.match(w.document.getElementById("trialBreadthStatus").textContent,/SYNTHETIC DEMO/);
   assert.match(w.document.getElementById("trialBreadthStatus").textContent,/Not NIFTY or BANK NIFTY breadth/);
   assert.equal(w.document.getElementById("journeyPrice").textContent,"Unavailable");
   w.document.getElementById("trialOpenBreadth").click();
   assert.equal(w.MSKWeb.state.tab,"breadth");
 }finally{w.close();}
});
test("CSV selected symbol and stale-price separation",()=>{
 const w=setup();try{
   const F=w.MSKWeb.features;
   F.importBars(prices("TESTA"),"test-data.csv");
   assert.equal(w.document.getElementById("journeySymbol").textContent,"TESTA");
   assert.match(w.document.getElementById("journeyPrice").textContent,/₹/);
   assert.match(w.document.getElementById("journeySource").textContent,/USER CSV/);
   const oldDate=w.document.getElementById("journeyDate").textContent;
   assert.match(oldDate,/2026-/);
   const ticker=w.document.getElementById("ticker");
   ticker.value="SBIN";
   ticker.dispatchEvent(new w.Event("input",{bubbles:true}));
   assert.equal(w.document.getElementById("journeyPrice").textContent,"Unavailable");
   assert.equal(w.document.getElementById("journeyDate").textContent,"Unavailable");
   assert.equal(w.document.getElementById("journeySymbol").textContent,"SBIN");
   // Stock analysis buttons still switch to the requested research module.
   w.document.querySelector('[data-journey="fundamental"]').click();
   assert.equal(w.MSKWeb.state.tab,"fundamental");
 }finally{w.close();}
});
test("single-symbol upload clears obsolete synthetic breadth",()=>{
 const w=setup();try{
   w.document.getElementById("loadSample").click();
   assert.notEqual(w.document.getElementById("trialAdvances").textContent,"—");
   w.MSKWeb.features.importBars(prices("TESTA"),"test-data.csv");
   assert.equal(w.document.getElementById("trialAdvances").textContent,"—");
   assert.equal(w.document.getElementById("trialDeclines").textContent,"—");
   assert.match(w.document.getElementById("trialBreadthStatus").textContent,/unavailable/);
 }finally{w.close();}
});
