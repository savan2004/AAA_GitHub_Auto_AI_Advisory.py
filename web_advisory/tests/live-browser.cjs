/* MSK real Chromium proof, not jsdom: validate actual EOD BEL candles on Render.
   The free source returns historical daily observations only. No trading accuracy
   or SEBI/commercial-data display licence is inferred from passing tests. */
const {chromium}=require("playwright-core");
const {mkdirSync}=require("node:fs");
const assert=require("node:assert/strict");
(async()=>{
 const bin=process.env.CHROME_BIN||"/usr/bin/google-chrome";
 const browser=await chromium.launch({headless:true,executablePath:bin,args:["--no-sandbox","--disable-dev-shm-usage"]});
 const page=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});
 const root="https://msk-web-advisory-preview.onrender.com/?symbol=BEL";
 try{
   const external=[];
   page.on("requestfailed",r=>{if(/api\.tejhq\.dev/i.test(r.url()))external.push(r.url()+": "+r.failure()?.errorText);});
   // The ROOT website must open a REAL EOD stock, not silently default
   // to fictional DEMO01 price. The demo button remains deliberately opt-in.
   await page.goto("https://msk-web-advisory-preview.onrender.com/",{waitUntil:"domcontentloaded",timeout:30000});
   await page.waitForSelector(".market-eod #dataLabel[data-eod-provider='TejHQ']",{
     state:"attached",timeout:25000
   });
   assert.equal(await page.locator("#ticker").inputValue(),"BEL");
   assert.match(await page.locator("#lastPrice").innerText(),/₹/);
   console.log("MSK_ROOT_REAL_EOD_PASS",await page.locator("#priceMeta").innerText());
   await page.goto(root,{waitUntil:"domcontentloaded",timeout:30000});
   await page.waitForSelector(".market-eod #dataLabel[data-eod-provider='TejHQ']",{
     state:"attached",timeout:25000
   });
   const state=await page.evaluate(()=>{
      const root=document.body,canvas=document.getElementById("chartCanvas");
      const d=document.getElementById("dataLabel");
      const rect=canvas.getBoundingClientRect();
      return {symbol:document.getElementById("ticker").value,
        source:d.dataset.eodProvider,asof:d.dataset.marketAsof,
        sourceText:d.textContent,lastPrice:document.getElementById("lastPrice").textContent,
        signal:document.getElementById("signal").textContent,
        adx:document.getElementById("adx").textContent,
        volume:document.getElementById("vol").textContent,
        marketEOD:root.classList.contains("market-eod"),
        chartWidth:rect.width,chartHeight:rect.height,
        indicatorText:document.getElementById("analysisLines").textContent,
        canvasHidden:canvas.hidden,
        canvasWidth:canvas.width,
        syntheticMix:window.MSKWeb.features.library.has("DEMO01"),
        candles:window.MSKWeb.state.data.length,
        close:window.MSKWeb.state.calc?.last||null
      };
   });
   console.log("MSK_EOD_REAL_BROWSER_DESKTOP",JSON.stringify(state));
   assert.equal(state.symbol,"BEL");
   assert.equal(state.source,"TejHQ");
   assert(state.asof>="2026-10-01"&&state.asof<="2026-10-27");
   assert.match(state.sourceText,/NSE END-OF-DAY.*NOT INTRADAY\/LIVE/);
   assert.match(state.lastPrice,/₹/);
   assert(state.marketEOD);
   assert.equal(state.signal,"RESEARCH ONLY");
   assert(!state.canvasHidden&&state.canvasWidth>1000);
   assert(state.chartWidth>=880,"Chart must be wide enough to see real candle wicks and bodies");
   assert(state.chartHeight>=530);
   assert(state.candles>=80,"Need enough real daily candles for EMA/ADX");
   assert.equal(state.syntheticMix,false);
   assert.equal(await page.locator("tv-advanced-chart").count(),0);
   assert.equal(external.length,0,"NSE EOD provider fetch must complete");
   const source=await fetch("https://api.tejhq.dev/v1/ohlcv/nse/BEL?from=2026-05-01&to=2026-10-27",{
     signal:AbortSignal.timeout(16000)
   }).then(r=>r.json());
   const latest=source.data.at(-1);
   assert.equal(state.asof,latest.date);
   assert(Math.abs(state.close-latest.close)<0.001,"Actual displayed EOD quote must match provider");
   mkdirSync("web_advisory/browser-proof",{recursive:true});
   await page.screenshot({path:"web_advisory/browser-proof/BEL-real-EOD-desktop.png",fullPage:true});
   await page.getByRole("button",{name:/technical analysis/i}).click();
   assert((await page.locator("#detailBody").innerText()).includes("EMA20"));
   await page.getByRole("button",{name:/trading chart/i}).click();
   await page.setViewportSize({width:390,height:844});
   const mobile=await page.evaluate(()=>{
     const c=document.querySelector("#chartCanvas");
     return {width:c.getBoundingClientRect().width,height:c.getBoundingClientRect().height,
       viewport:window.innerWidth,scrollWidth:document.documentElement.scrollWidth};
   });
   console.log("MSK_EOD_REAL_BROWSER_MOBILE",JSON.stringify(mobile));
   assert(mobile.width>=260&&mobile.width<=mobile.viewport+1);
   assert(mobile.height>=430);
   assert(mobile.scrollWidth<=mobile.viewport+2);
   await page.screenshot({path:"web_advisory/browser-proof/BEL-real-EOD-mobile.png",fullPage:true});
   await page.getByRole("button",{name:/fundamentals/i}).click();
   assert(/No financial statements imported|Unavailable|financial-statement CSV/i.test(await page.locator("#detailBody").innerText()),"No financial ratios may be invented from a price chart");
   console.log("MSK_BEL_EOD_REAL_CHROME_PASS",state.asof,state.close);
 }finally{await browser.close();}
})().catch(err=>{console.error("MSK_BEL_EOD_REAL_CHROME_FAIL",err.stack||String(err));process.exitCode=1});
