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
    await page.waitForSelector(".nse-unavailable",{timeout:15000});
    const state=await page.evaluate(()=>{
      const chart=document.querySelector(".nse-unavailable");
      const body=document.body;
      return {
        expected:chart?.getAttribute("data-symbol"),
        providerRestricted:chart?.textContent.includes("DATA ACCESS RESTRICTED"),
        providerLink:chart?.querySelector("a")?.href,
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
    assert(state.providerRestricted,"Restricted market data must be disclosed");
    assert.equal(state.providerLink,"https://in.tradingview.com/symbols/NSE-BEL/");
    assert(state.chartHeight>=460,"Stock-specific link panel must remain readable");
    assert(state.chartWidth>=400,"Candles not allowed in a narrow 250px panel");
    assert(state.mode);
    assert.equal(state.lastPrice,"No MSK quote");
    assert.equal(state.signal,"NOT ASSESSED");
    assert.equal(state.legacyScripts,0,"Old default-AAPL widget scripts must be absent");
    assert.equal(state.buttons.length,8);
    await page.waitForTimeout(2000);
    assert.equal(await page.locator("tv-advanced-chart").count(),0,"No exchange-blocked TradingView widget");
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
    if(wrongSymbol || frames.length>0)throw Error("CRITICAL: embed is forbidden for NSE:BEL; provider frames must never load");
    mkdirSync("web_advisory/browser-proof",{recursive:true});
    await page.screenshot({path:"web_advisory/browser-proof/BEL-desktop.png",fullPage:true});
    await page.getByRole("button",{name:/technical analysis/i}).click();
    assert.equal(await page.locator("tv-technical-analysis").count(),0,"Technical widget is exchange-restricted too");
    assert((await page.locator("#detailBody").innerText()).includes("cannot display this NSE symbol"));
    await page.getByRole("button",{name:/fundamentals/i}).click();
    assert(await page.locator(".financial-next-step").count()>0);
    await page.getByRole("button",{name:/trading chart/i}).click();
    await page.setViewportSize({width:390,height:844});
    const mobile=await page.evaluate(()=>{
      const x=document.querySelector(".nse-unavailable");
      return {width:x.getBoundingClientRect().width,height:x.getBoundingClientRect().height,
       scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth};
    });
    console.log("REAL_BROWSER_MOBILE",JSON.stringify(mobile));
    assert(mobile.width>=260 && mobile.width<=mobile.viewport+2);
    assert(mobile.height>=450);
    assert(mobile.scrollWidth<=mobile.viewport+2,"Horizontal viewport overflow");
    await page.screenshot({path:"web_advisory/browser-proof/BEL-mobile.png",fullPage:true});
    console.log("BEL_RESTRICTED_SOURCE_HANDLED: No alternate symbol displayed; direct TradingView link visible");
    console.log("MSK_CHROMIUM_BEL_END_TO_END_OK");
  }finally{await browser.close();}
})().catch(err=>{console.error("MSK_CHROMIUM_BEL_FAILURE",err.stack||String(err));process.exitCode=1});
