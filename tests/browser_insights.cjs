// Fully intercepted synthetic browser checks: no live server, profile or requests.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `insights-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const hostile = '<img src=x onerror="window.syntheticXss=true">';
let browser;

async function harness(viewport, handler) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [], requests = [], routeErrors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const raster = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 240; canvas.height = 320;
    const paint = canvas.getContext("2d");
    paint.fillStyle = "#3a9c77"; paint.fillRect(0, 0, 240, 320);
    paint.fillStyle = "#f3cf70"; paint.fillRect(25, 45, 190, 180);
    paint.fillStyle = "#111"; paint.font = "22px sans-serif";
    paint.fillText("LOCAL FIXTURE", 28, 270);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const json = (route, data, status = 200) => route.fulfill({ status,
    contentType: "application/json", body: JSON.stringify(data) });
  await context.route("**/*", async (route) => {
    try {
      const request = route.request(), url = new URL(request.url());
      assert.equal(url.origin, "http://insights.test", "unexpected external request");
      if (url.pathname.startsWith("/static/")) {
        const filename = url.pathname.slice(8);
        assert(/^(fetch-insights|ranking-insights|page-search|preferences)\.(html|js|css)$/.test(filename), filename);
        return await route.fulfill({ contentType: filename.endsWith(".js") ? "text/javascript"
          : filename.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", filename)) });
      }
      if (url.pathname === "/api/local-pages/image") return await route.fulfill({
        contentType: "image/png", body: Buffer.from(raster, "base64") });
      requests.push({ path: url.pathname, method: request.method(), query: Object.fromEntries(url.searchParams),
        body: request.postData() ? request.postDataJSON() : null });
      await handler({ route, request, url, json, requests });
    } catch (error) {
      routeErrors.push(error.message);
      await route.fulfill({ status: 500, body: "Synthetic route assertion failed" });
    }
  });
  return { context, page, errors, requests, json,
    async check() {
      assert.deepEqual(routeErrors, []);
      assert.deepEqual(errors, []);
      assert.equal(await page.evaluate(() => Boolean(window.syntheticXss)), false);
    },
    async screenshot(name) {
      await page.screenshot({ path: path.join(output, `${viewport.width}-${name}.png`), fullPage: true });
    },
    async layout() {
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "document overflows viewport");
      const boxes = await page.locator("button, input, select").evaluateAll((nodes) => nodes.filter((n) => n.getClientRects().length)
        .map((n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right }; }));
      for (const box of boxes) assert(box.left >= -1 && box.right <= viewport.width + 1, "control overflows viewport");
    }
  };
}

function fetchItem(label, overrides = {}) {
  return { label, sources: ["synthetic"], listing_operations: 1, completed_operations: 1,
    failed_operations: 0, unfinished_operations: 0, returned_occurrences: 4, admitted_occurrences: 2,
    existing_occurrence_rate: null, recorded_galleries: 2, new_galleries: 2, existing_galleries: 0,
    shared_galleries: 0, positive: 0, negative: 0, neutral: 0, unknown: 2, duplicate_update: 0,
    positive_rate_explicit: null, explicit_nonneutral: 0, new_positive_per_completed_operation: null,
    new_positive: 0, visible_positive: 0, visible_galleries: 0, visible_positive_rate: null,
    inferred_historical_galleries: 3, ...overrides };
}

async function fetchScenario(viewport) {
  let fail = true, empty = false;
  const test = await harness(viewport, async ({ route, request, url, json }) => {
    assert.equal(request.method(), "GET"); assert.equal(url.pathname, "/api/insights/fetch");
    return json(route, fail ? {} : { schema: "fetch-insights-v1", recorded_unique_galleries: 2,
      recorded_query_gallery_pairs: 3, measurement_started_at: null,
      items: empty ? [] : [fetchItem(hostile), fetchItem("Zero denominator with outcomes", {
        positive: 0, negative: 2, unknown: 0, explicit_nonneutral: 2, positive_rate_explicit: 0,
        visible_positive_rate: 0, visible_galleries: 2, new_positive_per_completed_operation: 0
      })] }, fail ? 503 : 200);
  });
  try {
    const { page } = test;
    await page.goto("http://insights.test/static/fetch-insights.html");
    await page.waitForFunction(() => document.querySelector("#status").classList.contains("error"));
    assert(await page.locator("#refresh").isEnabled());
    fail = false; await page.locator("#refresh").click();
    await page.locator("#rows tr").first().waitFor();
    assert.equal(await page.locator("#rows tr").count(), 2);
    const unknown = page.locator("#rows tr").filter({ hasText: hostile });
    assert.equal(await unknown.locator("td").nth(6).innerText(), "—\n明确样本 0");
    assert.equal(await unknown.locator("td").nth(7).innerText(), "—\n0 新增好评");
    assert((await unknown.innerText()).includes("2 / 0"));
    const zero = page.locator("#rows tr").filter({ hasText: "Zero denominator with outcomes" });
    assert.equal(await zero.locator("td").nth(6).innerText(), "0.0%\n明确样本 2");
    assert.equal(await page.locator("#rows img").count(), 0);
    await test.layout(); await test.screenshot("fetch");
    await page.locator("#filter").fill("not-a-query");
    assert(await page.locator("#empty").isVisible());
    await page.locator("#filter").fill("");
    fail = true; await page.locator("#refresh").click();
    await page.waitForFunction(() => document.querySelector("#status").classList.contains("error"));
    assert.equal(await page.locator("#rows tr").count(), 2, "stale report retained with warning");
    fail = false; empty = true; await page.locator("#refresh").click();
    await page.waitForFunction(() => document.querySelector("#rows").children.length === 0);
    assert((await page.locator("#empty").innerText()).includes("暂无采集记录"));
    await test.check();
    return { page: "fetch", width: viewport.width, requests: test.requests.length, passed: true };
  } finally { await test.context.close(); }
}

function rankingReport(cohorts, query) {
  return { schema: "ranking-insights-v1", window: { start_utc: "2026-10-06", end_utc: "2026-11-05", surface: query.surface },
    summary: { delivered_galleries: 3, visible_galleries: 0, latest_feedback_galleries: 1,
      unknown_attribution_galleries: 1, unknown_attribution_reasons: { "missing-serving-identity": 1 } },
    review_milestone: { window_attributed_explicit_galleries: 0, target: 100, review_due: false },
    definitions: [hostile, "Unknown preferences are not negatives."], cohorts };
}

async function rankingScenario(viewport) {
  let fail = true, empty = false;
  const cohort = { surface: "review", policy: "legacy", model_version: hostile, artifact_id: "fixture-artifact",
    trained_at: "Unknown", delivered_galleries: 3, delivered_events: 4, visible_galleries: 0,
    visible_events: 0, positive: 0, negative: 0, neutral: 0, unknown_preference_galleries: 3,
    excluded_duplicate_update: 0, invalid_outcome: 0, positive_fraction_of_explicit: null,
    observed_positive_per_100_visible: null, fallback_events: 1, visibility_uninstrumented_events: 2,
    negative_reasons: {} };
  const test = await harness(viewport, async ({ route, request, url, json }) => {
    assert.equal(request.method(), "GET"); assert.equal(url.pathname, "/api/insights/ranking");
    return json(route, fail ? {} : rankingReport(empty ? [] : [cohort, { ...cohort, policy: "personalized_trial",
      model_version: "v5-fixture", positive_fraction_of_explicit: 0,
      observed_positive_per_100_visible: 0 }], Object.fromEntries(url.searchParams)), fail ? 503 : 200);
  });
  try {
    const { page } = test;
    await page.goto("http://insights.test/static/ranking-insights.html");
    await page.waitForFunction(() => document.querySelector("#status").textContent.includes("Unable"));
    assert(await page.locator("#report").isHidden());
    fail = false; await page.locator("#filters button").click();
    await page.locator("#report").waitFor();
    const cards = page.locator(".cohort");
    assert.equal(await cards.count(), 2);
    assert((await cards.first().innerText()).includes("No denominator"));
    assert((await cards.nth(1).innerText()).includes("0.0%"));
    assert((await page.locator("#milestone").innerText()).includes("never activates a model"));
    assert.equal(await page.locator("#cohorts img").count(), 0);
    await page.locator("#surface").selectOption("review");
    await page.locator("#days").selectOption("7");
    await page.locator("#filters button").click();
    await page.waitForFunction(() => document.querySelector("#window").textContent.endsWith("review"));
    assert.deepEqual(test.requests.at(-1).query, { days: "7", surface: "review" });
    await test.layout(); await test.screenshot("ranking");
    empty = true; await page.locator("#filters button").click();
    await page.waitForFunction(() => document.querySelector("#cohorts").textContent.includes("No delivery cohorts"));
    fail = true; await page.locator("#filters button").click();
    await page.waitForFunction(() => document.querySelector("#status").textContent.includes("Unable"));
    assert(await page.locator("#report").isHidden());
    await test.check();
    return { page: "ranking", width: viewport.width, requests: test.requests.length, passed: true };
  } finally { await test.context.close(); }
}

async function pageSearchScenario(viewport) {
  let failCatalog = true, failSearch = false, available = true;
  const image = (key, position) => `/api/local-pages/image?key=${key}&position=${position}`;
  const items = [
    { key: "gallery-a", title: hostile, resolution: "1280x", image_count: 60,
      pages: [1, 8, 30].map((position) => ({ position, searchable: position !== 30, src: image("gallery-a", position) })) },
    { key: "gallery-b", title: "Actual matched page", resolution: "original", image_count: 90,
      pages: [4, 22].map((position) => ({ position, searchable: true, src: image("gallery-b", position) })) }
  ];
  const test = await harness(viewport, async ({ route, request, url, json }) => {
    assert.equal(request.method(), "GET");
    if (url.pathname === "/api/local-pages") return json(route, failCatalog ? {} : {
      available: true, items, collected_at: "2026-11-05", page_search: { available, indexed_pages: 4, reason: "rebuild_required" }
    }, failCatalog ? 503 : 200);
    assert.equal(url.pathname, "/api/local-pages/search");
    assert.equal(url.searchParams.get("key"), "gallery-a");
    assert.equal(url.searchParams.get("position"), "8");
    assert.equal(url.searchParams.get("limit"), "12");
    return json(route, failSearch ? {} : { available: true, query: { item: items[0], page: items[0].pages[1] },
      items: [{ item: items[1], page: items[1].pages[1], cosine: 0.8321 }], candidate_count: 1, indexed_pages: 4 }, failSearch ? 503 : 200);
  });
  try {
    const { page } = test;
    await page.goto("http://insights.test/static/page-search.html");
    await page.waitForFunction(() => document.querySelector("#catalog-status").classList.contains("error"));
    assert(await page.locator("#search").isDisabled());
    failCatalog = false; await page.locator("#reload").click();
    await page.locator("#query .card").waitFor();
    assert.equal(await page.locator("#gallery option").count(), 2);
    assert.equal(await page.locator("#page option").count(), 3);
    await page.locator("#page").selectOption("30");
    assert(await page.locator("#search").isDisabled());
    assert((await page.locator("#search-status").innerText()).includes("没有向量"));
    await page.locator("#gallery").selectOption("gallery-b");
    assert.equal(await page.locator("#page option").count(), 2);
    assert.equal(await page.locator("#query img").getAttribute("src"), image("gallery-b", 4));
    await page.locator("#gallery").selectOption("gallery-a");
    await page.locator("#page").selectOption("8");
    failSearch = true; await page.locator("#search").click();
    await page.waitForFunction(() => document.querySelector("#search-status").classList.contains("error"));
    assert(await page.locator("#search").isEnabled());
    failSearch = false; await page.locator("#search").click();
    await page.locator("#results .card").waitFor();
    assert.equal(await page.locator("#query img").getAttribute("src"), image("gallery-a", 8));
    assert.equal(await page.locator("#results img").getAttribute("src"), image("gallery-b", 22));
    assert((await page.locator("#results").innerText()).includes("第 22 / 90 页"));
    assert((await page.locator("#results").innerText()).includes("0.8321（不是概率）"));
    assert.equal(await page.locator("#query h3").innerText(), hostile);
    assert.equal(await page.locator("#query img").count(), 1);
    await page.waitForFunction(() => [...document.querySelectorAll(".card img")].every((n) => n.naturalWidth > 0));
    await test.layout(); await test.screenshot("pages");
    await page.locator("#page").selectOption("1");
    assert.equal(await page.locator("#results .card").count(), 0, "edited query invalidates results");
    available = false; await page.locator("#reload").click();
    await page.waitForFunction(() => document.querySelector("#catalog-status").textContent.includes("没有单页向量"));
    assert(await page.locator("#search").isDisabled());
    assert.equal(await page.locator("#query .card").count(), 1, "legacy catalog stays previewable");
    await test.check();
    return { page: "page-search", width: viewport.width, requests: test.requests.length, passed: true };
  } finally { await test.context.close(); }
}

async function preferencesScenario(viewport) {
  let preferences = [{ tag: "artist:fixture", weight: 2 }];
  let conflict = false, mismatch = false, failLoad = false, previewNumber = 0;
  const test = await harness(viewport, async ({ route, request, url, json }) => {
    if (url.pathname === "/api/preferences") {
      assert.equal(request.method(), "GET");
      return json(route, failLoad ? { error: "Synthetic unavailable" } : { preferences,
        learned_features: [{ feature: hostile, weight: 0, positive_count: 0, negative_count: 0 }] }, failLoad ? 503 : 200);
    }
    assert.equal(request.method(), "POST");
    const body = request.postDataJSON();
    if (url.pathname === "/api/preferences/preview") {
      assert.deepEqual(Object.keys(body).sort(), ["action", "tag", "weight"]);
      const previous = preferences.find((item) => item.tag === body.tag)?.weight ?? null;
      return json(route, { preview_id: `synthetic-preview-${++previewNumber}`, proposal: body,
        previous_weight: previous, previous_legacy_matching_contribution: previous ?? 0,
        proposed_legacy_matching_contribution: body.weight ?? 0,
        legacy_matching_score_delta: (body.weight ?? 0) - (previous ?? 0),
        counts: { matched: 3, positive: 0, negative: 0, neutral: 0, unknown: 3, duplicate_update: 0 },
        note: "Synthetic metadata matches; unrated remains unknown.", examples: [{ title: hostile, outcome: "unknown" }] });
    }
    assert.equal(url.pathname, "/api/preferences/apply");
    assert.deepEqual(Object.keys(body).sort(), ["action", "preview_id", "tag", "weight"]);
    assert(body.preview_id.startsWith("synthetic-preview-"));
    if (conflict) return json(route, { error: "Synthetic conflict" }, 409);
    if (!mismatch) {
      preferences = preferences.filter((item) => item.tag !== body.tag);
      if (body.action !== "remove") preferences.push({ tag: body.tag, weight: body.weight });
    }
    return json(route, { ok: true });
  });
  try {
    const { page } = test;
    const ready = (text) => page.waitForFunction((value) => document.querySelector("#preferencesStatus").textContent.includes(value), text);
    const preview = async () => {
      await page.locator("#previewPreferenceBtn").click();
      await ready("Preview ready");
      assert(await page.locator("#applyPreferenceBtn").isEnabled());
    };
    await page.goto("http://insights.test/static/preferences.html");
    await ready("Preferences loaded");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    assert.equal(await page.locator("#learnedFeatures img").count(), 0);
    await page.locator("#preferenceSelect").selectOption("artist:fixture");
    assert.equal(await page.locator("#preferenceWeight").inputValue(), "2");
    await page.locator("#preferenceWeight").fill("0");
    await preview();
    assert((await page.locator("#preferencePreview").innerText()).includes("3 unrated"));
    assert.equal(test.requests.filter((request) => request.path.endsWith("/apply")).length, 0, "preview is not apply");
    await page.locator("#preferenceWeight").fill("-1");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    assert.equal(await page.locator("#preferencePreview").innerText(), "");
    await page.locator("#preferenceWeight").fill("0");
    await preview();
    const beforeApply = test.requests.length;
    await page.locator("#applyPreferenceBtn").click();
    await ready("independently read back");
    assert.deepEqual(test.requests.slice(beforeApply).map((request) => `${request.method} ${request.path}`),
      ["POST /api/preferences/apply", "GET /api/preferences"]);
    assert((await page.locator("#explicitPreferences").innerText()).includes("Paused"));
    assert.equal(await page.locator("#learnedFeatures tr").count(), 1, "explicit pause retains learned evidence");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    await page.locator("#restorePreferenceBtn").click();
    assert.equal(await page.locator("#preferenceWeight").inputValue(), "2");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    await preview();
    await test.layout(); await test.screenshot("preferences");
    await page.locator("#preferenceTag").fill("artist:edited");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    await page.locator("#preferenceSelect").selectOption("artist:fixture");
    await preview();
    await page.locator("#preferenceAction").selectOption("remove");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    assert(await page.locator("#preferenceWeight").isDisabled());
    await preview();
    conflict = true;
    await page.locator("#applyPreferenceBtn").click();
    await ready("Preview again before applying");
    await page.waitForFunction(() => document.querySelector("#preferenceForm").getAttribute("aria-busy") === "false");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    assert(await page.locator("#restorePreferenceBtn").isDisabled());
    conflict = false; mismatch = true;
    await preview(); await page.locator("#applyPreferenceBtn").click();
    await ready("verification failed");
    await page.waitForFunction(() => document.querySelector("#preferenceForm").getAttribute("aria-busy") === "false");
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    assert(await page.locator("#restorePreferenceBtn").isDisabled());
    mismatch = false;
    await preview(); await page.locator("#applyPreferenceBtn").click();
    await ready("independently read back");
    assert.equal(preferences.length, 0);
    assert((await page.locator("#explicitPreferences").innerText()).includes("No explicit preferences"));
    failLoad = true; await page.reload(); await ready("Could not load preferences");
    assert(await page.locator("#previewPreferenceBtn").isDisabled());
    assert(await page.locator("#applyPreferenceBtn").isDisabled());
    failLoad = false; await page.reload(); await ready("Preferences loaded");
    await test.check();
    return { page: "preferences", width: viewport.width, requests: test.requests.length, passed: true };
  } finally { await test.context.close(); }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 568 }]) {
    for (const scenario of [fetchScenario, rankingScenario, pageSearchScenario, preferencesScenario]) results.push(await scenario(viewport));
  }
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
