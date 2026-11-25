// Browser-only test with a synthetic status response. No USB or capture required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "playwright-core");

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH
    ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 720, height: 1280 }, hasTouch: true });
    const html = fs.readFileSync(path.join(__dirname, "../apps/raspberrypi/dashboard.html"), "utf8");
    const actions = [];
    const id = "c".repeat(32);
    const payload = { relay: { state: "listening", bytes_windows_to_iphone: 8192,
      bytes_iphone_to_windows: 4096, updated_utc: "2026-01-01T12:00:00Z" },
      health: { gadget_up: true, iphone_usb: false, temperature: null, memory_percent: 28 },
      capture: { interfaces: { gadget: { interface: "lab0", filter: "tcp port 43123" },
        usbmon: { interface: "usbmon3", filter: "usb device 2" } },
        active: [], captures: [], free: null }, token: "synthetic-token", local_display: true };
    await page.route("http://127.0.0.1:8765/**", async route => {
      const request = route.request();
      const target = new URL(request.url()).pathname;
      if (target === "/") return route.fulfill({ contentType: "text/html; charset=utf-8", body: html });
      if (target === "/api/status") return route.fulfill({ json: payload });
      if (request.method() === "POST") {
        assert.equal(request.headers()["x-ulab-token"], payload.token);
        const data = request.postDataJSON();
        actions.push([target, data]);
        if (target === "/api/start") {
          payload.capture.active = [id];
          payload.capture.captures = [{ id, layer: data.layer, result: "capturing", packets: null,
            dropped: null, size: 0, started_utc: "2026-01-01T12:00:00Z" }];
        }
        if (target === "/api/stop") {
          payload.capture.active = [];
          payload.capture.captures[0].result = "complete";
          payload.capture.captures[0].packets = 2;
        }
        return route.fulfill({ json: { ok: true } });
      }
      return route.fulfill({ status: 404, body: "" });
    });
    await page.goto("http://127.0.0.1:8765/");
    await page.locator("#start:enabled").waitFor();
    assert.equal(await page.locator("#temperature").textContent(), "미확인");
    assert.equal(await page.locator("#iphone").innerText(), "없음");
    assert.equal(await page.locator("#stop").isDisabled(), true);
    await page.locator("#layer").selectOption("usbmon");
    assert.equal(await page.locator("#start").isDisabled(), true);
    await page.locator("#layer").selectOption("gadget");
    await page.locator("#start").click();
    await page.locator("#stop:enabled").waitFor();
    await page.locator("#stop").click();
    await page.locator("a[download]").waitFor();
    assert.equal(actions[0][0], "/api/start");
    assert.equal(actions[0][1].layer, "gadget");
    assert.deepEqual(actions[1], ["/api/stop", { id }]);
    assert.equal(await page.locator("a[download]").getAttribute("href"), "/download/" + id);
    await page.locator("#live").click();
    await page.waitForFunction(() => !document.querySelector("#live").disabled);
    assert.equal(actions.at(-1)[0], "/api/live");
    await page.locator("[data-open]").click();
    assert.deepEqual(actions.at(-1), ["/api/open", { id }]);
    for (const width of [720, 360, 1280]) {
      await page.setViewportSize({ width, height: 1280 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const rect = await page.locator("#start").boundingBox();
      assert.ok(rect.height >= 50 && rect.y + rect.height <= 1280);
    }
    if (process.env.UI_TEST_SCREENSHOT) {
      await page.setViewportSize({ width: 720, height: 1280 });
      await page.screenshot({ path: process.env.UI_TEST_SCREENSHOT });
    }
    payload.capture={interfaces:{}, active:[], captures:[], free:null, unavailable:true};
    payload.local_display=false;
    await page.waitForFunction(() => document.querySelector("#state").textContent==="캡처 서비스 대기");
    assert.equal(await page.locator("#start").isDisabled(),true);
    assert.equal(await page.locator("#live").isDisabled(),true);
    console.log("Touch dashboard browser checks passed (synthetic API).");
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
