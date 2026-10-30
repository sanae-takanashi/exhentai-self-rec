// Synthetic pages only: no real downloads, browser profiles or external networking.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const scriptPath = path.resolve(__dirname, "../static/review-keyboard-hints.user.js");
const output = path.resolve(__dirname, `../data/browser-validation/keyboard-hints-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const gallery = "https://exhentai.org/g/123/fixture/";
const archive = "https://exhentai.org/archiver.php?gid=123&token=fixture";
let browser;

async function scenario(viewport, colorScheme) {
  const context = await browser.newContext({ viewport, colorScheme });
  const requests = [], errors = [];
  await context.route("**/*", async (route) => {
    const request = route.request();
    requests.push({ url: request.url(), method: request.method() });
    await route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Synthetic native page</title>
      <style>body { margin: 8px; font: 14px sans-serif; } a { display: inline-block; padding: 6px; } a:focus { outline: 3px solid #246bce; }</style>
      <h1>Synthetic native page</h1>
      <form id="hathdl_form"><input aria-label="Fixture input"><button>Fixture submit</button></form>
      <table><tr><td><a href="#" onclick="return do_hathdl('org')">Original</a></td>
      <td><a href="#" onclick="return do_hathdl('800')">800x</a></td>
      <td><a href="#" onclick="return do_hathdl('1280')">1280x</a></td>
      <td><a href="#" onclick="return do_hathdl('1920')">1920x</a></td>
      <td><a href="#" onclick="return do_hathdl('2560')">2560x</a></td></tr></table>
      <a id="last" href="#">Last native action</a>
      <script>window.submits=0; window.downloads=0;
      window.do_hathdl=()=>{ window.downloads++; return false; };
      document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.submits++;});</script>` });
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  const host = page.locator("#review-keyboard-hints");
  const status = host.locator(".status");
  const dock = host.locator(".dock");
  const native = (size) => page.locator(`a[onclick="return do_hathdl('${size}')"]`);
  const expectStatus = (text) => page.waitForFunction((expected) =>
    document.querySelector("#review-keyboard-hints")?.shadowRoot.querySelector(".status").textContent === expected, text);
  async function checkBounds() {
    const box = await dock.boundingBox();
    assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height,
      `dock outside viewport: ${JSON.stringify(box)}`);
    for (const hint of await host.locator(".items > span").all()) {
      const b = await hint.boundingBox();
      assert(b.x >= box.x && b.x + b.width <= box.x + box.width && b.y + b.height <= box.y + box.height,
        "hint text outside dock");
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator("#last").evaluate((el) => el.scrollIntoView({ block: "start" }));
    const last = await page.locator("#last").boundingBox();
    assert(last.y + last.height <= box.y, "dock hides the last native action at scroll bottom");
  }
  try {
    await page.goto(archive);
    const nativeHTML = await page.locator("table").innerHTML();
    await page.addScriptTag({ path: scriptPath });
    await expectStatus("1280x available. Press d to focus it.");
    assert.equal(await page.evaluate(() => document.activeElement.tagName), "BODY", "hints moved focus");
    await page.addScriptTag({ path: scriptPath });
    assert.equal(await host.count(), 1, "duplicate hint dock");
    await page.keyboard.press("d");
    assert.equal(await page.evaluate(() => document.activeElement.tagName), "BODY", "hints intercepted d");
    // Emulate only Vimium's focus result, not a download click or installed extension.
    await native("1280").focus();
    await expectStatus("1280x focused. Enter submits this option.");
    await checkBounds();
    await page.screenshot({ path: path.join(output, `${viewport.width}-${viewport.height}-${colorScheme}-archive.png`) });
    await native("800").focus();
    await expectStatus("800x focused. Enter submits this option.");
    await native("org").focus();
    await expectStatus("Original focused. Press d for a numeric option, or f to choose manually.");
    await page.locator("input").focus();
    await expectStatus("Typing: press Esc before using single-key shortcuts.");
    await host.locator("summary").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.querySelector("#review-keyboard-hints").shadowRoot.querySelector("details").open);
    assert.equal(await host.locator(".items").isVisible(), false);
    let collapsed = await dock.boundingBox();
    assert(collapsed.width <= 48 && collapsed.height >= 40, "collapsed toggle lost its hit area");
    await page.screenshot({ path: path.join(output, `${viewport.width}-${colorScheme}-collapsed.png`) });
    await page.keyboard.press("Space");
    await page.waitForFunction(() => document.querySelector("#review-keyboard-hints").shadowRoot.querySelector("details").open);
    for (const [size, next] of [["1280", "800"], ["800", "1920"], ["1920", "2560"], ["2560", null]]) {
      await native(size).evaluate((el) => { el.hidden = true; el.style.display = "none"; });
      await expectStatus(next ? `${next}x available. Press d to focus it.` : "No supported numeric H@H option. Choose manually with f.");
    }
    // The script leaves original links, handlers and form data untouched.
    assert.equal(await native("org").getAttribute("onclick"), "return do_hathdl('org')");
    assert.equal(await page.evaluate(() => window.downloads + window.submits), 0);
    assert.equal(requests.length, 1, "hints performed networking");
    assert.equal(requests[0].method, "GET");
    await page.goto(gallery);
    await page.addScriptTag({ path: scriptPath });
    assert.equal(await status.textContent(), "");
    assert.match(await host.locator(".items").innerText(), /f Link hints/);
    assert.match(await host.locator(".items").innerText(), /j\/k Page down \/ up/);
    assert.doesNotMatch(await host.locator(".items").innerText(), /download/);
    assert.equal(await page.locator("table").innerHTML(), nativeHTML);
    await checkBounds();
    await page.screenshot({ path: path.join(output, `${viewport.width}-${viewport.height}-${colorScheme}-gallery.png`) });
    const frame = await page.locator("body").evaluateHandle(() => {
      const iframe = document.createElement("iframe");
      iframe.src = "/g/456/child/";
      document.body.append(iframe);
      return iframe;
    });
    const child = await frame.asElement().contentFrame();
    await child.waitForLoadState();
    await child.addScriptTag({ path: scriptPath });
    assert.equal(await child.locator("#review-keyboard-hints").count(), 0, "script ran in a child frame");
    for (const url of ["https://exhentai.org/", "https://exhentai.org/g/not-a-gallery", "https://example.test/g/123/fixture/", "http://exhentai.org/g/123/fixture/"]) {
      await page.goto(url);
      await page.addScriptTag({ path: scriptPath });
      assert.equal(await host.count(), 0, `out-of-scope injection: ${url}`);
    }
    assert.deepEqual(errors, []);
    return { viewport, colorScheme, passed: true };
  } finally {
    await context.close();
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 480, height: 390 }, { width: 320, height: 400 }]) {
    for (const colorScheme of ["dark", "light"]) results.push(await scenario(viewport, colorScheme));
  }
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
