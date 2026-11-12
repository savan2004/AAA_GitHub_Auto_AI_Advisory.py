"use strict";
/* LIVE Render site test. Read-only browsing, no provider secrets or client data. */
const {chromium}=require("playwright-core");
const assert=require("node:assert/strict");
const base=(process.env.MSK_WEB_URL||"https://msk-web-advisory-preview.onrender.com").replace(/\/$/,"");
const pages={
 "index.html":"overview","technical.html":"technical","fundamental.html":"fundamental",
 "chart.html":"chart","screener.html":"screener","breadth.html":"breadth",
 "pdf.html":"pdf","portfolio.html":"portfolio","ai.html":"ai"
};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||"/usr/bin/google-chrome",
  headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
 try{
  const page=await browser.newPage({viewport:{width:1366,height:850}});
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  for(const [file,tab] of Object.entries(pages)){
   const response=await page.goto(base+"/"+file,{waitUntil:"domcontentloaded",timeout:30000});
   assert.equal(response?.status(),200,"Live route HTTP failed: "+file);
   await page.waitForFunction(t=>window.MSKDashboard&&document.body.dataset.page===t,tab,{timeout:15000});
   const state=await page.evaluate(()=>({title:document.title,page:document.body.dataset.page,
    heading:document.querySelector(".heading-block h1")?.textContent,
    nav:document.querySelectorAll(".nav[data-tab]").length,
    detail:document.getElementById("detailBody")?.textContent?.length??0}));
   assert.equal(state.page,tab);
   assert.equal(state.nav,9);
   assert.ok(state.detail>5,"Empty live research view: "+file);
   console.log("RENDER_LIVE_DEDICATED_PAGE_PASS",file,JSON.stringify(state));
  }
  await page.goto(base+"/portfolio.html",{waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>window.MSKPortfolioAudit&&document.body.dataset.page==="portfolio");
  await page.locator('.nav[data-tab="portfolio"]').click();
  const body=await page.locator("#detailBody").innerText();
  for(const label of ["Upload Holdings CSV","Import Historical Prices CSV","Import Financial Ratios CSV"])
   assert.ok(body.includes(label),label+" missing on live Portfolio Health");
  console.log("RENDER_LIVE_PORTFOLIO_ACTIONS_PASS");
  await page.locator('.nav[data-tab="technical"]').click();
  assert.equal(new URL(page.url()).pathname,"/technical.html");
  await page.goBack({waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>document.body.dataset.page==="portfolio");
  console.log("RENDER_LIVE_BROWSER_HISTORY_PASS");
  // External TejHQ availability is separately guarded by existing EOD test.
  assert.deepEqual(errors,[]);
  console.log("RENDER_LIVE_NINE_PAGES_VERIFIED");
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack||String(e));process.exitCode=1});
