// Optional integration check: npm install --prefix data/browser-validation playwright
// node tests/browser_audit.cjs .venv-rocm/Scripts/python.exe
const { chromium } = require("../data/browser-validation/node_modules/playwright");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const directory = path.resolve("data/browser-validation", `run-${Date.now()}`);
fs.mkdirSync(directory, { recursive: true });
const fixture = `
from exh_rec import db, app
from exh_rec.exhentai import Gallery
from exh_rec.recommender import store_galleries
from http.server import ThreadingHTTPServer
db.init_db()
with db.connect() as c:
    for k,v in {"auto_refresh":"0","review_require_bootstrap_match":"0","recommend_language_filter":"all","visual_encoder":"simple"}.items():
        db.set_setting(c,k,v)
    store_galleries(c,[Gallery(url=f"https://example.test/g/{i}/a/",gid=str(i),token="a",title=f"Audit QA {i}") for i in range(60)])
s=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
print("PORT="+str(s.server_port),flush=True)
s.serve_forever()
`;
const child = spawn(process.argv[2] || "python", ["-u", "-c", fixture], {
  env: { ...process.env, EXH_REC_DATA_DIR: directory },
  stdio: ["ignore", "pipe", "pipe"],
});
let browser;
async function main() {
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("fixture startup timeout")), 30000);
    child.once("error", reject);
    child.once("exit", (code) => reject(new Error(`fixture exited ${code}`)));
    child.stdout.on("data", (chunk) => {
      const match = chunk.toString().match(/PORT=(\d+)/);
      if (match) { clearTimeout(timer); resolve(Number(match[1])); }
    });
    child.stderr.on("data", (chunk) => fs.appendFileSync(path.join(directory, "server.log"), chunk));
  });
  browser = await chromium.launch({ channel: "msedge", headless: true });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let delivered = 0;
    const visible = new Set();
    const acknowledgments = [];
    page.on("response", (response) => {
      if (response.url().endsWith("/api/impressions/visible")) {
        acknowledgments.push(response.json().then((body) => {
          assert.equal(response.status(), 200);
          assert.equal(body.ok, true);
          return body.updated;
        }));
      }
    });
    page.on("request", (request) => {
      if (request.url().endsWith("/api/impressions")) delivered += request.postDataJSON().items.length;
      if (request.url().endsWith("/api/impressions/visible")) {
        for (const item of request.postDataJSON().items) visible.add(item.gallery_url);
      }
    });
    await page.goto(`http://127.0.0.1:${port}`, { waitUntil: "networkidle" });
    await page.locator("[data-gallery-url]").first().waitFor();
    await page.locator("[data-gallery-url]").first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(1800);
    assert(delivered > 0, "no delivery recorded");
    assert(visible.size > 0, "visible card did not report dwell");
    assert(visible.size < delivered, "offscreen delivered cards were counted visible");
    const before = visible.size;
    await page.locator("[data-gallery-url]").last().scrollIntoViewIfNeeded();
    await page.waitForTimeout(1800);
    assert(visible.size > before, "scroll did not expose another card");
    const confirmed = (await Promise.all(acknowledgments)).reduce((sum, n) => sum + n, 0);
    assert(confirmed >= visible.size, "backend did not persist the visible events");
    assert.deepEqual(errors, []);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "horizontal overflow");
    await page.screenshot({ path: path.join(directory, `viewport-${viewport.width}.png`) });
    console.log(JSON.stringify({ viewport, delivered, visible: visible.size, confirmed }));
    await context.close();
  }
  console.log(`Browser evidence: ${directory}`);
}
main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  child.kill();
});
