// Optional integration check: node tests/browser_hath_pack.cjs
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
let browser;

async function scenario(viewport, available) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const previews = [];
  page.on("pageerror", (error) => errors.push(error.name));
  await context.route("**/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/" || pathname.startsWith("/static/")) {
      const name = pathname === "/" ? "index.html" : pathname.slice(8);
      assert(["index.html", "app.js", "styles.css", "visibility.js"].includes(name));
      await route.fulfill({
        contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
        body: fs.readFileSync(path.join(root, "static", name)),
      });
      return;
    }
    let body = {};
    if (pathname === "/api/settings") body = { bootstrap_tags: [] };
    if (pathname === "/api/integrations/hath/pack/status") body = { configured: true, state: "idle" };
    if (pathname === "/api/integrations/hath/pack/inventory") {
      body = {
        mega: { available }, trash: { jobs: [] },
        candidates: [{ name: "fixture-gallery", kind: "gallery", file_count: 1, size_bytes: 100 }],
      };
    }
    if (route.request().method() !== "GET") {
      assert.equal(pathname, "/api/integrations/hath/pack/preview", "unexpected mutation request");
      previews.push(route.request().postDataJSON());
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  try {
    await page.goto("http://hath.test/");
    await page.locator("#hathTab").click();
    const source = page.locator("input[data-hath-pack-source]");
    await source.waitFor();
    const upload = page.locator("#hathPackUpload");
    const archive = page.locator("#hathPackTrashArchive");
    assert.equal(await upload.isChecked(), available);
    assert.equal(await upload.isDisabled(), !available);
    assert(await page.locator("#hathPackTrashSources").isChecked());
    assert.equal(await archive.isChecked(), available);
    assert.equal(await archive.isDisabled(), !available);
    assert.equal(await page.locator("#hathPackCleanupMode").inputValue(), "trash");
    await source.check();
    await page.locator("#hathPackPreviewBtn").click();
    await page.waitForFunction(() => !document.querySelector("#hathPackPlan").classList.contains("hidden"));
    assert.equal(previews.length, 1);
    assert.deepEqual(previews[0], {
      selected: ["fixture-gallery"], archive_name: previews[0].archive_name,
      upload: available, mega_destination: "/H@H Archives", trash_sources: true,
      trash_archive_after_upload: available, cleanup_mode: "trash",
    });
    if (available) {
      await upload.uncheck();
      assert(!(await archive.isChecked()));
      assert(await archive.isDisabled());
      assert(await page.locator("#hathPackPlan").evaluate((el) => el.classList.contains("hidden")));
      assert(await page.locator("#hathPackBtn").isDisabled());
      await page.reload();
      await page.locator("#hathTab").click();
      await source.waitFor();
      assert(await upload.isChecked());
      assert(await archive.isChecked());
      assert(await page.locator("#hathPackTrashSources").isChecked());
    }
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
  }
}

(async () => {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    for (const available of [true, false]) await scenario(viewport, available);
  }
  console.log("H@H defaults: 4 desktop/mobile and MEGA availability scenarios passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
});
