const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const bootstrap = source.indexOf('document.querySelector("#saveBtn").addEventListener');
assert(bootstrap > 0);
const items = ["a", "b"].map((name) => ({
  url: `https://example.test/g/${name}/token/`,
  title: `Fixture ${name}`,
  tags: [],
  samples: [],
  reasons: [],
}));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture(view = "review") {
  const elements = new Map();
  function element() {
    return {
      value: "", checked: false, style: {}, dataset: {}, children: [],
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute() {},
      appendChild(child) { this.children.push(child); },
      set innerHTML(value) { this.html = value; this.children = []; },
      get innerHTML() { return this.html || ""; },
      querySelector() { return null; },
      querySelectorAll() {
        return this.children.flatMap((child) => child.buttons || []);
      },
    };
  }
  const document = {
    querySelector(selector) {
      if (selector.startsWith("dialog[open]")) return null;
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
    querySelectorAll: () => [],
    createElement() {
      const card = element();
      Object.defineProperty(card, "innerHTML", {
        set(html) {
          this.html = html;
          this.buttons = [...html.matchAll(/<button\b([^>]*)>/g)].map((match) => {
            const dataset = {};
            for (const attr of match[1].matchAll(/data-([a-z-]+)="([^"]*)"/g)) {
              dataset[attr[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = attr[2];
            }
            return { dataset, disabled: false };
          });
        },
        get() { return this.html; },
      });
      return card;
    },
  };
  const requests = [];
  const context = {
    document, console, URLSearchParams, CSS: { escape: (value) => value },
    fixtureItems: items, fixtureView: view,
    fixtureApi: (url, options) => {
      const pending = deferred();
      requests.push({ url, options, ...pending });
      return pending.promise;
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(0, bootstrap), context);
  vm.runInContext(`
    api = fixtureApi;
    refreshQueueCounts = () => {};
    recordPageImpressions = () => {};
    queueVisualEmbedding = () => {};
    renderLowInterestPolicy = () => {};
    renderClassifierSummary = () => {};
    currentView = fixtureView;
    renderGalleryCards(fixtureItems);
    nextRecommendationOffset = 2;
  `, context);
  return {
    context, requests, run: (code) => vm.runInContext(code, context),
    urls: () => JSON.parse(vm.runInContext("JSON.stringify(renderedGalleryUrls)", context)),
    buttons: (url = items[0].url) => document.querySelector("#recommendations")
      .querySelectorAll().filter((button) => button.dataset.url === url),
    respond(index, payload) { requests[index].resolve(payload); },
  };
}

const loaders = [
  ["review", "loadRecommendations()"],
  ["low-interest", "loadRecommendations()"],
  ["discovery", "loadDiscovery()"],
  ["continuing-updates", "loadContinuingUpdates()"],
  ["short-repeats", "loadShortRepeats()"],
  ["history", "loadReactionHistory()"],
  ["favorite", 'loadMarkedGalleries("favorite")'],
  ["classifier", "loadClassificationSamples()"],
];
function page(pageItems = items) {
  return { items: pageItems, total: pageItems.length, next_offset: pageItems.length, has_more: false };
}

for (const [view, loader] of loaders) {
  test(`${view}: a pre-submit list cannot restore a successfully reviewed card`, async () => {
    const f = fixture(view);
    const loading = f.run(loader);
    const submitting = f.run('skip(fixtureItems[0].url)');
    f.respond(1, { ok: true, removed_gallery_url: items[0].url });
    // History intentionally requests a fresh page after a successful edit.
    if (view === "history") {
      await new Promise(setImmediate);
      f.respond(2, page([items[1]]));
    }
    await submitting;
    f.respond(0, page());
    await loading;
    assert.deepEqual(f.urls(), [items[1].url]);
  });

  test(`${view}: a response from a departed view cannot overwrite the new view`, async () => {
    const f = fixture(view);
    const loading = f.run(loader);
    f.run('currentView = "preview"; renderGalleryCards([fixtureItems[1]]);');
    f.respond(0, page());
    await loading;
    assert.deepEqual(f.urls(), [items[1].url]);
  });
}

test("a list started during submission is invalidated after the commit", async () => {
  const f = fixture();
  const submitting = f.run("vote(fixtureItems[0].url, 1)");
  const loading = f.run("loadRecommendations()");
  f.respond(0, { ok: true, removed_gallery_url: items[0].url });
  await submitting;
  f.respond(1, page());
  await loading;
  assert.deepEqual(f.urls(), [items[1].url]);
  const fresh = f.run("loadRecommendations()");
  f.respond(2, page([items[1]]));
  await fresh;
  assert.deepEqual(f.urls(), [items[1].url]);
});

test("a failed submission retains the card, reenables controls, and allows a retry", async () => {
  const f = fixture();
  const submitting = f.run("score(fixtureItems[0].url, 5)");
  assert(f.buttons().every((button) => button.disabled));
  f.requests[0].reject(new Error("Fixture failure"));
  await assert.rejects(submitting, /Fixture failure/);
  assert.deepEqual(f.urls(), items.map((item) => item.url));
  assert(f.buttons().every((button) => !button.disabled));
  const retry = f.run("score(fixtureItems[0].url, 5)");
  f.respond(1, { ok: true, removed_gallery_url: items[0].url });
  await retry;
  assert.deepEqual(f.urls(), [items[1].url]);
});

test("rerendered controls remain disabled and duplicate clicks do not submit twice", async () => {
  const f = fixture();
  const first = f.run("vote(fixtureItems[0].url, 1)");
  f.run("renderGalleryCards(fixtureItems)");
  assert(f.buttons().length > 0);
  assert(f.buttons().every((button) => button.disabled));
  await f.run("vote(fixtureItems[0].url, 1)");
  assert.equal(f.requests.length, 1);
  f.respond(0, { ok: true, removed_gallery_url: items[0].url });
  await first;
});

test("simultaneous feedback on different cards preserves both successful removals", async () => {
  const f = fixture();
  const first = f.run("skip(fixtureItems[0].url)");
  const second = f.run("score(fixtureItems[1].url, 4)");
  f.respond(0, { ok: true, removed_gallery_url: items[0].url });
  await first;
  assert(f.buttons(items[1].url).every((button) => button.disabled));
  f.respond(1, { ok: true, removed_gallery_url: items[1].url });
  await second;
  assert.deepEqual(f.urls(), []);
});

test("feedback completed after a tab switch reloads that tab rather than applying the old result", async () => {
  const f = fixture();
  const submitting = f.run("skip(fixtureItems[0].url)");
  f.run('currentView = "favorite";');
  f.respond(0, { ok: true, removed_gallery_url: items[0].url, items: [] });
  await new Promise(setImmediate);
  assert(f.requests[1].url.startsWith("/api/marks?"));
  f.respond(1, page([items[1]]));
  await submitting;
  assert.deepEqual(f.urls(), [items[1].url]);
});

test("an older Load More response cannot append a reviewed card", async () => {
  const f = fixture();
  const loading = f.run("loadRecommendations(2, true)");
  const submitting = f.run("skip(fixtureItems[0].url)");
  f.respond(1, { ok: true, removed_gallery_url: items[0].url });
  await submitting;
  f.respond(0, page([items[0]]));
  await loading;
  assert.deepEqual(f.urls(), [items[1].url]);
});

test("normal pagination and newer list requests still work", async () => {
  const f = fixture();
  const old = f.run("loadRecommendations()");
  const latest = f.run("loadRecommendations()");
  f.respond(1, page([items[1]]));
  await latest;
  f.respond(0, page());
  await old;
  assert.deepEqual(f.urls(), [items[1].url]);
  const more = f.run("loadRecommendations(1, true)");
  f.respond(2, page([items[0]]));
  await more;
  assert.deepEqual(f.urls(), [items[1].url, items[0].url]);
});

function focusFixture() {
  const f = fixture();
  f.run('reviewLayout = "focus"; renderGalleryCards(renderedGalleryItems);');
  return f;
}

const undoResponse = (id = 7) => ({
  ok: true, removed_gallery_url: items[0].url,
  feedback_undo_supported: true, feedback_update: { latest_feedback_id: id },
});

test("Focus: navigation does not label; mode changes preserve the selected URL", async () => {
  const f = focusFixture();
  assert.equal(f.run('recommendationsEl.children.length'), 1);
  await f.run('navigateReview(1)');
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
  assert.equal(f.requests.length, 0);
  f.run('setReviewLayout("grid"); setReviewLayout("focus");');
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
  assert.equal(f.run('recommendationsEl.children[0].dataset.galleryUrl'), items[1].url);
});

test("Focus: pending rating blocks duplicate actions and navigation; success advances once", async () => {
  const f = focusFixture();
  const first = f.run('score(fixtureItems[0].url, 5)');
  await f.run('navigateReview(1); skip(fixtureItems[0].url);');
  assert.equal(f.requests.length, 1);
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  f.respond(0, undoResponse());
  await first;
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
  assert(f.buttons(items[1].url).every((button) => !button.disabled));
  assert.equal(f.run('reviewUndo.feedbackId'), 7);
});

test("Focus: failed rating stays in place and can be retried", async () => {
  const f = focusFixture();
  const first = f.run('skip(fixtureItems[0].url)');
  f.requests[0].reject(new Error("Synthetic failure"));
  await assert.rejects(first);
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  assert(f.buttons().every((button) => !button.disabled));
});

test("Focus: exact-event undo restores the item and invalidates stale pages", async () => {
  const f = focusFixture();
  const vote = f.run('vote(fixtureItems[0].url, -1)');
  f.respond(0, undoResponse());
  await vote;
  const loading = f.run('loadRecommendations(1, true)');
  f.respond(1, page([items[1]]));
  await loading;
  const undo = f.run('undoReviewRating()');
  assert.deepEqual(JSON.parse(f.requests[2].options.body), { gallery_url: items[0].url, feedback_id: 7 });
  await f.run('undoReviewRating()');
  assert.equal(f.requests.length, 3);
  f.respond(2, { ok: true });
  await undo;
  assert.deepEqual(f.urls(), items.map((item) => item.url));
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  assert.equal(f.run('reviewUndo'), null);
});

test("Focus: old backends do not enable undo", async () => {
  const f = focusFixture();
  const vote = f.run('skip(fixtureItems[0].url)');
  f.respond(0, { ok: true, feedback_update: { latest_feedback_id: 7 } });
  await vote;
  assert.equal(f.run('reviewUndo'), null);
  await f.run('undoReviewRating()');
  assert.equal(f.requests.length, 1);
});

for (const status of [409, 500]) {
  test(`Focus: undo ${status} retains the next gallery without local reinsertion`, async () => {
    const f = focusFixture();
    const vote = f.run('skip(fixtureItems[0].url)');
    f.respond(0, undoResponse());
    await vote;
    const undo = f.run('undoReviewRating()');
    f.requests[1].reject(Object.assign(new Error("Synthetic undo failure"), { status }));
    await assert.rejects(undo);
    assert.deepEqual(f.urls(), [items[1].url]);
    assert.equal(Boolean(f.run('reviewUndo')), status !== 409);
    assert.equal(f.run('reviewBusy()'), false);
  });
}

test("Focus: background completion signals updates without replacing the queue", async () => {
  const f = focusFixture();
  await f.run('reloadRecommendationsAfterFetch({ fetch: {}, last_fetch: { id: 99 } })');
  assert.equal(f.requests.length, 0);
  assert.equal(f.run('reviewUpdatesAvailable'), true);
  assert.deepEqual(f.urls(), items.map((item) => item.url));
});

test("Focus: duplicate pagination is deduplicated and storage failures are harmless", async () => {
  const f = focusFixture();
  f.run('globalThis.sessionStorage = { setItem() { throw Error("blocked"); } };');
  f.run('renderGalleryCards([fixtureItems[1]], true)');
  assert.deepEqual(f.urls(), items.map((item) => item.url));
  assert.equal(f.run('recommendationsEl.children.length'), 1);
});

test("Focus: shortcuts ignore composition, editable targets and repeat", async () => {
  const f = focusFixture();
  f.run(`globalThis.fixtureKey = { key: 's', target: { closest: () => null }, preventDefault() {} };`);
  for (const flag of ['repeat', 'isComposing', 'ctrlKey', 'altKey', 'metaKey', 'shiftKey']) {
    f.run(`reviewKeydown({ ...fixtureKey, ${flag}: true })`);
  }
  f.run(`reviewKeydown({ ...fixtureKey, target: { closest: () => ({}) } })`);
  assert.equal(f.requests.length, 0);
  f.run('reviewKeydown(fixtureKey)');
  assert.equal(f.requests.length, 1);
  f.respond(0, undoResponse());
  await new Promise(setImmediate);
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
});

test("Focus: failed queue loads release navigation and retain the current gallery", async () => {
  const f = focusFixture();
  const loading = f.run('loadRecommendations()');
  f.requests[0].reject(new Error("Synthetic list failure"));
  await assert.rejects(loading);
  assert.equal(f.run('reviewBusy()'), false);
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  assert.deepEqual(f.urls(), items.map((item) => item.url));
  assert(f.buttons().every((button) => !button.disabled));
});

test("Focus: returning during a pending vote reloads Review after the commit", async () => {
  const f = focusFixture();
  const vote = f.run('skip(fixtureItems[0].url)');
  await f.run('setActiveView("favorite"); renderGalleryCards([]); setActiveView("review"); loadCurrentPage();');
  assert.equal(f.requests.length, 1);
  f.respond(0, undoResponse());
  await new Promise(setImmediate);
  assert.equal(f.requests.length, 2);
  f.respond(1, page([items[1]]));
  await vote;
  assert.deepEqual(f.urls(), [items[1].url]);
  assert.equal(f.run('reviewUndo'), null);
  assert.equal(f.run('reviewBusy()'), false);
});

test("Focus: queue-boundary loading can retry without opening or labeling", async () => {
  const f = focusFixture();
  await f.run('navigateReview(1); hasMoreRecommendations = true;');
  const first = f.run('navigateReview(1)');
  f.requests[0].reject(new Error("Synthetic page failure"));
  await assert.rejects(first);
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
  const retry = f.run('navigateReview(1)');
  f.respond(1, { items: [{ ...items[1], url: "https://example.test/g/c/token/" }], total: 3, has_more: false, next_offset: 3 });
  await retry;
  assert.equal(f.run('reviewCurrentUrl'), "https://example.test/g/c/token/");
  assert(f.requests.every((request) => !request.options || request.options.method !== "POST"));
});

test("Focus: explicit queue refresh clears undo and restores only eligible saved URLs", async () => {
  const f = focusFixture();
  const vote = f.run('skip(fixtureItems[0].url)');
  f.respond(0, undoResponse());
  await vote;
  const refresh = f.run('loadRecommendations()');
  assert.equal(f.run('reviewUndo'), null);
  f.respond(1, page([{ ...items[1], url: "https://example.test/g/new/token/" }]));
  await refresh;
  assert.equal(f.run('reviewCurrentUrl'), "https://example.test/g/new/token/");
});

test("Focus: returning during undo reloads eligible items instead of restoring an old view", async () => {
  const f = focusFixture();
  const vote = f.run('skip(fixtureItems[0].url)');
  f.respond(0, undoResponse());
  await vote;
  const undo = f.run('undoReviewRating()');
  await f.run('setActiveView("favorite"); renderGalleryCards([]); setActiveView("review"); loadCurrentPage();');
  f.respond(1, { ok: true });
  await new Promise(setImmediate);
  assert.equal(f.requests.length, 3);
  f.respond(2, page());
  await undo;
  assert.deepEqual(f.urls(), items.map((item) => item.url));
  assert.equal(f.run('reviewBusy()'), false);
  assert.equal(f.run('reviewUndo'), null);
});

test("Focus: metadata refresh clears undo and failure releases all controls", async () => {
  const f = focusFixture();
  const vote = f.run('skip(fixtureItems[0].url)');
  f.respond(0, undoResponse());
  await vote;
  const refresh = f.run('refreshGalleryMetadata(fixtureItems[1].url)');
  assert.equal(f.run('reviewUndo'), null);
  assert.equal(f.run('reviewBusy()'), true);
  await f.run('navigateReview(-1)');
  f.requests[1].reject(new Error("Synthetic metadata failure"));
  await assert.rejects(refresh);
  assert.equal(f.run('reviewBusy()'), false);
  assert(f.buttons(items[1].url).every((button) => !button.disabled));
});

for (const action of [
  'markGallery(fixtureItems[0].url, "favorite")',
  'markGallery(fixtureItems[0].url, "ban")',
  "clearMark(fixtureItems[0].url)",
  "clearRating(fixtureItems[0].url)",
  'classifyGallery(fixtureItems[0].url, "updates")',
]) {
  test(`${action}: a stale list cannot overwrite the completed action`, async () => {
    const f = fixture();
    const loading = f.run("loadRecommendations()");
    const submitting = f.run(action);
    f.respond(1, { ok: true, removed: 1, removed_gallery_url: items[0].url });
    await submitting;
    f.respond(0, page());
    await loading;
    assert.deepEqual(f.urls(), [items[1].url]);
  });
}

for (const view of ["review", "low-interest", "continuing-updates"]) {
  test(`${view}: classification advances locally without loading or replacing queues`, async () => {
    const f = view === "review" ? focusFixture() : fixture(view);
    const saving = f.run('classifyGallery(fixtureItems[0].url, "updates")');
    f.respond(0, { ok: true, classification: "updates", review: page([items[0]]), updates: page() });
    await saving;
    assert.equal(f.requests.length, 1);
    assert.deepEqual(f.urls(), [items[1].url]);
    assert.equal(f.run('reviewBusy()'), false);
  });
}

test("Focus: failed classification retains selection; duplicate/retry guards release after commit", async () => {
  const f = focusFixture();
  const first = f.run('classifyGallery(fixtureItems[0].url, "updates")');
  await f.run('classifyGallery(fixtureItems[0].url, "updates"); navigateReview(1);');
  assert.equal(f.requests.length, 1);
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  f.requests[0].reject(new Error("Synthetic classification failure"));
  await assert.rejects(first);
  assert(f.buttons().every((button) => !button.disabled));
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  const retry = f.run('classifyGallery(fixtureItems[0].url, "updates")');
  f.respond(1, { ok: true, classification: "updates" });
  await retry;
  assert.equal(f.run('reviewCurrentUrl'), items[1].url);
  assert.equal(f.run('reviewUndo'), null);
});

test("Focus: Use Auto clears the override without waiting for a queue rebuild", async () => {
  const f = focusFixture();
  f.run('renderedGalleryItems[0].classification_override = "review";');
  const saving = f.run('classifyGallery(fixtureItems[0].url, "auto")');
  f.respond(0, { ok: true, classification: "auto" });
  await saving;
  assert.equal(f.requests.length, 1);
  assert.equal(f.run('currentReviewItem().classification_override'), null);
  assert.equal(f.run('reviewCurrentUrl'), items[0].url);
  assert.equal(f.run('reviewUpdatesAvailable'), true);
  assert.equal(f.run('reviewBusy()'), false);
});

test("classification completed after a view switch cannot apply the old queue", async () => {
  const f = focusFixture();
  const saving = f.run('classifyGallery(fixtureItems[0].url, "updates")');
  f.run('setActiveView("favorite"); renderGalleryCards([fixtureItems[1]]);');
  f.respond(0, { ok: true, classification: "updates", review: page() });
  await new Promise(setImmediate);
  assert(f.requests[1].url.startsWith("/api/marks?"));
  f.respond(1, page([items[1]]));
  await saving;
  assert.deepEqual(f.urls(), [items[1].url]);
});
