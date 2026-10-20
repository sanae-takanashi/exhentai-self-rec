// Optional integration check: node tests/browser_hath_cleanup.cjs
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const output = path.join(root, "data/browser-validation", `hath-cleanup-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
let browser;

async function scenario(viewport, mode, behavior = "normal") {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const previews = [];
  const starts = [];
  let heldPreview, state = "idle", cleaned = false;
  page.on("pageerror", (error) => errors.push(error.name));
  const candidates = [
    { name: 'Gallery <one> & "special"', kind: "gallery", file_count: 5, size_bytes: 100 },
    { name: "active-gallery", active: true, kind: "gallery", file_count: 2, size_bytes: 50 },
  ];
  const archive = { name: "20261020.zip", kind: "archive", file_count: 1, size_bytes: 100 };
  function plan(request) {
    return { kind: "cleanup-only", preview_id: `preview-${previews.length}`,
      request: { ...request, snapshot: { fixture: "fingerprint" } },
      selected_count: request.selected.length + request.archives.length,
      source_count: request.selected.length, archive_count: request.archives.length,
      input_files: 6, input_bytes: 200,
      actions: [{ message: `${request.cleanup_mode === "delete" ? "Permanently delete" : "Move to recoverable trash:"} /srv/hath/download/${candidates[0].name}` }],
    };
  }
  async function json(route, body, status = 200) {
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  }
  await context.route("**/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/" || pathname.startsWith("/static/")) {
      const name = pathname === "/" ? "index.html" : pathname.slice(8);
      assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
      await route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", name)) });
    } else if (pathname === "/api/settings") {
      await json(route, { bootstrap_tags: [] });
    } else if (pathname === "/api/integrations/hath/pack/inventory") {
      await json(route, { mega: { available: false }, trash: { jobs: [] },
        capabilities: behavior === "unsupported" ? [] : ["cleanup-only-v1"],
        candidates: cleaned ? candidates.slice(1) : candidates, archives: cleaned ? [] : [archive] });
    } else if (pathname === "/api/integrations/hath/pack/status") {
      await json(route, { configured: true, state });
    } else if (pathname === "/api/integrations/hath/pack/preview") {
      const request = route.request().postDataJSON();
      assert.equal(request.operation, "cleanup-only");
      assert.deepEqual(Object.keys(request).sort(), ["archives", "cleanup_mode", "operation", "selected"]);
      assert(!request.selected.includes("active-gallery"));
      previews.push(request);
      if (previews.length === 1) heldPreview = { route, request };
      else await json(route, behavior === "legacy" ? { request: { archive_name: "legacy.zip" } } : plan(request));
    } else if (pathname === "/api/integrations/hath/pack/start") {
      starts.push(route.request().postDataJSON());
      assert.equal(starts.length, 1, "cleanup was submitted twice");
      state = "running";
      await json(route, { configured: true, state });
    } else {
      assert.equal(route.request().method(), "GET", "unexpected mutation request");
      await json(route, {});
    }
  });
  try {
    await page.goto("http://hath-cleanup.test/");
    await page.locator("#hathTab").click();
    await page.locator("input[data-hath-pack-source]").first().waitFor();
    await page.locator('input[name="hathFileMode"][value="cleanup"]').check();
    assert(!(await page.locator("#hathPackArchiveName").isVisible()));
    assert(!(await page.locator("#hathPackUpload").isVisible()));
    assert(await page.locator("#hathCleanupArchives").isVisible());
    assert(await page.locator("#hathPackPreviewBtn").isDisabled());
    assert(await page.locator("#hathPackBtn").isDisabled());
    assert.equal(await page.locator("#hathPackCleanupMode").inputValue(), "trash");
    await page.locator("#hathCleanupArchiveAllBtn").click();
    if (behavior === "unsupported") {
      assert(await page.locator("#hathPackPreviewBtn").isDisabled());
      assert.equal(previews.length, 0);
      return { viewport, mode, behavior, passed: true };
    }
    assert(await page.locator("#hathPackPreviewBtn").isEnabled(), "archive-only cleanup is disabled");
    await page.locator('input[name="hathFileMode"][value="archive"]').check();
    assert(await page.locator("#hathPackPreviewBtn").isDisabled());
    await page.locator('input[name="hathFileMode"][value="cleanup"]').check();
    await page.locator("#hathPackPreviewBtn").click();
    await page.waitForTimeout(50);
    assert(heldPreview);
    await page.locator("#hathCleanupArchiveNoneBtn").click();
    await json(heldPreview.route, plan(heldPreview.request));
    await page.waitForTimeout(50);
    assert(await page.locator("#hathPackBtn").isDisabled(), "stale preview enabled cleanup");
    assert(!(await page.locator("#hathPackPlan").isVisible()));
    await page.locator("#hathPackSelectAllBtn").click();
    assert.equal(await page.locator("input[data-hath-pack-source]:checked").count(), 1);
    await page.locator("#hathCleanupArchiveAllBtn").click();
    await page.locator("#hathPackCleanupMode").selectOption(mode);
    await page.locator("#hathPackPreviewBtn").click();
    if (behavior === "legacy") {
      await page.locator("#status").filter({ hasText: "does not support cleanup-only" }).waitFor();
      assert(await page.locator("#hathPackBtn").isDisabled());
      assert.equal(starts.length, 0);
      return { viewport, mode, behavior, passed: true };
    }
    await page.locator("#hathPackPlan").waitFor();
    assert(await page.locator("#hathPackBtn").isEnabled());
    assert.deepEqual(previews[1], { operation: "cleanup-only", selected: [candidates[0].name],
      archives: [archive.name], cleanup_mode: mode });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator(".hath-pack-section").screenshot({ path: path.join(output, `${viewport.width}-${mode}.png`) });
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.locator("#hathPackBtn").click();
    assert.equal(starts.length, 0, "cancelled cleanup was submitted");
    page.once("dialog", async (dialog) => {
      assert(dialog.message().includes("No archive creation, upload, or MEGA verification."));
      assert.equal(dialog.message().includes("This cannot be undone."), mode === "delete");
      await dialog.accept();
    });
    await page.locator("#hathPackBtn").click();
    await page.locator("#hathPackBtn").dispatchEvent("click");
    await page.waitForFunction(() => document.querySelector("#hathPackBadge").textContent === "Running");
    assert.deepEqual(starts, [{ preview_id: "preview-2" }]);
    cleaned = true;
    state = "succeeded";
    await page.evaluate(() => loadHathPackStatus());
    await page.locator("input[data-hath-cleanup-archive]").waitFor({ state: "detached" });
    assert.equal(await page.locator("input[data-hath-pack-source]").count(), 1);
    assert(await page.locator("#hathPackBtn").isDisabled());
    assert.deepEqual(errors, []);
    return { viewport, mode, passed: true };
  } finally {
    await context.close();
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    for (const mode of ["trash", "delete"]) results.push(await scenario(viewport, mode));
    for (const behavior of ["unsupported", "legacy"]) results.push(await scenario(viewport, "trash", behavior));
  }
  fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ passed: results.length, evidence: output }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
