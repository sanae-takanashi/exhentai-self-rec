"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let items = [], catalogReady = false, serial = 0, controller = null;
  const reasons = {
    rebuild_required: "The current index has no page vectors. Rebuild it in a new directory using the verified offline experiment, then configure it for loading. Existing pages remain available for preview.",
    page_not_indexed: "This sample page has no indexed vector. Select another page.",
    not_configured: "No local page library is configured.",
    invalid_library: "The local page library failed validation. Search is unavailable."
  };
  function status(id, message, error = false) {
    $(id).textContent = message;
    $(id).classList.toggle("error", error);
  }
  function invalidate() {
    serial++;
    if (controller) controller.abort();
    controller = null;
    $("results").replaceChildren();
    status("search-status", "");
  }
  function card(item, page, cosine) {
    const node = document.createElement("article");
    node.className = "card";
    const image = document.createElement("img");
    image.src = page.src;
    image.alt = `${item.title}, page ${page.position}`;
    image.loading = "lazy";
    image.addEventListener("error", () => {
      image.hidden = true;
      const warning = document.createElement("p");
      warning.textContent = "The local image could not be read or failed validation.";
      warning.className = "error";
      node.append(warning);
    }, { once: true });
    const heading = document.createElement("h3");
    heading.textContent = item.title;
    const detail = document.createElement("p");
    detail.textContent = `Page ${page.position} / ${item.image_count} · ${item.resolution} · ${item.pages.length} sample pages`;
    node.append(image, heading, detail);
    if (cosine !== undefined) {
      const score = document.createElement("p");
      score.textContent = `Cosine similarity ${cosine.toFixed(4)} (not a probability)`;
      node.append(score);
    }
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open original gallery";
      node.append(link);
    }
    return node;
  }
  function selection() {
    const item = items.find((row) => row.key === $("gallery").value);
    return { item, page: item?.pages.find((row) => row.position === Number($("page").value)) };
  }
  function showQuery() {
    invalidate();
    const { item, page } = selection();
    $("query").replaceChildren(...(page ? [card(item, page)] : []));
    $("search").disabled = !catalogReady || !page?.searchable;
    if (page && !page.searchable && catalogReady) status("search-status", reasons.page_not_indexed);
  }
  function chooseGallery() {
    const item = items.find((row) => row.key === $("gallery").value);
    $("page").replaceChildren();
    for (const page of item?.pages || []) {
      $("page").add(new Option(`Page ${page.position}${page.searchable ? "" : " (preview only)"}`, String(page.position)));
    }
    $("page").disabled = !item;
    showQuery();
  }
  async function readJSON(url, signal) {
    const response = await fetch(url, { signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}). Retry or check the local index.`);
    return response.json();
  }
  async function load() {
    invalidate();
    const request = serial;
    controller = new AbortController();
    catalogReady = false;
    items = [];
    $("gallery").replaceChildren();
    $("page").replaceChildren();
    $("query").replaceChildren();
    $("gallery").disabled = $("page").disabled = $("search").disabled = true;
    status("catalog-status", "Loading local catalog…");
    try {
      const data = await readJSON("/api/local-pages", controller.signal);
      if (request !== serial) return;
      if (!data.available) { status("catalog-status", reasons[data.reason] || "The local page library is unavailable.", true); return; }
      items = data.items;
      catalogReady = Boolean(data.page_search?.available);
      for (const item of items) $("gallery").add(new Option(`${item.title} · ${item.resolution}`, item.key));
      $("gallery").disabled = items.length === 0;
      status("catalog-status", catalogReady
        ? `${items.length} local entries, ${data.page_search.indexed_pages} indexed sample pages. Collected: ${data.collected_at || "Unknown"}.`
        : reasons[data.page_search?.reason] || reasons.rebuild_required);
      chooseGallery();
    } catch (error) {
      if (request === serial && error.name !== "AbortError") status("catalog-status", error.message, true);
    }
  }
  $("gallery").addEventListener("change", chooseGallery);
  $("page").addEventListener("change", showQuery);
  $("reload").addEventListener("click", load);
  $("search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const { item, page } = selection();
    if (!catalogReady || !page?.searchable) return;
    invalidate();
    const request = serial;
    controller = new AbortController();
    $("search").disabled = true;
    status("search-status", "Comparing local sample pages…");
    try {
      const query = new URLSearchParams({ key: item.key, position: String(page.position), limit: "12" });
      const data = await readJSON(`/api/local-pages/search?${query}`, controller.signal);
      if (request !== serial) return;
      if (!data.available) { status("search-status", reasons[data.reason] || "Search is currently unavailable for this page.", true); return; }
      $("query").replaceChildren(card(data.query.item, data.query.page));
      $("results").replaceChildren(...data.items.map((hit) => card(hit.item, hit.page, hit.cosine)));
      status("search-status", data.items.length
        ? `Showing ${data.items.length} / ${data.candidate_count} deduplicated candidate pages, from ${data.indexed_pages} local indexed sample pages only.`
        : "No other indexed sample pages match. Unindexed pages may still contain similar content.");
    } catch (error) {
      if (request === serial && error.name !== "AbortError") status("search-status", error.message, true);
    } finally {
      if (request === serial) $("search").disabled = !catalogReady || !selection().page?.searchable;
    }
  });
  load();
})();
