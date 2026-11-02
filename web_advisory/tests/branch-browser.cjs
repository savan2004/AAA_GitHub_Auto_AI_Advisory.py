"use strict";
/* Website-only local Chromium acceptance tests. Requires playwright-core.
 * Start a local static server at http://127.0.0.1:8766 in CI.
 * Never calls or changes live Render, Telegram or AiAutoUpdate.
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const executablePath = process.env.CHROME_BIN || "/usr/bin/google-chrome";
  const browser = await chromium.launch({
    executablePath, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"]
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.route("**/*", route => {
      const url = route.request().url();
      if (url.startsWith("http://127.0.0.1:8766/")) return route.continue();
      return route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"offline_test"}' });
    });
    const errors = [];
    page.on("pageerror", err => errors.push(err.message));
    await page.goto("http://127.0.0.1:8766/", { waitUntil: "domcontentloaded" });
    await page.locator("#loadSample").click();
    assert.match(await page.locator("#dataLabel").innerText(), /SYNTHETIC/);

    const expectations = {
      overview: /Research|Source|Trend/i,
      technical: /Historical support|Historical resistance/i,
      fundamental: /financial statement|financial|import/i,
      chart: /Trend|EMA|Pattern/i,
      screener: /Stock|Trend|ADX/i,
      breadth: /Advances|Declines|Coverage/i,
      pdf: /Company overview/i,
      ai: /Local evidence|local|generative/i,
      portfolio: /No holdings|Portfolio|holdings/i,
    };
    for (const [tab, expected] of Object.entries(expectations)) {
      await page.locator('.nav[data-tab="' + tab + '"]').click();
      assert.equal(await page.locator("body").getAttribute("data-page"), tab, "Navigation failed: " + tab);
      const body = (await page.locator("#detailBody").innerText()) +
                   (tab === "ai" ? await page.locator("#aiPanel").innerText() : "");
      assert.match(body, expected, "Feature output missing: " + tab);
      if (tab === "technical") assert.match(body, /EMA 20|EMA 50/);
      if (tab === "pdf") assert.equal(await page.locator(".msk-report-page").count(), 8);
      console.log("BUTTON_ACCEPTED", tab);
    }

    await page.locator('.nav[data-tab="ai"]').click();
    await page.locator("#chatInput").fill("What does ADX mean?");
    await page.locator("#chatForm button[type=submit]").click();
    assert.match(await page.locator("#chatHistory").innerText(), /ADX/i);

    await page.locator("#openImport").click();
    assert.equal(await page.locator("#importModal").getAttribute("hidden"), null);
    for (const kind of ["price", "fundamental", "portfolio"]) {
      await page.locator('[data-import-kind="' + kind + '"]').click();
      assert.equal(await page.locator('[data-import-kind="' + kind + '"]').getAttribute("aria-selected"), "true");
    }
    await page.locator("#closeImport").click();

    // Check that the layout remains within viewport at mobile widths; source data stays offline.
    for (const width of [375, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await page.evaluate(() =>
        document.documentElement.scrollWidth > innerWidth + 2);
      assert.equal(overflow, false, "Horizontal page overflow at " + width + "px");
      console.log("LAYOUT_ACCEPTED", width);
    }
    assert.deepEqual(errors, [], "Uncaught browser JavaScript exceptions");
    console.log("MSK_NINE_BUTTON_BROWSER_ACCEPTANCE_COMPLETE");
  } finally {
    await browser.close();
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
