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

for (const action of [
  'markGallery(fixtureItems[0].url, "favorite")',
  'markGallery(fixtureItems[0].url, "ban")',
  "clearMark(fixtureItems[0].url)",
  "clearRating(fixtureItems[0].url)",
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
