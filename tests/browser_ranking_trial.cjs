// Synthetic, fully intercepted Review trial. No production data or networking.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `ranking-trial-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
let browser;

async function scenario(viewport) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(() => sessionStorage.setItem("review-layout", "focus"));
  const page = await context.newPage();
  const errors = [], deliveries = [], writes = [], feedback = [];
  let mode = "legacy", saveState = "ok", heldSave, fallback = false, failLoad = false, lists = 0;
  const items = ["a", "b", "c"].map((name) => ({
    url: `https://example.test/g/${name}/token/`, title: `Synthetic gallery ${name.toUpperCase()}`,
    thumb_url: "https://images.test/fixture.png", tags: ["artist:fixture"], samples: [],
    page_count: 40, category: "Illustration", score: 2, model_version: "legacy-linear-v1",
    like_probability: null, uncertainty: null, confidence: null, served_model: "legacy", ranking_trial: false,
    visual_embedding_version: "canvas-rgb-8x8-v1", visual_embedding_at: "fixture",
  }));
  const raster = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 300; canvas.height = 420;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#70b69e"; ctx.fillRect(0, 0, 300, 420);
    ctx.fillStyle = "#ce657c"; ctx.fillRect(30, 30, 180, 180);
    ctx.fillStyle = "#efe36e"; ctx.fillRect(100, 160, 180, 180);
    ctx.fillStyle = "#202020"; ctx.font = "bold 28px sans-serif";
    ctx.fillText("TRIAL FIXTURE", 25, 385);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const json = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
  const settings = () => ({ bootstrap_tags: [], visual_encoder: "simple", recommend_language_filter: "all",
    recommend_model_mode: "hybrid", review_ranking_mode: mode });
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    assert.equal(url.hostname, "trial.test", "unexpected external request");
    const name = url.pathname === "/" ? "index.html" : url.pathname.slice(8);
    if (url.pathname === "/" || url.pathname.startsWith("/static/")) {
      assert(["index.html", "styles.css", "app.js", "visibility.js"].includes(name));
      return route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", name)) });
    }
    if (url.pathname === "/thumb") return route.fulfill({ contentType: "image/png", body: Buffer.from(raster, "base64") });
    if (url.pathname === "/api/settings") {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        assert.deepEqual(Object.keys(body), ["review_ranking_mode"]);
        writes.push(body);
        if (saveState === "failure") return json(route, { error: "Synthetic setting failure" }, 500);
        if (saveState === "hold") { heldSave = route; return; }
        mode = body.review_ranking_mode;
      }
      return json(route, settings());
    }
    if (url.pathname === "/api/recommendations") {
      lists++;
      if (failLoad) return json(route, { error: "Synthetic load failure" }, 500);
      const active = mode === "personalized_trial" && !fallback && url.searchParams.get("interest_band") === "primary";
      const selected = active ? [...items].reverse() : items;
      const ranked = selected.map((item, index) => ({ ...item, served_rank: index + 1, ranking_requested: mode,
        ranking_policy: active ? "personalized_trial" : "legacy", ranking_trial: active,
        served_model: active ? "personalized-trial" : "legacy", model_version: active ? "v5-fixture" : "legacy-linear-v1",
        ranking_artifact_id: "artifact-fixture", ranking_trained_at: "2026-11-03 00:00:00" }));
      return json(route, { request_id: `trial-${lists}`, items: ranked, total: ranked.length,
        next_offset: ranked.length, has_more: false, review_ranking: { requested: mode,
          active: active ? "personalized_trial" : "legacy", fallback_reason: fallback ? "model-not-ready" : null,
          artifact_id: "artifact-fixture", trained_at: "2026-11-03 00:00:00" } });
    }
    if (url.pathname === "/api/impressions") {
      deliveries.push(route.request().postDataJSON());
      return json(route, { ok: true });
    }
    if (url.pathname === "/api/impressions/visible") return json(route, { ok: true });
    if (url.pathname === "/api/feedback") {
      feedback.push(route.request().postDataJSON());
      return json(route, { error: "Synthetic feedback failure" }, 500);
    }
    if (url.pathname === "/api/queue-counts") return json(route, { review: 3, low_interest: 2 });
    assert.equal(route.request().method(), "GET", `unplanned synthetic mutation: ${url.pathname}`);
    return json(route, {});
  });
  const idle = () => page.waitForFunction(() => !reviewBusy());
  const current = () => page.locator(".focus-card").getAttribute("data-gallery-url");
  try {
    await page.goto("http://trial.test/");
    await page.locator(".focus-card").waitFor();
    await idle();
    assert.equal(await current(), items[0].url);
    saveState = "hold";
    await page.locator('label[for="reviewTrialRanking"]').click();
    await page.waitForFunction(() => reviewRankingPending);
    assert(await page.locator("#reviewTrialRanking").isDisabled());
    assert(await page.locator("#reviewNextBtn").isDisabled());
    assert(await page.locator(".scorebar button").first().isDisabled());
    const deadline = Date.now() + 10000;
    while (!heldSave) {
      assert(Date.now() < deadline);
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    mode = "personalized_trial";
    saveState = "ok";
    await json(heldSave, settings());
    await idle();
    assert.equal(await current(), items[2].url);
    assert.equal(await page.locator("#reviewRankingStatus").innerText(), "v5 Trial");
    assert.equal(await page.locator("#reviewTrialRanking").isChecked(), true);
    assert.equal(await page.locator("#reviewUndoBtn").isDisabled(), true);
    assert(!/match \d+%/.test(await page.locator(".focus-card").innerText()));
    assert(!/Confidence/.test(await page.locator(".focus-card").innerText()));
    await page.waitForFunction(() => document.querySelector(".thumb img")?.naturalWidth > 0);
    const cover = await page.locator(".focus-card .thumb").boundingBox();
    assert(cover.height >= 100, "cover collapsed below inspectable height");
    const details = await page.locator(".focus-card .body").boundingBox();
    const rating = await page.locator(".focus-rating").boundingBox();
    assert(details.y + details.height <= rating.y + 1, "rating overlaps details");
    assert(deliveries.some((delivery) => delivery.items.every((item) => item.ranking_trial && item.ranking_artifact_id === "artifact-fixture")));
    const boxes = await page.locator("#reviewModeControls").evaluate((el) => [...el.querySelectorAll("fieldset, #reviewSessionToolbar")].map((node) => {
      const r = node.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    }));
    for (const box of boxes) assert(box.left >= 0 && box.right <= viewport.width + 1, "toolbar overflow");
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      assert(Math.min(a.right, b.right) <= Math.max(a.left, b.left) + 1 ||
        Math.min(a.bottom, b.bottom) <= Math.max(a.top, b.top) + 1, "overlapping controls");
    }
    await page.screenshot({ path: path.join(output, `${viewport.width}-trial.png`), fullPage: true });
    await page.locator(".scorebar button[data-score='3']").click();
    await idle();
    assert.equal(feedback.length, 1);
    assert.equal(feedback[0].score, 3);
    assert.equal(feedback[0].impression_surface, "review");
    assert.equal(feedback[0].impression_request_id, deliveries.at(-1).request_id);

    // A failed save retains the old queue and restores controls.
    saveState = "failure";
    await page.locator('label[for="reviewLegacyRanking"]').click();
    await idle();
    assert.equal(await current(), items[2].url);
    assert(await page.locator("#reviewTrialRanking").isChecked());
    assert(await page.locator(".scorebar button").first().isEnabled());
    saveState = "ok";
    await page.locator('label[for="reviewLegacyRanking"]').click();
    await idle();
    assert.equal(await current(), items[0].url);

    // A successful save followed by a failed reload must not retain old cards.
    failLoad = true;
    await page.locator('label[for="reviewTrialRanking"]').click();
    await idle();
    assert.equal(await page.locator(".focus-card").count(), 0);
    failLoad = false;
    fallback = true;
    await page.locator("#reviewRefreshBtn").click();
    await idle();
    assert.equal(await current(), items[0].url);
    assert.equal(await page.locator("#reviewRankingStatus").innerText(), "Legacy (v5 unavailable)");
    assert(await page.locator("#reviewTrialRanking").isChecked());
    await page.screenshot({ path: path.join(output, `${viewport.width}-fallback.png`), fullPage: true });

    // Grid cards must also unlock after a failed save.
    await page.locator('label[for="reviewGridMode"]').click();
    saveState = "failure";
    await page.locator('label[for="reviewLegacyRanking"]').click();
    await idle();
    assert(await page.locator("#recommendations button[data-score]").first().isEnabled());
    await page.locator("#lowInterestTab").click();
    await page.locator("#reviewModeControls").waitFor({ state: "hidden" });
    assert.deepEqual(errors, []);
    return { viewport, passed: true, switches: writes.length, deliveries: deliveries.length, lists };
  } finally {
    await context.close();
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 568 }]) {
    results.push(await scenario(viewport));
  }
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
