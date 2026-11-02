// Synthetic startup checks: all requests are intercepted, including mutations.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `startup-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let browser;
const settings = { bootstrap_tags: [], visual_encoder: "simple", recommend_language_filter: "japanese",
  recommend_model_mode: "legacy", review_require_bootstrap_match: false };
const item = { url: "https://exhentai.org/g/1000/abc/", title: "Startup fixture", tags: [], reasons: [],
  samples: [], score: 1, category: "Fixture", visual_embedding_at: "fixture", visual_embedding_version: "canvas-rgb-8x8-v1" };
const listing = (id = 23) => ({ request_id: `fixture-${id}`, items: [item], total: 1, has_more: false,
  last_fetch: { id }, next_offset: 1 });
const status = (id = 23) => ({ fetch: { running: false }, last_fetch: { id }, settings: {} });
const reply = (route, value, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(value) });

async function staticRoute(route, appJs = null) {
  const url = new URL(route.request().url());
  assert.equal(url.hostname, "review-startup.test");
  if (url.pathname !== "/" && !url.pathname.startsWith("/static/")) return false;
  const name = url.pathname === "/" ? "index.html" : url.pathname.slice(8);
  assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
  await route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
    body: name === "app.js" && appJs !== null ? appJs : fs.readFileSync(path.join(root, "static", name)) });
  return true;
}

async function scenario(viewport, layout, failedStatus = false, failedCatalog = false) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript((value) => sessionStorage.setItem("review-layout", value), layout);
  const page = await context.newPage();
  let settingsRoute, catalogRoute, recommendationRoute, statusRoute;
  let listCalls = 0, statusId = 23, holding = true;
  const paths = [], errors = [];
  page.on("pageerror", () => errors.push(true));
  await context.route("**/*", async (route) => {
    if (await staticRoute(route)) return;
    const url = new URL(route.request().url());
    paths.push(url.pathname);
    if (url.pathname === "/api/settings") { settingsRoute = route; return; }
    if (url.pathname === "/api/local-pages") { catalogRoute = route; return; }
    if (url.pathname === "/api/recommendations") {
      listCalls++;
      assert.equal(url.searchParams.get("language_filter"), "japanese");
      assert.equal(url.searchParams.get("model_mode"), "legacy");
      assert.equal(url.searchParams.get("require_bootstrap_match"), "0");
      if (holding) { recommendationRoute = route; return; }
      return reply(route, listing(statusId));
    }
    if (url.pathname === "/api/status") {
      if (holding) { statusRoute = route; return; }
      return reply(route, status(statusId));
    }
    return reply(route, {});
  });
  try {
    await page.goto("http://review-startup.test/");
    const until = async (check) => {
      for (let i = 0; i < 200; i++) { if (check()) return; await pause(10); }
      assert.fail("Startup request did not arrive");
    };
    await until(() => settingsRoute && catalogRoute);
    assert.equal(listCalls, 0, "recommendations must use saved settings");
    await reply(catalogRoute, failedCatalog ? { error: "Fixture unavailable" } : { available: false, items: [] }, failedCatalog ? 500 : 200);
    await reply(settingsRoute, settings);
    await until(() => recommendationRoute);
    assert(!paths.includes("/api/status"), "status delayed the first page");
    assert(!paths.includes("/api/queue-counts"), "counts compete with the first page");
    assert(!paths.includes("/api/integrations/hath/status"), "H@H competes with the first page");
    await page.evaluate(() => reloadRecommendationsAfterFetch({ fetch: {}, last_fetch: { id: 23 } }));
    assert.equal(listCalls, 1, "polling duplicated the in-flight startup request");
    await reply(recommendationRoute, listing());
    await page.locator("article .title").waitFor();
    assert.equal(await page.locator("article .title").innerText(), item.title);
    await until(() => statusRoute);
    assert.equal(listCalls, 1, "startup requested recommendations twice");
    holding = false;
    await reply(statusRoute, failedStatus ? { error: "Fixture status failure" } : status(), failedStatus ? 500 : 200);
    await page.evaluate(() => loadStatus());
    assert.equal(listCalls, 1, "last completed fetch caused redundant startup rendering");
    assert.equal(errors.length, 0);
    await page.screenshot({ path: path.join(output, `${viewport.width}-${layout}-${failedStatus}-${failedCatalog}.png`) });
    statusId = 24;
    await page.evaluate(() => loadStatus());
    assert.equal(listCalls, layout === "focus" ? 1 : 2, "post-startup fetch refresh behavior changed");
    return { viewport, layout, failed_status: failedStatus, failed_catalog: failedCatalog, startup_list_calls: 1,
      renders_before_status: true, polling_preserved: true, page_errors: errors.length };
  } finally { await context.close(); }
}

async function benchmark(appJs) {
  const context = await browser.newContext();
  const page = await context.newPage();
  let calls = 0;
  await context.route("**/*", async (route) => {
    if (await staticRoute(route, appJs)) return;
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings") { await pause(100); return reply(route, settings); }
    if (url.pathname === "/api/status") { await pause(800); return reply(route, status()); }
    if (url.pathname === "/api/local-pages") { await pause(50); return reply(route, { available: false, items: [] }); }
    if (url.pathname === "/api/recommendations") { calls++; await pause(250); return reply(route, listing()); }
    return reply(route, {});
  });
  try {
    const start = performance.now();
    await page.goto("http://review-startup.test/");
    await page.locator("article .title").waitFor();
    const firstRenderMs = Math.round(performance.now() - start);
    await pause(1200);
    return { first_render_ms: firstRenderMs, list_calls: calls };
  } finally { await context.close(); }
}

async function settingsFailure() {
  const context = await browser.newContext();
  const page = await context.newPage();
  let calls = 0;
  await context.route("**/*", async (route) => {
    if (await staticRoute(route)) return;
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings") return reply(route, { error: "Fixture settings failure" }, 500);
    if (url.pathname === "/api/recommendations") calls++;
    if (url.pathname === "/api/status") return reply(route, status());
    return reply(route, {});
  });
  try {
    await page.goto("http://review-startup.test/");
    await page.locator("#status").filter({ hasText: "Fixture settings failure" }).waitFor();
    await page.evaluate(() => loadStatus());
    assert.equal(calls, 0, "failed settings must not serve a default-filter queue");
    return { settings_failure_blocks_default_filters: true };
  } finally { await context.close(); }
}

async function staleStartupResponse() {
  const context = await browser.newContext();
  const page = await context.newPage();
  let held;
  await context.route("**/*", async (route) => {
    if (await staticRoute(route)) return;
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings") return reply(route, settings);
    if (url.pathname === "/api/recommendations") { held = route; return; }
    if (url.pathname === "/api/reactions") return reply(route, { ...listing(), items: [{ ...item, title: "History fixture" }] });
    if (url.pathname === "/api/status") return reply(route, status());
    return reply(route, {});
  });
  try {
    await page.goto("http://review-startup.test/");
    await page.waitForFunction(() => reviewLoading);
    await page.locator('[data-view="history"]').click();
    await page.locator("article .title").filter({ hasText: "History fixture" }).waitFor();
    assert(held);
    await reply(held, listing());
    await page.waitForFunction(() => !initialPageLoading);
    assert.equal(await page.locator("article .title").innerText(), "History fixture");
    return { stale_startup_response_ignored: true };
  } finally { await context.close(); }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    for (const layout of ["grid", "focus"]) results.push(await scenario(viewport, layout));
  }
  results.push(await scenario({ width: 1440, height: 900 }, "grid", true, true));
  results.push(await settingsFailure());
  results.push(await staleStartupResponse());
  const baselineRef = process.argv[2];
  let timing = null;
  if (baselineRef) {
    assert(/^[0-9a-f]{7,40}$/.test(baselineRef));
    const original = execFileSync("git", ["show", `${baselineRef}:static/app.js`], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    const before = await benchmark(original);
    const after = await benchmark(null);
    assert.equal(before.list_calls, 2);
    assert.equal(after.list_calls, 1);
    timing = { before, after };
  }
  const report = { ok: true, scenarios: results, controlled_delays_ms: { settings: 100, status: 800, catalog: 50, recommendations: 250 },
    benchmark: timing, no_production_network: true };
  fs.writeFileSync(path.join(output, "summary.json"), JSON.stringify(report, null, 2), { flag: "wx" });
  console.log(JSON.stringify({ ok: true, scenarios: results.length, benchmark: report.benchmark, output }));
})().catch(() => { console.log(JSON.stringify({ ok: false, output })); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
