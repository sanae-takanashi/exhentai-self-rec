const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const renderSource = source.slice(source.indexOf("function renderStatus(payload)"), source.indexOf("function escapeAttr(value)"));

function render(payload) {
  const refreshStatusEl = { innerHTML: "" };
  const context = vm.createContext({ refreshStatusEl, payload });
  vm.runInContext(renderSource, context);
  vm.runInContext("renderStatus(payload)", context);
  return refreshStatusEl.innerHTML;
}

test("page cap completion has a reason and frozen publication cutoff", () => {
  const html = render({ fetch: { running: false }, fetch_activity: {
    run_id: 42, min_posted_date: "2026-08-05", running: false,
    queries: [{ label: "Recent galleries", state: "done", completion_reason: "page_limit", pages_done: 25, page_budget: 25 }],
  } });
  assert.match(html, /Published &gt;= 2026-08-05; page cap completes query/);
  assert.match(html, /done: Page cap; 25\/25 pages/);
  assert.doesNotMatch(html, /catch-up pending/);
});

test("metadata batches and actual waits render with independent stage timing", () => {
  const html = render({ fetch: { running: true }, fetch_activity: {
    run_id: 42, running: true, stage: "cover_metadata", message: "Processing cover metadata batch",
    metadata_batches_done: 3, metadata_total_batches: 10, metadata_batch: 4, metadata_failed_batches: 1,
    wait_reason: "retry", wait_remaining_seconds: 2, request_attempt: 3,
    elapsed_seconds: 90, phase_elapsed_seconds: 12, quiet_seconds: 1,
  } });
  assert.match(html, /3\/10 processed; current 4; failed 1/);
  assert.match(html, /Retry backoff; 2s remaining; attempt 3/);
  assert.match(html, /90s elapsed; 12s in stage; updated 1s ago/);
});

test("finished activity never presents a stale wait as live", () => {
  const html = render({ fetch_activity: { run_id: 42, running: false, wait_reason: "retry", wait_remaining_seconds: 5 } });
  assert.doesNotMatch(html, /Retry backoff|remaining|in stage/);
});

test("old services and plan-only responses remain supported", () => {
  assert.match(render({ fetch: { running: true, message: "Fetching" } }), /Fetching/);
  assert.match(render({ plan: { entries: [], pages: 5, stale_fetch_extra_pages: 20, detail_fetch_limit: 8,
    min_posted_date: "2026-08-06", page_limit_policy: "complete" } }), /Plan Window/);
});

test("query text and unknown completion reasons cannot inject HTML", () => {
  const html = render({ fetch_activity: { run_id: 42, queries: [{ label: '<img src=x onerror="alert(1)">',
    state: "partial", completion_reason: "<script>", pages_done: 2, page_budget: 25 }] } });
  assert.match(html, /&lt;img/);
  assert.match(html, /partial: Unknown/);
  assert.doesNotMatch(html, /<img|<script>/);
});
