"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {JSDOM}=require("jsdom");
const fs=require("node:fs");
const path=require("node:path");
const root=path.join(__dirname,"..");
test("uploaded and synthetic candles never display BUY or SELL recommendations",()=>{
 const html=fs.readFileSync(path.join(root,"index.html"),"utf8");
 const dom=new JSDOM(html,{url:"https://example.test/",runScripts:"outside-only",pretendToBeVisual:true});
 const w=dom.window;
 const canvas={setTransform(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){},setLineDash(){}};
 w.HTMLCanvasElement.prototype.getContext=()=>canvas;
 Object.defineProperty(w.HTMLCanvasElement.prototype,"clientWidth",{get(){return 920}});
 Object.defineProperty(w.HTMLCanvasElement.prototype,"clientHeight",{get(){return 390}});
 w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({left:0,top:0,width:920,height:390});
 w.print=()=>{};
 w.URL.createObjectURL=()=>"blob:https://example.test/file";
 w.URL.revokeObjectURL=()=>{};
 w.fetch=async()=>({ok:false,status:503});
 for(const file of ["config.js","app.js","features.js","market_widget.js","dashboard.js","import_center.js"]){
  w.eval(fs.readFileSync(path.join(root,file),"utf8"));
 }
 w.document.getElementById("loadSample").click();
 assert.equal(w.document.getElementById("signal").textContent,"RESEARCH ONLY");
 const lines=["symbol,timestamp,open,high,low,close,volume"];
 for(let i=0;i<70;i++){
  const date=new Date(Date.UTC(2026,0,i+1)).toISOString().slice(0,10);
  const close=100+i;
  lines.push(["ABC",date,close-1,close+2,close-2,close,100000+i*500].join(","));
 }
 w.MSKWeb.features.importBars(lines.join("\n"),"research.csv");
 assert.equal(w.document.getElementById("signal").textContent,"RESEARCH ONLY");
 assert.doesNotMatch(w.document.getElementById("analysisLines").textContent,/\bBUY\b|\bSELL\b/);
 w.close();
});
