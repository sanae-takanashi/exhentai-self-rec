// Live metadata display checks. Only static files and local-page GETs reach production.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");

const [manifestPath, outputPath, origin = "http://127.0.0.1:18787"] = process.argv.slice(2);
assert(manifestPath && outputPath);
const base = new URL(origin);
assert(base.protocol === "http:" && base.hostname === "127.0.0.1" && base.pathname === "/");
assert(!base.username && !base.password && !base.search && !base.hash);
assert(!fs.existsSync(outputPath));
const { targets } = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
assert(Array.isArray(targets) && targets.length > 0);
let browser, stage = "launch";

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    stage = "catalog";
    const context = await browser.newContext({ viewport });
    const response = await context.request.get(new URL("/api/local-pages", base).href);
    assert(response.ok());
    const catalog = await response.json();
    const byKey = new Map(catalog.items.map((item) => [item.key, item]));
    for (const target of targets) {
      const item = byKey.get(target.key);
      assert(item && item.metadata_status === "matched" && item.url === target.url);
    }
    const errors = [], forwarded = [];
    const page = await context.newPage();
    page.on("pageerror", () => errors.push(true));
    await context.route("**/*", (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const reply = (value) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
      if (url.origin !== base.origin) return route.abort();
      if (request.method() !== "GET") return reply({ ok: true });
      if (url.pathname === "/api/settings") return reply({ bootstrap_tags: [], visual_encoder: "simple" });
      if (url.pathname === "/api/recommendations") return reply({ items: [], total: 0, has_more: false });
      if (url.pathname === "/" || url.pathname.startsWith("/static/") ||
          ["/api/local-pages", "/api/local-pages/compare", "/api/local-pages/image"].includes(url.pathname)) {
        forwarded.push(request.method());
        return route.continue();
      }
      return reply({});
    });
    try {
      stage = "review_local_pages_entry";
      await page.goto(base.href);
      await page.locator("#localPagesBtn").click();
      await page.locator("#localPagesDialog[open]").waitFor();
      for (const target of targets) {
        stage = "restored_title_link_and_images";
        const item = byKey.get(target.key);
        await page.selectOption("#localPagesSelect", target.key);
        await page.waitForFunction(({ key, title, url }) => {
          const link = document.querySelector("#localPagesContent > h3 a");
          return document.querySelector("#localPagesSelect").value === key && link?.textContent === title &&
            link.getAttribute("href") === url && document.querySelector("#localPagesStatus").textContent.startsWith("Metadata matched");
        }, { key: target.key, title: item.title, url: item.url });
        const link = page.locator("#localPagesContent > h3 a");
        assert.equal(await link.getAttribute("target"), "_blank");
        assert.equal(await link.getAttribute("rel"), "noreferrer");
        assert.equal(await page.locator(".local-source-pages img").count(), item.pages.length);
        await page.locator(".local-source-pages img").first().scrollIntoViewIfNeeded();
        await page.waitForFunction(() => [...document.querySelectorAll(".local-source-pages img")]
          .every((image) => image.complete && image.naturalWidth > 0));
        assert(await page.locator("#localPagesDialog").evaluate((element) => element.scrollWidth <= element.clientWidth + 1));
      }
      assert.equal(errors.length, 0);
      assert(forwarded.every((method) => method === "GET"));
      results.push({ width: viewport.width, height: viewport.height, restored_displayed: targets.length,
                     titles_and_links_match: true, images_loaded: true, no_horizontal_overflow: true,
                     forwarded_get_only: true, page_errors: 0 });
    } finally {
      await context.close();
    }
  }
  const result = { ok: true, restored: targets.length, viewports: results, no_production_mutations: true };
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2), { flag: "wx" });
  console.log(JSON.stringify({ ok: true, restored: targets.length, viewports: results.length, no_production_mutations: true }));
})().catch(() => {
  console.log(JSON.stringify({ ok: false, stage }));
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
