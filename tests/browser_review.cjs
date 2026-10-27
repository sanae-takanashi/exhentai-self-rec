// Headless synthetic workflow; never touches real galleries, feedback or downloads.
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `review-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
let browser;

async function until(check) {
  const deadline = Date.now() + 10000;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("Synthetic workflow timed out");
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

async function scenario(viewport) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(() => sessionStorage.setItem("review-layout", "focus"));
  const page = await context.newPage();
  const raster = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 360; canvas.height = 480;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#83bba2"; ctx.fillRect(0, 0, 360, 480);
    ctx.fillStyle = "#de707c"; ctx.fillRect(30, 30, 220, 190);
    ctx.fillStyle = "#f0d277"; ctx.fillRect(110, 180, 220, 190);
    ctx.fillStyle = "#182123"; ctx.font = "bold 32px sans-serif";
    ctx.fillText("FIXTURE A", 30, 440);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const items = ["a", "b", "c"].map((name, index) => ({
    url: `https://example.test/g/${name}/token/`,
    title: index === 0 ? `Fixture A - ${"LongUnbrokenTitle".repeat(12)}` : `Fixture ${name.toUpperCase()}`,
    thumb_url: index === 1 ? null : "https://images.test/synthetic.png",
    samples: index === 0 ? ["one", "two", "three"] : [],
    tags: ["artist:fixture", "language:english", "synthetic:color-study"], reasons: ["Synthetic match"],
    visual_embedding_version: "canvas-rgb-8x8-v1", visual_embedding_at: "fixture",
    page_count: 40, category: "Illustration", uploader: "Fixture uploader", like_probability: 0.78,
  }));
  const removed = new Set();
  const visible = [], errors = [], external = [];
  let delivery, feedbackRoute, feedbackCalls = 0, undoCalls = 0, listCalls = 0;
  let ratingMode = "failure", undoMode = "failure";
  let classificationMode = "failure", classificationRoute, classificationCalls = 0, markCalls = 0;
  let lastRating, holdCounts = false, countRoute;
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  page.on("pageerror", (error) => errors.push(error.message));
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (url.hostname === "example.test") {
      external.push(url.href);
      return route.fulfill({ contentType: "text/html", body: "<title>Synthetic external gallery</title>" });
    }
    assert.equal(url.hostname, "review.test", "unexpected external networking");
    if (pathname === "/" || pathname.startsWith("/static/")) {
      const name = pathname === "/" ? "index.html" : pathname.slice(8);
      assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
      return route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", name)) });
    }
    if (pathname === "/thumb") {
      return route.fulfill({ contentType: "image/png", body: Buffer.from(raster, "base64") });
    }
    if (pathname === "/api/settings") return json(route, { bootstrap_tags: [], visual_encoder: "simple", recommend_language_filter: "all" });
    if (pathname === "/api/recommendations") {
      listCalls++;
      const remaining = items.filter((item) => !removed.has(item.url));
      return json(route, { request_id: `synthetic-${listCalls}`, items: remaining, total: remaining.length,
        next_offset: remaining.length, has_more: false });
    }
    if (pathname === "/api/impressions") {
      if (!delivery) { delivery = route; return; }
      return json(route, { ok: true });
    }
    if (pathname === "/api/impressions/visible") {
      visible.push(route.request().postDataJSON());
      return json(route, { ok: true });
    }
    if (pathname === "/api/feedback") {
      feedbackCalls++;
      lastRating = route.request().postDataJSON();
      if (ratingMode === "hold") { feedbackRoute = route; return; }
      if (ratingMode === "failure") return json(route, { error: "Synthetic rating failure" }, 500);
      const body = route.request().postDataJSON();
      removed.add(body.gallery_url);
      return json(route, { ok: true, removed_gallery_url: body.gallery_url, feedback_undo_supported: true,
        feedback_update: { latest_feedback_id: feedbackCalls } });
    }
    if (pathname === "/api/feedback/undo") {
      undoCalls++;
      if (undoMode === "failure") return json(route, { error: "Synthetic undo failure" }, 500);
      if (undoMode === "conflict") return json(route, { error: "Synthetic newer rating" }, 409);
      removed.delete(route.request().postDataJSON().gallery_url);
      return json(route, { ok: true });
    }
    if (pathname === "/api/classification") {
      classificationCalls++;
      if (classificationMode === "hold") { classificationRoute = route; return; }
      return json(route, { error: "Synthetic classification failure" }, 500);
    }
    if (pathname === "/api/mark") {
      markCalls++;
      const body = route.request().postDataJSON();
      if (body.kind === "ban") return json(route, { error: "Synthetic ban failure" }, 500);
      removed.add(body.gallery_url);
      return json(route, { ok: true, removed_gallery_url: body.gallery_url });
    }
    if (pathname === "/api/queue-counts") {
      if (holdCounts) { countRoute = route; return; }
      return json(route, { review: items.length - removed.size });
    }
    return json(route, {});
  });
  try {
    await page.goto("http://review.test/");
    await page.locator(".focus-card").waitFor();
    const current = () => page.locator(".focus-card").getAttribute("data-gallery-url");
    assert.equal(await current(), items[0].url);
    assert.equal(await page.locator(".focus-card").count(), 1);
    const cover = page.locator("a.thumb");
    assert.equal(await cover.getAttribute("target"), "_blank");
    assert.equal(await cover.getAttribute("rel"), "noreferrer");
    await page.locator("#reviewNextBtn").click();
    assert.equal(await current(), items[1].url);
    assert.equal(feedbackCalls, 0);
    assert.equal(external.length, 0);
    await until(() => delivery);
    await json(delivery, { ok: true });
    await until(() => visible.length);
    assert.equal(visible[0].request_id, "synthetic-1");
    assert.deepEqual(visible.flatMap((event) => event.items.map((item) => item.gallery_url)), [items[1].url]);
    await page.locator("#reviewPrevBtn").click();
    await page.locator(".thumb img").waitFor();
    await page.waitForFunction(() => document.querySelector(".thumb img")?.naturalWidth > 0);
    const popupPromise = page.waitForEvent("popup");
    await cover.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    await popup.close();
    await page.bringToFront();
    assert.equal(await current(), items[0].url);
    assert.equal(feedbackCalls, 0);
    assert.equal(await page.locator(".review-opened").isVisible(), true);
    await page.evaluate(() => reloadRecommendationsAfterFetch({ fetch: {}, last_fetch: { id: 77 } }));
    assert.equal(listCalls, 1);
    assert.equal(await page.locator("#reviewRefreshBtn").evaluate((el) => el.classList.contains("updates-available")), true);

    const score = page.locator('[data-score="1"]');
    await page.locator('.focus-rating select[data-reason-for]').selectOption("quality");
    await score.click();
    await page.locator("#status").filter({ hasText: "Synthetic rating failure" }).waitFor();
    assert.equal(await current(), items[0].url);
    assert.equal(lastRating.reason_code, "quality");
    assert(await score.isEnabled());
    ratingMode = "hold";
    await score.click();
    await until(() => feedbackRoute);
    assert(await page.locator("#reviewNextBtn").isDisabled());
    await score.dispatchEvent("click");
    assert.equal(feedbackCalls, 2);
    removed.add(items[0].url);
    await json(feedbackRoute, { ok: true, removed_gallery_url: items[0].url, feedback_undo_supported: true,
      feedback_update: { latest_feedback_id: 2 } });
    await page.waitForFunction((url) => document.querySelector(".focus-card")?.dataset.galleryUrl === url, items[1].url);
    await page.locator("#reviewUndoBtn").click();
    await page.locator("#status").filter({ hasText: "Synthetic undo failure" }).waitFor();
    assert.equal(await current(), items[1].url);
    assert(await page.locator("#reviewUndoBtn").isEnabled());
    undoMode = "success";
    await page.locator("#reviewUndoBtn").click();
    await page.waitForFunction((url) => document.querySelector(".focus-card")?.dataset.galleryUrl === url, items[0].url);

    await page.locator('label[for="reviewGridMode"]').click();
    assert.equal(await page.locator("[data-gallery-url]").count(), 3);
    await page.locator('label[for="reviewFocusMode"]').click();
    assert.equal(await current(), items[0].url);
    assert.equal(listCalls, 1);
    await page.evaluate(() => window.scrollTo(0, 0));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const bounds = await page.locator(".focus-rating").boundingBox();
    assert(bounds.y >= 0 && bounds.y + bounds.height <= viewport.height + 1);
    assert.equal(await page.locator(".thumb img").evaluate((el) => getComputedStyle(el).objectFit), "contain");
    const coverBounds = await cover.boundingBox();
    const imageBounds = await page.locator(".thumb img").boundingBox();
    assert(Math.abs(imageBounds.height - coverBounds.height) <= 1, "cover image overflows its frame");
    const bodyBounds = await page.locator(".focus-card .body").boundingBox();
    await page.screenshot({ path: path.join(output, `${viewport.width}-focus.png`) });
    assert(bodyBounds.y + bodyBounds.height <= bounds.y,
      `rating footer overlaps gallery details: ${JSON.stringify({ viewport, bounds, bodyBounds })}`);
    for (const selector of ['select[data-reason-for]', '[data-mark="favorite"]', '[data-mark="ban"]', '[data-classification="updates"]']) {
      const control = page.locator(`.focus-rating ${selector}`);
      assert(await control.isVisible(), `${selector} is not directly visible`);
      const box = await control.boundingBox();
      assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1,
        `${selector} is outside the viewport`);
      if (selector.startsWith('[')) await control.click({ trial: true });
    }
    await page.screenshot({ path: path.join(output, `${viewport.width}-focus.png`) });

    ratingMode = "success";
    await page.locator("body").click({ position: { x: 4, y: 4 } });
    await page.keyboard.press("5");
    await page.waitForFunction((url) => document.querySelector(".focus-card")?.dataset.galleryUrl === url, items[1].url);
    undoMode = "conflict";
    await page.keyboard.press("z");
    await page.locator("#status").filter({ hasText: "Synthetic newer rating" }).waitFor();
    assert(await page.locator("#reviewUndoBtn").isDisabled());
    assert.equal(await current(), items[1].url);

    const updates = page.locator('[data-classification="updates"]');
    await updates.click();
    await page.locator("#status").filter({ hasText: "Synthetic classification failure" }).waitFor();
    assert.equal(await current(), items[1].url);
    assert(await updates.isEnabled());
    classificationMode = "hold";
    await updates.click();
    await until(() => classificationRoute);
    await updates.dispatchEvent("click");
    assert.equal(classificationCalls, 2);
    assert.equal(await current(), items[1].url);
    const beforeMoveLists = listCalls;
    holdCounts = true;
    removed.add(items[1].url);
    await json(classificationRoute, { ok: true, gallery_url: items[1].url, classification: "updates" });
    await page.waitForFunction((url) => document.querySelector(".focus-card")?.dataset.galleryUrl === url, items[2].url);
    await until(() => countRoute);
    assert(await page.locator('[data-score="5"]').isEnabled(), "classification waits for background counts");
    assert.equal(listCalls, beforeMoveLists, "classification unexpectedly rebuilt the queue");
    await page.locator('[data-mark="ban"]').click();
    await page.locator("#status").filter({ hasText: "Synthetic ban failure" }).waitFor();
    assert.equal(await current(), items[2].url);
    await page.locator('[data-mark="favorite"]').click();
    await page.waitForFunction(() => !document.querySelector(".focus-card"));
    assert.equal(markCalls, 2);
    holdCounts = false;
    await json(countRoute, { review: 0 });
    await page.locator("#localFilter").fill("555");
    assert.equal(feedbackCalls, 3);
    assert.equal(external.length, 1);
    assert.deepEqual(errors, []);
    return { viewport, feedbackCalls, undoCalls, classificationCalls, markCalls, visibleEvents: visible.length, passed: true };
  } finally {
    await context.close();
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 568 }]) results.push(await scenario(viewport));
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
