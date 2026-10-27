/* AUDIT-ONLY against currently LIVE Render MSK Web. Non-destructive:
 * uploads synthetic LOCAL browser CSV bytes; never changes server or DB.
 * Tests actual click/upload and record separate status per feature.
 */
const {chromium}=require("playwright-core");
const fs=require("node:fs");
const path=require("node:path");
const results=[];
const base="https://msk-web-advisory-preview.onrender.com/";
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const emit=(id,section,status,details)=>{
 const row={id,section,status,details:String(details).slice(0,680)};
 results.push(row); console.log("MSK_AUDIT_RESULT "+JSON.stringify(row));
};
const ok=(condition,detail)=>({success:!!condition,detail});
const err=e=>String(e?.message||e).slice(0,500);
function csvPrices(){
 const a=[["symbol","timestamp","open","high","low","close","volume"]];
 for(const symbol of ["AUDITAAA","AUDITBBB"]){
   for(let i=0;i<88;i++){
     const date=new Date(Date.UTC(2026,4,1+i)).toISOString().slice(0,10);
     const p=(symbol==="AUDITAAA"?125:360)+i*.38;
     a.push([symbol,date,p-1,p+3,p-3,p+1,12000+i*13]);
   }
 }
 return a.map(x=>x.join(",")).join("\n");
}
function csvFinance(){
 return "symbol,asof,source,pe,roe_percent,revenue_growth_percent,market_cap_inr,debt_equity_ratio\n"+
  "AUDITAAA,2026-09-30,AUDIT SYNTHETIC LOCAL CSV ONLY,18.75,14.30,8.10,91000000000,0.32\n";
}
async function step(id,section,action){
 try{const out=await action();emit(id,section,out?.success?"PASS":"FAIL",out?.detail||"");}
 catch(e){emit(id,section,"FAIL",err(e));}
}
(async()=>{
 const bin=process.env.CHROME_BIN||"/usr/bin/google-chrome";
 const browser=await chromium.launch({headless:true,executablePath:bin,args:["--no-sandbox","--disable-dev-shm-usage"]});
 const page=await browser.newPage({viewport:{width:1440,height:900},acceptDownloads:true});
 const pageErrors=[];const alerts=[];
 page.on("pageerror",e=>pageErrors.push(err(e)));
 page.on("dialog",async d=>{alerts.push(d.message());await d.dismiss()});
 let successful=false;
 try{
  await step("site-live","Core",async()=>{
    const r=await page.goto(base,{waitUntil:"domcontentloaded",timeout:25000});
    return ok(r?.status()===200,"HTTP "+r?.status()+"; title "+await page.title());
  });
  await step("dashboard","Dashboard",async()=>{
    const n=await page.locator(".action-card[data-go]").count();
    return ok(n===8,"Dashboard action cards "+n+"; heading "+await page.locator("h1").first().innerText());
  });
  await step("search-bel-eod","Research",async()=>{
    await page.waitForFunction(()=>document.body.classList.contains("market-eod"),{timeout:25000}).catch(()=>{});
    const evidence=await page.evaluate(()=>({symbol:document.getElementById("ticker").value,kind:window.MSKWeb?.state.kind,source:document.getElementById("dataLabel")?.textContent,metric:document.getElementById("lastPrice")?.textContent,session:document.getElementById("dataLabel")?.dataset.marketAsof}));
    return ok(evidence.kind==="tej_eod"&&evidence.symbol==="BEL",JSON.stringify(evidence));
  });
  for(const tab of ["overview","technical","fundamental","chart","screener","breadth","pdf","ai"]){
    await step("nav-"+tab,"Navigation",async()=>{
      const b=page.locator('.nav[data-tab="'+tab+'"]');
      await b.click({timeout:8000});
      const q=await page.evaluate(()=>({active:window.MSKWeb?.state.tab,page:document.body.dataset.page||"",heading:document.querySelector(".heading-block h1")?.textContent,detail:document.getElementById("detailBody")?.textContent.slice(0,310),hash:window.location.hash}));
      return ok(q.active===tab&&q.detail.length>14,"state "+JSON.stringify(q));
    });
    await step("card-"+tab,"Action Card",async()=>{
      await page.locator('.action-card[data-go="'+tab+'"]').click({timeout:8000});
      const q=await page.evaluate(()=>({active:window.MSKWeb?.state.tab,heading:document.querySelector(".heading-block h1")?.textContent,detail:document.getElementById("detailBody")?.textContent.slice(0,190),hash:window.location.hash}));
      return ok(q.active===tab&&q.detail.length>14,JSON.stringify(q));
    });
  }
  await step("deeplink-fundamental","Separate Workspace",async()=>{
    await page.goto(base+"?symbol=BEL#fundamental",{waitUntil:"domcontentloaded",timeout:25000});
    await sleep(1800);
    const m=await page.evaluate(()=>({hash:location.hash,active:window.MSKWeb?.state.tab,page:document.body.dataset.page||"",h1:document.querySelector(".heading-block h1").textContent}));
    return ok(m.active==="fundamental"&&m.page==="fundamental",JSON.stringify(m));
  });
  await step("upload-price-csv","CSV Upload",async()=>{
    await page.locator("#csvFile").setInputFiles({name:"MSK-AUDIT-FAKE-PRICES.csv",mimeType:"text/csv",buffer:Buffer.from(csvPrices())});
    await sleep(550);
    const r=await page.evaluate(()=>({kind:window.MSKWeb?.state.kind,symbol:window.MSKWeb?.features?.getResearch()?.symbol,keys:[...window.MSKWeb?.features?.library?.keys()||[]],price:document.getElementById("lastPrice")?.textContent,alert:document.getElementById("dataLabel")?.textContent}));
    return ok(r.kind==="uploaded"&&r.keys.length===2&&r.symbol==="AUDITAAA",JSON.stringify(r));
  });
  await step("csv-symbol-switch","CSV Upload",async()=>{
    await page.locator("#ticker").fill("AUDITBBB");
    await page.locator("#analyzeSymbol").click();
    await sleep(220);
    const r=await page.evaluate(()=>({kind:window.MSKWeb.state.kind,symbol:window.MSKWeb.features.getResearch()?.symbol,title:document.getElementById("chartTitle").textContent}));
    return ok(r.kind==="uploaded"&&r.symbol==="AUDITBBB"&&r.title.includes("AUDITBBB"),JSON.stringify(r));
  });
  await step("fundamental-visible","Fundamentals",async()=>{
    await page.locator('.action-card[data-go="fundamental"]').click();
    const input=page.locator("#fundCSV");
    const result={visible:await input.isVisible(),exists:await input.count(),label:await input.getAttribute("aria-label"),head:(await page.locator("#detailBody").innerText()).slice(0,220)};
    return ok(result.exists===1&&result.visible&&result.label?.includes("financial"),JSON.stringify(result));
  });
  await step("fundamental-import-csv","Fundamentals",async()=>{
    await page.locator("#ticker").fill("AUDITAAA");
    await page.locator("#analyzeSymbol").click();
    await page.locator('.action-card[data-go="fundamental"]').click();
    await page.locator("#fundCSV").setInputFiles({name:"MSK-AUDIT-FAKE-FUNDAMENTALS.csv",mimeType:"text/csv",buffer:Buffer.from(csvFinance())});
    await sleep(500);
    const d=await page.evaluate(()=>({statement:window.MSKWeb?.features?.statements?.get("AUDITAAA"),body:document.getElementById("detailBody")?.innerText||document.getElementById("detailBody")?.textContent}));
    return ok(d.statement?.pe===18.75 && /18\.75/.test(d.body||""),JSON.stringify({statement:d.statement,body:String(d.body).slice(0,450)}));
  });
  await step("screener-two-stock","Screener",async()=>{
    await page.locator('.action-card[data-go="screener"]').click();
    const txt=(await page.locator("#detailBody").innerText()).slice(0,440);
    return ok(txt.includes("AUDITAAA")&&txt.includes("AUDITBBB"),txt);
  });
  await step("breadth-two-stock","Breadth",async()=>{
    await page.locator('.action-card[data-go="breadth"]').click();
    const txt=(await page.locator("#detailBody").innerText()).slice(0,500);
    return ok(/Advances|Declines/.test(txt)&&/AUDIT|2 uploaded/.test(txt),txt);
  });
  await step("pdf-export-mode","Report",async()=>{
    await page.locator('.action-card[data-go="pdf"]').click();
    const controls=await page.locator("#detailBody button").allTextContents();
    return ok(controls.some(x=>x.includes("PDF")),"Report buttons: "+JSON.stringify(controls));
  });
  await step("ai-sbin-outlook","Local Research Q&A",async()=>{
    // Ask with an imported matching symbol and date; no private finance data sent.
    await page.locator('.action-card[data-go="ai"]').click();
    await page.locator("#chatInput").fill("AUDITAAA Upper Target for 3 month");
    await page.locator('#chatForm button[type="submit"]').click();
    await sleep(200);
    const answer=(await page.locator("#chatHistory").innerText()).slice(-1200);
    return ok(/not available|not.*predic|cannot.*predict|No price target/i.test(answer)&&!/USER CSV — SOURCE UNVERIFIED.*TejHQ/i.test(answer),answer);
  });
  await step("mobile-width-and-nav","Mobile",async()=>{
    await page.setViewportSize({width:390,height:844});await sleep(400);
    const width=await page.evaluate(()=>({sw:document.documentElement.scrollWidth,vw:innerWidth}));
    await page.locator("#mobileMenuToggle").click();
    const expanded=await page.locator("body.menu-open").count();
    await page.locator("#mobileScrim").click();
    return ok(width.sw<=width.vw+2&&expanded===1,JSON.stringify({width,expanded}));
  });
  await step("no-console-errors","Reliability",async()=>ok(!pageErrors.length&&!alerts.length,"pageErrors="+JSON.stringify(pageErrors)+"; browserDialogs="+JSON.stringify(alerts)));
  successful=true;
 }finally{
   fs.mkdirSync("web_advisory/browser-proof",{recursive:true});
   fs.writeFileSync("web_advisory/browser-proof/AUDIT-LIVE-RESULTS.json",JSON.stringify({
      timestamp:new Date().toISOString(),
      site:base,pass:results.filter(x=>x.status==="PASS").length,
      fail:results.filter(x=>x.status==="FAIL").length,
      checks:results,pageErrors,alerts,completed:successful
   },null,2));
   await browser.close();
   console.log("MSK_AUDIT_SUMMARY "+JSON.stringify({passed:results.filter(x=>x.status==="PASS").length,failed:results.filter(x=>x.status==="FAIL").length,checks:results.length,completed:successful}));
 }
})().catch(e=>{console.error("MSK_AUDIT_EXECUTION_ERROR",e.stack||String(e));process.exitCode=1});
