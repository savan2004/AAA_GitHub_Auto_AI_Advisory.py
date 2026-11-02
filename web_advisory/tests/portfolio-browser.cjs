"use strict";
/* Browser evidence: real holdings CSV -> audit -> CSV download -> PDF export.
   External provider is deterministically mocked. No secrets or public deploy. */
const {chromium}=require("playwright-core");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||"/usr/bin/google-chrome",
   headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900},acceptDownloads:true});
  const errors=[];
  page.on("pageerror",err=>errors.push(err.message));
  const date=new Date(Date.now()-86400000).toISOString().slice(0,10);
  await page.route("**/*",route=>{
   const url=route.request().url();
   if(url.startsWith("http://127.0.0.1:8766/"))return route.continue();
   if(url.startsWith("https://api.tejhq.dev/v1/ohlcv/nse/")){
    const symbol=new URL(url).pathname.split("/").at(-1);
    if(!["AAA","BBB"].includes(symbol))
     return route.fulfill({status:404,contentType:"application/json",body:'{"error":"not found"}'});
    const bars=Array.from({length:55},(_,i)=>{
     const day=new Date(Date.now()-(55-i)*86400000).toISOString().slice(0,10);
     const c=(symbol==="AAA"?100:200)+i;
     return {date:day,open:c-1,high:c+2,low:c-2,close:c,volume:1000+i};
    });
    bars.at(-1).date=date;
    return route.fulfill({status:200,contentType:"application/json",
      headers:{"access-control-allow-origin":"*"},
      body:JSON.stringify({meta:{symbol,exchange:"NSE"},data:bars})});
   }
   return route.fulfill({status:503,contentType:"application/json",body:'{"error":"offline"}'});
  });
  await page.goto("http://127.0.0.1:8766/",{waitUntil:"domcontentloaded"});
  await page.locator('.nav[data-tab="portfolio"]').click();
  assert.equal(await page.locator("body").getAttribute("data-page"),"portfolio");
  for(const name of ["Upload Holdings CSV","Import Historical Prices CSV","Import Financial Ratios CSV"]){
   assert.ok((await page.locator("#detailBody").innerText()).includes(name),name);
  }
  await page.getByRole("button",{name:/Upload Holdings CSV/}).click();
  assert.equal(await page.locator("#importModal").getAttribute("hidden"),null);
  await page.locator("#importFile").setInputFiles({
   name:"holdings.csv",mimeType:"text/csv",
   buffer:Buffer.from("symbol,quantity,buy_price\nAAA,2,100\nBBB,3,200\n")
  });
  await page.locator("#confirmImport").waitFor({state:"visible"});
  await page.waitForFunction(()=>!document.getElementById("confirmImport").disabled);
  await page.locator("#confirmImport").click();
  assert.equal(await page.locator("body").getAttribute("data-page"),"portfolio");
  const auditButton=page.getByRole("button",{name:"Run Portfolio Audit"});
  assert.equal(await auditButton.isVisible(),true);
  await auditButton.click();
  await page.waitForFunction(()=>window.MSKPortfolioAudit?.getAudit()?.priced===2,{timeout:25000});
  const result=await page.evaluate(()=>window.MSKPortfolioAudit.getAudit());
  assert.equal(result.entries.length,2);
  assert.equal(result.commonDate,date);
  assert.ok(result.totalValue>0);
  assert.equal(result.invested,800);
  assert.match(await page.locator("#detailBody").innerText(),/Historical unrealized gain/);
  console.log("PORTFOLIO_IMPORTED_AND_AUDITED",result.entries.length,result.priced,result.commonDate);
  const [download]=await Promise.all([
   page.waitForEvent("download"),
   page.getByRole("button",{name:"Download Audit CSV"}).click()
  ]);
  assert.match(download.suggestedFilename(),/^msk-portfolio-audit-/);
  const csv=fs.readFileSync(await download.path(),"utf8");
  assert.match(csv,/AAA/);assert.match(csv,/BBB/);
  assert.match(csv,/Total invested INR/);
  assert.match(csv,/Common-date EOD/);
  console.log("PORTFOLIO_AUDIT_CSV_DOWNLOADED",csv.length);

  await page.evaluate(()=>{window.print=()=>{document.body.dataset.portfolioPrinted="true"};});
  await page.getByRole("button",{name:/Save Audit PDF/}).click();
  assert.equal(await page.locator("body").getAttribute("data-portfolio-printed"),"true");
  await page.emulateMedia({media:"print"});
  assert.ok(await page.locator("#mskPortfolioPrint").isVisible());
  const pdf=await page.pdf({format:"A4",printBackground:true,preferCSSPageSize:true});
  assert.ok(pdf.length>2000);
  fs.writeFileSync("/tmp/msk-portfolio-audit-browser.pdf",pdf);
  console.log("PORTFOLIO_AUDIT_PDF_GENERATED",pdf.length);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
