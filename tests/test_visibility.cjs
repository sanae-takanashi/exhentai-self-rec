const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

function fixture() {
  let observer, mutation, visibilityListener;
  let id = 0;
  const timers = new Map();
  const sent = [];
  const document = {
    hidden: false, body: {},
    addEventListener: (_, callback) => { visibilityListener = callback; },
    removeEventListener: () => {},
  };
  const context = {
    document,
    setTimeout: (fn, delay) => { assert.equal(delay, 1000); timers.set(++id, fn); return id; },
    clearTimeout: (key) => timers.delete(key),
    IntersectionObserver: class {
      constructor(callback, options) {
        observer = this;
        this.callback = callback;
        assert.deepEqual(Array.from(options.threshold), [0, 0.5]);
        assert.equal(options.rootMargin, "0px");
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
    MutationObserver: class {
      constructor(callback) { mutation = callback; }
      observe() {}
      disconnect() {}
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("static/visibility.js", "utf8"), context);
  const element = { isConnected: true };
  context.observeVisibleCards([{ element, gallery_url: "test" }], async (url) => sent.push(url));
  return {
    sent, timers, element,
    ratio: (ratio) => observer.callback([{ target: element, isIntersecting: ratio > 0, intersectionRatio: ratio }]),
    hidden: (hidden) => { document.hidden = hidden; visibilityListener(); },
    detach: () => { element.isConnected = false; mutation(); },
    tick: async () => {
      const callbacks = [...timers.values()];
      timers.clear();
      for (const callback of callbacks) await callback();
    },
  };
}

test("offscreen and partial cards are not visible; full dwell emits once", async () => {
  const f = fixture();
  f.ratio(0.2);
  assert.equal(f.timers.size, 0);
  f.ratio(0.6);
  assert.equal(f.timers.size, 1);
  await f.tick();
  f.ratio(1);
  await f.tick();
  assert.deepEqual(f.sent, ["test"]);
});

test("background and interrupted dwell restart rather than accumulate", async () => {
  const f = fixture();
  f.ratio(1);
  f.hidden(true);
  assert.equal(f.timers.size, 0);
  await f.tick();
  assert.equal(f.sent.length, 0);
  f.hidden(false);
  assert.equal(f.timers.size, 1);
  f.ratio(0);
  assert.equal(f.timers.size, 0);
  f.ratio(1);
  await f.tick();
  assert.deepEqual(f.sent, ["test"]);
});

test("detached cards cancel timers and cannot become visible", async () => {
  const f = fixture();
  f.ratio(1);
  f.detach();
  await f.tick();
  assert.equal(f.sent.length, 0);
});
