"use strict";
/* Dedicated-page browser acceptance; runs on local static files only.
 * This test checks real direct URLs, reload, navigation, Back/Forward,
 * portfolio session integrity, and responsive layout without market traffic.
 */
const {chromium}=require("playwright-core");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const paths={
 overview:"index.html",technical:"technical.html",fundamental:"fundamental.html",
 chart:"chart.html",screener:"screener.html",breadth:"breadth.html",
 pdf:"pdf.html",portfolio:"portfolio.html",ai:"ai.html"
};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||"/usr/bin/google-chrome",
   headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900},acceptDownloads:true});
  const failures=[];
  page.on("pageerror",err=>failures.push(err.message));
  await page.route("**/*",request=>{
    if(request.request().url().startsWith("http://127.0.0.1:8766/"))return request.continue();
    return request.fulfill({status:503,contentType:"application/json",body:'{"error":"offline_test"}'});
  });
  for(const [tab,route] of Object.entries(paths)){
    await page.goto("http://127.0.0.1:8766/"+route,{waitUntil:"domcontentloaded"});
    await page.waitForFunction(()=>window.MSKDashboard&&document.body.dataset.page===new URL(location.href).pathname.split("/").pop().replace(".html","").replace("index","overview"),
       {timeout:20000});
    assert.equal(await page.locator(".nav[data-tab]").count(),9);
    assert.equal(await page.locator("#detailBody").count(),1);
    const heading=await page.locator(".heading-block h1").innerText();
    assert.ok(heading.length>5);
    assert.equal(await page.locator('.nav[data-tab="'+tab+'"]').getAttribute("aria-current"),"page");
    if(tab!=="overview"){
      assert.equal(await page.locator("#researchBreadcrumb").isVisible(),true);
      assert.equal(await page.locator(".quick-actions").isVisible(),false);
    }
    console.log("DIRECT_ROUTE_PASS",route,heading);
  }
  await page.goto("http://127.0.0.1:8766/index.html",{waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>window.MSKDashboardEvidence&&document.getElementById("dashboardEvidence"));
  await page.locator("#loadSample").click();
  assert.match(await page.locator("#dashboardEvidence").innerText(),/Snapshot & data confidence/);
  assert.match(await page.locator("#dashboardEvidence").innerText(),/SYNTHETIC|simulated/i);
  assert.equal(await page.locator("#dashboardEvidence").getAttribute("data-source"),"synthetic");
  await page.locator("#ticker").fill("NOTLOADED");
  assert.equal(await page.locator("body").getAttribute("data-symbol-pending"),"true");
  assert.equal(await page.locator(".snapshot-grid").isVisible(),false);
  await page.getByRole("button",{name:"Import source data"}).click();
  assert.equal(await page.locator("#importModal").getAttribute("hidden"),null);
  await page.locator("#closeImport").click();
  console.log("DASHBOARD_SOURCE_GATES_AND_IMPORT_ACTION_PASS");
  await page.goto("http://127.0.0.1:8766/portfolio.html",{waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>window.MSKPortfolioAudit&&document.body.dataset.page==="portfolio");
  await page.locator('.nav[data-tab="portfolio"]').click();
  await page.getByRole("button",{name:/Upload Holdings CSV/}).click();
  await page.locator("#importFile").setInputFiles({
    name:"client-holdings.csv",mimeType:"text/csv",
    buffer:Buffer.from("symbol,quantity,buy_price\nBEL,10,100\nTCS,3,2000\n")
  });
  await page.waitForFunction(()=>!document.querySelector("#confirmImport").disabled);
  await page.locator("#confirmImport").click();
  assert.match(await page.locator("#detailBody").innerText(),/Holdings 2/);
  await page.locator('.nav[data-tab="technical"]').click();
  assert.equal(new URL(page.url()).pathname,"/technical.html");
  assert.equal(await page.locator("body").getAttribute("data-page"),"technical");
  await page.locator('.nav[data-tab="portfolio"]').click();
  assert.equal(new URL(page.url()).pathname,"/portfolio.html");
  assert.match(await page.locator("#detailBody").innerText(),/Holdings 2/);
  await page.goBack({waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>document.body.dataset.page==="technical");
  await page.goForward({waitUntil:"domcontentloaded"});
  await page.waitForFunction(()=>document.body.dataset.page==="portfolio");
  assert.match(await page.locator("#detailBody").innerText(),/Holdings 2/);
  console.log("SESSION_PRESERVED_ON_ROUTE_CHANGE_AND_BACK_FORWARD");
  fs.mkdirSync("/tmp/msk-pages-browser",{recursive:true});
  await page.screenshot({path:"/tmp/msk-pages-browser/portfolio-desktop.png",fullPage:true});
  for(const [tab,route] of Object.entries(paths)){
    await page.goto("http://127.0.0.1:8766/"+route,{waitUntil:"domcontentloaded"});
    await page.waitForFunction(()=>window.MSKDashboard);
    await page.setViewportSize({width:375,height:820});
    await page.waitForTimeout(300);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
    assert.equal(overflow,false,"Mobile horizontal overflow on "+route);
    assert.ok(await page.locator("#mobileMenuToggle").isVisible());
    console.log("MOBILE_ROUTE_PASS",route);
    if(tab==="portfolio")await page.screenshot({path:"/tmp/msk-pages-browser/portfolio-mobile.png",fullPage:true});
  }
  assert.deepEqual(failures,[],"Browser JS errors");
  console.log("DEDICATED_NINE_PAGE_ACCEPTANCE_COMPLETE");
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack||String(e));process.exitCode=1});
