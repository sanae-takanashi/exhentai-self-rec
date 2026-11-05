"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let items = [], catalogReady = false, serial = 0, controller = null;
  const reasons = {
    rebuild_required: "当前索引没有单页向量。请使用已验证的离线实验重新构建到新的目录，再配置加载；原有页面仍可预览。",
    page_not_indexed: "这张样本页没有向量，请选择另一页。",
    not_configured: "尚未配置本地页面库。",
    invalid_library: "本地页面库验证失败，无法搜索。"
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
    image.alt = `${item.title}，第 ${page.position} 页`;
    image.loading = "lazy";
    image.addEventListener("error", () => {
      image.hidden = true;
      const warning = document.createElement("p");
      warning.textContent = "本地图片无法读取或验证失败。";
      warning.className = "error";
      node.append(warning);
    }, { once: true });
    const heading = document.createElement("h3");
    heading.textContent = item.title;
    const detail = document.createElement("p");
    detail.textContent = `第 ${page.position} / ${item.image_count} 页 · ${item.resolution} · ${item.pages.length} 张抽样页`;
    node.append(image, heading, detail);
    if (cosine !== undefined) {
      const score = document.createElement("p");
      score.textContent = `余弦相似度 ${cosine.toFixed(4)}（不是概率）`;
      node.append(score);
    }
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "打开原站画廊";
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
      $("page").add(new Option(`第 ${page.position} 页${page.searchable ? "" : "（仅预览）"}`, String(page.position)));
    }
    $("page").disabled = !item;
    showQuery();
  }
  async function readJSON(url, signal) {
    const response = await fetch(url, { signal, cache: "no-store" });
    if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}），请重试或检查本地索引。`);
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
    status("catalog-status", "正在读取本地目录…");
    try {
      const data = await readJSON("/api/local-pages", controller.signal);
      if (request !== serial) return;
      if (!data.available) { status("catalog-status", reasons[data.reason] || "本地页面库不可用。", true); return; }
      items = data.items;
      catalogReady = Boolean(data.page_search?.available);
      for (const item of items) $("gallery").add(new Option(`${item.title} · ${item.resolution}`, item.key));
      $("gallery").disabled = items.length === 0;
      status("catalog-status", catalogReady
        ? `${items.length} 个本地条目，${data.page_search.indexed_pages} 张已索引样本页。采集时间：${data.collected_at || "未知"}。`
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
    status("search-status", "正在比较本地样本页…");
    try {
      const query = new URLSearchParams({ key: item.key, position: String(page.position), limit: "12" });
      const data = await readJSON(`/api/local-pages/search?${query}`, controller.signal);
      if (request !== serial) return;
      if (!data.available) { status("search-status", reasons[data.reason] || "这张页面暂时无法搜索。", true); return; }
      $("query").replaceChildren(card(data.query.item, data.query.page));
      $("results").replaceChildren(...data.items.map((hit) => card(hit.item, hit.page, hit.cosine)));
      status("search-status", data.items.length
        ? `展示 ${data.items.length} / ${data.candidate_count} 张去重候选页；范围仅为 ${data.indexed_pages} 张本地已索引样本页。`
        : "没有其他可匹配的已索引样本页。这不代表未收录页面没有相似内容。");
    } catch (error) {
      if (request === serial && error.name !== "AbortError") status("search-status", error.message, true);
    } finally {
      if (request === serial) $("search").disabled = !catalogReady || !selection().page?.searchable;
    }
  });
  load();
})();
