// Synthetic UI regressions plus optional GET-only loopback preview verification.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `local-pages-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
let browser;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function scenario(viewport) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(() => sessionStorage.setItem("review-layout", "focus"));
  const page = await context.newPage();
  const raster = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 300; canvas.height = 440;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#487c69"; ctx.fillRect(0, 0, 300, 440);
    ctx.fillStyle = "#efb361"; ctx.fillRect(25, 40, 220, 180);
    ctx.fillStyle = "#d9677a"; ctx.fillRect(95, 190, 180, 180);
    ctx.fillStyle = "#ffffff"; ctx.font = "bold 24px sans-serif";
    ctx.fillText("LOCAL FIXTURE", 20, 410);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const image = { src: `/api/local-pages/image?id=${"a".repeat(64)}`, width: 300, height: 440 };
  const local = [1, 2, 3, 4].map((id) => ({
    key: `${id}-1280`, gid: String(id), resolution: "1280", image_count: 30, ordering: "filename_lexical",
    title: id === 4 ? "LongUnbrokenGalleryTitle".repeat(16) : `Local fixture ${id}`,
    url: id === 3 ? null : `https://exhentai.org/g/${id}/abc/`, metadata_status: id === 3 ? "missing" : "matched",
    cover: id === 3 ? null : image, comparable: id !== 3,
    pages: [1, 16, 30].map((position) => ({ ...image, position })),
  }));
  const review = local.filter((item) => item.url).map((item) => ({ ...item, samples: item.gid === "2" ? ["site-sample"] : [],
    thumb_url: item.gid === "2" ? "https://s.exhentai.org/broken.jpg" : null, tags: [], reasons: [],
    visual_embedding_at: "fixture", visual_embedding_version: "canvas-rgb-8x8-v1" }));
  let catalogMode = "ready", holdKey = null, held = [], failedComparison = false, visibleEvents = 0;
  const errors = [], mutations = [], externals = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const comparison = (key) => {
    const item = local.find((item) => item.key === key);
    const candidates = local.filter((candidate) => candidate.comparable && candidate.key !== key);
    return { item, comparable: item.comparable, candidate_count: candidates.length,
      cover: item.comparable ? candidates.map((candidate) => ({ ...candidate, cosine: .91 })) : [],
      body: item.comparable ? candidates.toReversed().map((candidate) => ({ ...candidate, cosine: .83 })) : [] };
  };
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== "local.test") {
      externals.push(url.hostname);
      return route.abort();
    }
    if (url.pathname === "/" || url.pathname.startsWith("/static/")) {
      const name = url.pathname === "/" ? "index.html" : url.pathname.slice(8);
      assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
      return route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html", body: fs.readFileSync(path.join(root, "static", name)) });
    }
    if (route.request().method() !== "GET") {
      if (url.pathname === "/api/impressions/visible") visibleEvents++;
      if (!["/api/impressions", "/api/impressions/visible"].includes(url.pathname)) mutations.push(url.pathname);
      return response(route, { ok: true });
    }
    if (url.pathname === "/api/local-pages") return response(route, catalogMode === "ready"
      ? { available: true, items: local, comparable_count: 3, collected_at: "2026-10-31" }
      : { available: false, reason: catalogMode, items: [] });
    if (url.pathname === "/api/local-pages/compare") {
      const key = url.searchParams.get("key");
      if (key === holdKey) { held.push({ route, key }); return; }
      if (failedComparison) return response(route, { error: "fixture failure" }, 500);
      return response(route, comparison(key));
    }
    if (url.pathname === "/api/local-pages/image") return url.searchParams.get("id") === "missing"
      ? route.fulfill({ status: 404, contentType: "text/plain", body: "missing fixture" })
      : route.fulfill({ contentType: "image/png", headers: { "cache-control": "no-store" }, body: Buffer.from(raster, "base64") });
    if (url.pathname === "/thumb") return route.fulfill({ status: 404, body: "fixture missing" });
    if (url.pathname === "/api/settings") return response(route, { bootstrap_tags: [], visual_encoder: "simple" });
    if (url.pathname === "/api/recommendations") return response(route, { request_id: "fixture", items: review, total: review.length, has_more: false, next_offset: review.length });
    return response(route, {});
  });
  try {
    await page.goto("http://local.test/");
    await page.locator(".focus-card .thumb img").waitFor();
    const current = () => page.locator(".focus-card").getAttribute("data-gallery-url");
    const first = await current();
    await page.waitForFunction(() => document.querySelector(".thumb img").naturalWidth > 0);
    assert(await page.evaluate(() => renderedGalleryItems.every((item) => visualImageSources(item).every((src) => !src.includes("/api/local-pages/")))), "local fallback leaked into production visual inputs");
    assert.match(await page.locator(".local-media-status").innerText(), /Local file/);
    assert.equal(await page.locator("a.thumb").getAttribute("target"), "_blank");
    assert.equal(await page.locator("a.thumb").getAttribute("rel"), "noreferrer");
    await page.keyboard.press("j");
    await page.waitForFunction(() => document.querySelector(".thumb img")?.src.includes("/api/local-pages/image"));
    await page.waitForFunction(() => document.querySelector(".samples img")?.src.includes("/api/local-pages/image"));
    await page.keyboard.press("k");
    assert.equal(await current(), first);
    const footer = await page.locator(".focus-rating").boundingBox();
    const body = await page.locator(".focus-card .body").boundingBox();
    assert(body.y + body.height <= footer.y + 1, "local tools overlap rating footer");
    await page.screenshot({ path: path.join(output, `${viewport.width}-fallback.png`) });
    await page.locator("[data-local-compare]").click();
    await page.locator(".local-comparison").waitFor();
    const beforeDwell = visibleEvents;
    await pause(1100);
    assert.equal(visibleEvents, beforeDwell, "occluded Review card acquired a visible impression");
    await page.keyboard.press("5");
    assert.equal(await current(), first, "modal key rated gallery");
    assert.equal(await page.locator(".local-comparison section").count(), 2);
    assert.equal(await page.locator(".local-comparison li").count(), 4);
    assert(await page.locator("#localPagesContent h3 a").first().getAttribute("href"));
    assert(await page.locator("#localPagesDialog").evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "modal horizontal overflow");
    for (const button of await page.locator(".local-result-title").all()) {
      assert(await button.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "long result title overflow");
    }
    await page.screenshot({ path: path.join(output, `${viewport.width}-comparison.png`) });
    await page.locator(".local-source-pages button").first().click();
    await page.waitForFunction(() => document.querySelector("#localImage").naturalWidth > 0);
    assert(await page.locator("#localImageDialog").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
    await page.keyboard.press("Escape");
    await page.selectOption("#localPagesSelect", "3-1280");
    await page.locator("#localPagesStatus").filter({ hasText: "Metadata missing" }).waitFor();
    assert.equal(await page.locator("#localPagesContent a").count(), 0, "fabricated unknown gallery URL");
    assert.equal(await page.locator(".local-source-pages img").count(), 3);
    assert.equal(await page.locator(".local-comparison").count(), 0);
    holdKey = "1-1280";
    await page.selectOption("#localPagesSelect", holdKey);
    await page.waitForFunction(() => document.querySelector("#localPagesStatus").textContent.includes("Loading"));
    await page.selectOption("#localPagesSelect", "2-1280");
    await page.locator("#localPagesContent h3").first().filter({ hasText: "Local fixture 2" }).waitFor();
    assert.equal(held.length, 1);
    await response(held[0].route, comparison(held[0].key));
    await pause(50);
    assert.equal(await page.locator("#localPagesContent h3").first().innerText(), "Local fixture 2");
    holdKey = null;
    failedComparison = true;
    await page.selectOption("#localPagesSelect", "1-1280");
    await page.locator("#localPagesStatus").filter({ hasText: "Comparison unavailable" }).waitFor();
    failedComparison = false;
    await page.locator("#localPagesRetry").click();
    await page.locator(".local-comparison").waitFor();
    await page.evaluate(() => inspectLocalImage("/api/local-pages/image?id=missing", "Missing fixture"));
    await page.locator("#localImageStatus").filter({ hasText: "Local image unavailable" }).waitFor();
    await page.keyboard.press("Escape");
    catalogMode = "invalid_library";
    await page.locator("#localPagesRetry").click();
    await page.locator("#localPagesStatus").filter({ hasText: "validation failed" }).waitFor();
    assert(await page.locator("#localPagesSelect").isDisabled());
    catalogMode = "not_configured";
    await page.locator("#localPagesRetry").click();
    await page.locator("#localPagesStatus").filter({ hasText: "No local page library" }).waitFor();
    catalogMode = "ready";
    await page.locator("#localPagesRetry").click();
    await page.locator(".local-comparison").waitFor();
    await page.locator("#localPagesClose").click();
    assert.equal(await current(), first);
    assert(await page.evaluate(() => {
      const previousView = currentView;
      const image = document.querySelector(".focus-card .thumb img");
      currentView = "favorites";
      image.dispatchEvent(new Event("error"));
      currentView = previousView;
      return !image.hidden;
    }), "Review fallback altered another view's image failure behavior");
    assert.deepEqual(mutations, []);
    assert.deepEqual(externals, []);
    assert.deepEqual(errors, []);
    return { viewport, passed: true, no_mutations: true, no_external_network: true };
  } finally { await context.close(); }
}

async function live() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [], requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, "http://127.0.0.1:18788");
    requests.push(route.request().method());
    return route.continue();
  });
  try {
    await page.goto("http://127.0.0.1:18788/");
    await page.locator("#localPagesDialog[open]").waitFor();
    await page.locator(".local-source-pages img").first().waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll(".local-source-pages img")].every((img) => img.naturalWidth > 0));
    const catalog = await (await page.request.get("http://127.0.0.1:18788/api/local-pages")).json();
    assert.equal(catalog.items.length, 59);
    assert.equal(catalog.comparable_count, 43);
    const paired = catalog.items.find((item) => item.comparable);
    await page.selectOption("#localPagesSelect", paired.key);
    await page.locator(".local-comparison").waitFor();
    const matched = await page.locator(".local-comparison li").count();
    assert.equal(matched, 6);
    await page.waitForFunction(() => [...document.querySelectorAll(".local-comparison img")].every((img) => img.naturalWidth > 0));
    await page.screenshot({ path: path.join(output, "live-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.locator("#localPagesDialog").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
    await page.screenshot({ path: path.join(output, "live-mobile.png") });
    assert(requests.every((method) => method === "GET"));
    assert.deepEqual(errors, []);
    return { passed: true, real_galleries: catalog.items.length, comparable: catalog.comparable_count, all_images_loaded: true, get_only: true };
  } finally { await context.close(); }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const synthetic = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 568 }]) synthetic.push(await scenario(viewport));
  const result = { synthetic, live: process.argv.includes("--live") ? await live() : null };
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: synthetic.length, live: result.live, evidence: output }));
})().catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
