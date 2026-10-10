// Optional integration check: npm install --prefix data/browser-validation playwright
// node tests/browser_feedback.cjs
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `feedback-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const items = ["a", "b"].map((name) => ({
  url: `https://example.test/g/${name}/token/`,
  title: `Feedback regression ${name}`,
  tags: [], samples: [], reasons: [],
}));
const settings = {
  bootstrap_tags: [], fetch_pages: 1, stale_fetch_extra_pages: 3,
  detail_fetch_limit: 0, learned_query_limit: 0, recommend_candidate_limit: 2000,
  sample_extra_pages: 0, request_interval_seconds: 3, temporary_ban_pause_seconds: 600,
  refresh_interval_minutes: 60, auto_refresh: false,
  review_require_bootstrap_match: false, recommend_language_filter: "all",
  visual_encoder: "simple",
};
let browser;

async function scenario(viewport, mode) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const removed = new Set();
  let heldList, heldFeedback, listCalls = 0, feedbackCalls = 0;
  page.on("pageerror", (error) => errors.push(error.name));
  function list() {
    const remaining = items.filter((item) => !removed.has(item.url));
    return { items: remaining, total: remaining.length, next_offset: remaining.length, has_more: false };
  }
  async function json(route, body, status = 200) {
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  }
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname === "/" || pathname.startsWith("/static/")) {
      const name = pathname === "/" ? "index.html" : pathname.slice("/static/".length);
      assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
      await route.fulfill({
        contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", name)),
      });
    } else if (pathname === "/api/settings") {
      await json(route, settings);
    } else if (pathname === "/api/recommendations") {
      listCalls++;
      if (listCalls === 2 && mode !== "failure") {
        heldList = { route, snapshot: list() };
      } else {
        await json(route, list());
      }
    } else if (pathname === "/api/feedback") {
      feedbackCalls++;
      if (mode === "during" && feedbackCalls === 1) {
        heldFeedback = route;
      } else if (mode === "failure" && feedbackCalls === 1) {
        await json(route, { error: "Synthetic submission failure" }, 500);
      } else {
        const body = route.request().postDataJSON();
        removed.add(body.gallery_url);
        await json(route, { ok: true, removed_gallery_url: body.gallery_url });
      }
    } else if (pathname === "/api/queue-counts") {
      await json(route, { review: list().total });
    } else {
      await json(route, {});
    }
  });
  try {
    await page.goto("http://review.test/");
    await page.locator("[data-gallery-url]").first().waitFor();
    const card = page.locator(`[data-gallery-url="${items[0].url}"]`);
    const skip = card.locator("button[data-skip]");
    if (mode === "before") {
      await page.evaluate(() => { window.delayedList = loadRecommendations(); });
      await waitFor(() => heldList);
    }
    await skip.click();
    if (mode === "during") {
      await waitFor(() => heldFeedback);
      await page.evaluate(() => {
        renderGalleryCards(renderedGalleryItems);
        window.delayedList = loadRecommendations();
      });
      assert(await skip.isDisabled(), "rerender enabled a pending submit button");
      await skip.dispatchEvent("click");
      assert.equal(feedbackCalls, 1, "duplicate click posted a second feedback");
      await waitFor(() => heldList);
      removed.add(items[0].url);
      await json(heldFeedback, { ok: true, removed_gallery_url: items[0].url });
    }
    if (mode === "failure") {
      await page.locator("#status").filter({ hasText: "Synthetic submission failure" }).waitFor();
      assert.equal(await card.count(), 1);
      assert(await skip.isEnabled());
      await skip.click();
    }
    await card.waitFor({ state: "detached" });
    if (heldList) {
      await json(heldList.route, heldList.snapshot);
      await page.evaluate(() => window.delayedList);
    }
    assert.equal(await card.count(), 0, "stale response resurrected the reviewed card");
    assert.equal(await page.locator("[data-gallery-url]").count(), 1);
    assert.deepEqual(errors, []);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, `${viewport.width}-${mode}.png`) });
    return { viewport, mode, feedbackCalls, remaining: 1, passed: true };
  } finally {
    await context.close();
  }
}

async function waitFor(check) {
  const deadline = Date.now() + 10000;
  while (!check()) {
    if (Date.now() >= deadline) throw new Error("Synthetic route timeout");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    for (const mode of ["before", "during", "failure"]) {
      results.push(await scenario(viewport, mode));
    }
  }
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2), "utf8");
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
