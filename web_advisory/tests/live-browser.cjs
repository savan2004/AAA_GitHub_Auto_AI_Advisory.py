/* Actual Chromium browser smoke test; runs against deployed Render site.
   Catches wrong-symbol frames (e.g. AAPL displayed while URL is ?symbol=BEL),
   incorrect dimensions, old cached scripts and tab navigation.
   Does NOT claim independently verified quote accuracy.
*/
const { chromium } = require("playwright-core");
const { mkdirSync } = require("node:fs");
const assert = require("node:assert/strict");

(async () => {
  const bin=process.env.CHROME_BIN||"/usr/bin/google-chrome";
  const browser=await chromium.launch({headless:true,executablePath:bin,args:["--no-sandbox","--disable-dev-shm-usage"]});
  const page=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});
  const url="https://msk-web-advisory-preview.onrender.com/?symbol=BEL";
  try{
    await page.goto(url,{waitUntil:"domcontentloaded",timeout:30000});
    await page.waitForSelector("tv-advanced-chart",{timeout:15000});
    const state=await page.evaluate(()=>{
      const chart=document.querySelector("tv-advanced-chart");
      const body=document.body;
      return {
        expected:chart?.getAttribute("symbol"),
        interval:chart?.getAttribute("interval"),
        chartHeight:chart?.getBoundingClientRect().height,
        chartWidth:chart?.getBoundingClientRect().width,
        mode:body.classList.contains("market-external"),
        signal:document.getElementById("signal")?.textContent,
        lastPrice:document.getElementById("lastPrice")?.textContent,
        legacyScripts:document.querySelectorAll('script[src*="embed-widget-advanced-chart.js"]').length,
        buttons:[...document.querySelectorAll(".nav")].map(e=>e.dataset.tab)
      };
    });
    console.log("REAL_BROWSER_MARKUP",JSON.stringify(state));
    assert.equal(state.expected,"NSE:BEL");
    assert.equal(state.interval,"1D");
    assert(state.chartHeight>=460,"Visible candlesticks must occupy >=460px height");
    assert(state.chartWidth>=400,"Candles not allowed in a narrow 250px panel");
    assert(state.mode);
    assert.equal(state.lastPrice,"No MSK quote");
    assert.equal(state.signal,"NOT ASSESSED");
    assert.equal(state.legacyScripts,0,"Old default-AAPL widget scripts must be absent");
    assert.equal(state.buttons.length,8);
    await page.waitForTimeout(12000);
    const frames=page.frames().filter(f=>/tradingview/i.test(f.url()));
    const details=[];
    let wrongSymbol=false;
    for(const frame of frames){
      const frameUrl=frame.url();
      let body="";
      try{body=(await frame.locator("body").innerText({timeout:3000})).slice(0,3000);}catch{}
      details.push({url:frameUrl.slice(0,400),body:body.slice(0,240)});
      if(/(?:NASDAQ[:%]2?AAPL|Apple Inc)/i.test(frameUrl+" "+body))wrongSymbol=true;
    }
    console.log("REAL_BROWSER_PROVIDER_FRAMES",JSON.stringify(details));
    if(wrongSymbol)throw Error("CRITICAL: provider displayed Apple while NSE:BEL selected");
    mkdirSync("web_advisory/browser-proof",{recursive:true});
    await page.screenshot({path:"web_advisory/browser-proof/BEL-desktop.png",fullPage:true});
    await page.getByRole("button",{name:/technical analysis/i}).click();
    const rating=await page.locator("tv-technical-analysis").getAttribute("symbol");
    assert.equal(rating,"NSE:BEL");
    await page.getByRole("button",{name:/fundamentals/i}).click();
    assert(await page.locator(".financial-next-step").count()>0);
    await page.getByRole("button",{name:/trading chart/i}).click();
    await page.setViewportSize({width:390,height:844});
    const mobile=await page.evaluate(()=>{
      const x=document.querySelector("tv-advanced-chart");
      return {width:x.getBoundingClientRect().width,height:x.getBoundingClientRect().height,
       scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth};
    });
    console.log("REAL_BROWSER_MOBILE",JSON.stringify(mobile));
    assert(mobile.width>=260 && mobile.width<=mobile.viewport+2);
    assert(mobile.height>=450);
    assert(mobile.scrollWidth<=mobile.viewport+2,"Horizontal viewport overflow");
    await page.screenshot({path:"web_advisory/browser-proof/BEL-mobile.png",fullPage:true});
    if(frames.length===0)console.log("PROVIDER_NOT_PROVEN: Widget HTML present, but external chart frame did not load within 12 seconds; do not claim provider data rendered");
    console.log("MSK_CHROMIUM_BEL_END_TO_END_OK");
  }finally{await browser.close();}
})().catch(err=>{console.error("MSK_CHROMIUM_BEL_FAILURE",err.stack||String(err));process.exitCode=1});
