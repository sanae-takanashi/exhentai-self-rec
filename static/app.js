const statusEl = document.querySelector("#status");
const cookieEl = document.querySelector("#cookie");
const cookiePreviewEl = document.querySelector("#cookiePreview");
const tagsEl = document.querySelector("#tags");
const pagesEl = document.querySelector("#pages");
const detailLimitEl = document.querySelector("#detailLimit");
const learnedLimitEl = document.querySelector("#learnedLimit");
const candidateLimitEl = document.querySelector("#candidateLimit");
const sampleExtraPagesEl = document.querySelector("#sampleExtraPages");
const minutesEl = document.querySelector("#minutes");
const networkProxyEl = document.querySelector("#networkProxy");
const visualEncoderEl = document.querySelector("#visualEncoder");
const dinov2DeviceEl = document.querySelector("#dinov2Device");
const autoRefreshEl = document.querySelector("#autoRefresh");
const recommendationsEl = document.querySelector("#recommendations");
const queryEl = document.querySelector("#query");
const localFilterEl = document.querySelector("#localFilter");
const importFileEl = document.querySelector("#importFile");
const replaceImportEl = document.querySelector("#replaceImport");
const loadMoreBtn = document.querySelector("#loadMoreBtn");
const modelDialog = document.querySelector("#modelDialog");
const dialogTitle = document.querySelector("#dialogTitle");
const modelBody = document.querySelector("#modelBody");
const refreshStatusEl = document.querySelector("#refreshStatus");
const viewTitleEl = document.querySelector("#viewTitle");
const viewSubtitleEl = document.querySelector("#viewSubtitle");
const viewTabs = [...document.querySelectorAll("[data-view]")];
let nextRecommendationOffset = 0;
let hasMoreRecommendations = false;
let lastRenderedFetchId = null;
let currentView = "review";
const recommendationLimit = 40;
const pendingFeedbackUrls = new Set();
let renderedGalleryUrls = [];
let visualDefaultEncoder = "simple";
let visualEmbeddingVersion = "canvas-rgb-8x8-v1";
let visualFallbackEncoder = "simple";
let visualFallbackVersion = "canvas-rgb-8x8-v1";
const visualGridSize = 8;
const visualMaxSampleImages = 10;
const visualMaxConcurrent = 2;
const visualQueuedUrls = new Set();
const visualSavedUrls = new Set();
const visualQueue = [];
let visualActive = 0;
let visualRefreshTimer = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

function thumbnailSrc(item) {
  const params = new URLSearchParams({
    url: item.thumb_url,
    gallery_url: item.url,
  });
  return `/thumb?${params.toString()}`;
}

function sampleSrc(galleryUrl, index) {
  const params = new URLSearchParams({
    gallery_url: galleryUrl,
    sample: String(index),
  });
  return `/thumb?${params.toString()}`;
}

function visualImageSources(item) {
  const sources = [];
  if (item.thumb_url) {
    sources.push(thumbnailSrc(item));
  }
  for (const [index] of (item.samples || []).slice(0, visualMaxSampleImages).entries()) {
    sources.push(sampleSrc(item.url, index));
  }
  return sources;
}

function visualImageUrls(item) {
  const urls = [];
  if (item.thumb_url) {
    urls.push(item.thumb_url);
  }
  for (const thumb of (item.samples || []).slice(0, visualMaxSampleImages)) {
    // Sprite-frame samples are objects the DINOv2 server path cannot fetch
    // directly; the cover plus any standalone sample URLs are enough for it.
    if (typeof thumb === "string" && thumb) {
      urls.push(thumb);
    }
  }
  return urls;
}

function queueVisualEmbedding(item) {
  if (!item || !item.url || visualQueuedUrls.has(item.url) || visualSavedUrls.has(item.url)) {
    return;
  }
  if (item.visual_embedding_version === visualEmbeddingVersion && item.visual_embedding_at) {
    visualSavedUrls.add(item.url);
    return;
  }
  const sources = visualImageSources(item);
  const imageUrls = visualImageUrls(item);
  if (!sources.length && !imageUrls.length) {
    return;
  }
  visualQueuedUrls.add(item.url);
  visualQueue.push({ galleryUrl: item.url, sources, imageUrls });
  runVisualQueue();
}

function runVisualQueue() {
  while (visualActive < visualMaxConcurrent && visualQueue.length) {
    const task = visualQueue.shift();
    visualActive += 1;
    saveVisualEmbedding(task)
      .catch(() => {})
      .finally(() => {
        visualActive -= 1;
        runVisualQueue();
      });
  }
}

async function saveVisualEmbedding(task) {
  const dinov2Saved = await saveDinov2Embedding(task);
  if (dinov2Saved) {
    visualSavedUrls.add(task.galleryUrl);
    scheduleVisualRefresh();
    return;
  }
  const vectors = [];
  for (const source of task.sources) {
    try {
      vectors.push(await imageEmbedding(source));
    } catch (_) {
      // Some image hosts return occasional broken thumbnails; one usable image is enough.
    }
  }
  const embedding = averageVectors(vectors);
  if (!embedding) {
    return;
  }
  await api("/api/visual", {
    method: "POST",
    body: JSON.stringify({
      gallery_url: task.galleryUrl,
      encoder: visualFallbackEncoder,
      version: visualFallbackVersion,
      embedding,
    }),
  });
  visualSavedUrls.add(task.galleryUrl);
  scheduleVisualRefresh();
}

async function saveDinov2Embedding(task) {
  if (visualDefaultEncoder !== "dinov2" || !task.imageUrls.length) {
    return false;
  }
  try {
    const payload = await api("/api/visual", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: task.galleryUrl,
        encoder: "dinov2",
        image_urls: task.imageUrls,
      }),
    });
    return Boolean(payload.ok && payload.version === visualEmbeddingVersion);
  } catch (_) {
    return false;
  }
}

async function imageEmbedding(source) {
  const image = await loadImage(source);
  const canvas = imageEmbedding.canvas || document.createElement("canvas");
  imageEmbedding.canvas = canvas;
  canvas.width = visualGridSize;
  canvas.height = visualGridSize;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.clearRect(0, 0, visualGridSize, visualGridSize);
  context.drawImage(image, 0, 0, visualGridSize, visualGridSize);
  const pixels = context.getImageData(0, 0, visualGridSize, visualGridSize).data;
  const vector = [];
  for (let index = 0; index < pixels.length; index += 4) {
    vector.push(pixels[index] / 255, pixels[index + 1] / 255, pixels[index + 2] / 255);
  }
  return normalizeVector(vector);
}

function loadImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = source;
  });
}

function averageVectors(vectors) {
  if (!vectors.length) {
    return null;
  }
  const length = vectors[0].length;
  const sum = new Array(length).fill(0);
  let count = 0;
  for (const vector of vectors) {
    if (vector.length !== length) {
      continue;
    }
    for (let index = 0; index < length; index += 1) {
      sum[index] += vector[index];
    }
    count += 1;
  }
  if (!count) {
    return null;
  }
  return normalizeVector(sum.map((value) => value / count));
}

function normalizeVector(vector) {
  const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  if (!norm) {
    return vector;
  }
  return vector.map((value) => Number((value / norm).toFixed(6)));
}

function scheduleVisualRefresh() {
  if (visualRefreshTimer) {
    return;
  }
  visualRefreshTimer = setTimeout(() => {
    visualRefreshTimer = null;
    if (currentView !== "preview") {
      loadCurrentPage().catch(() => {});
    }
  }, 5000);
}

function bootstrapText(tags) {
  return tags.map((item) => `${item.tag}:${item.weight}`).join("\n");
}

function applyVisualSettings(visual) {
  if (!visual) {
    return;
  }
  visualDefaultEncoder = visual.default_encoder || "simple";
  visualEmbeddingVersion = visual.default_version || visualFallbackVersion;
  visualFallbackEncoder = visual.fallback_encoder || "simple";
  visualFallbackVersion = visual.fallback_version || "canvas-rgb-8x8-v1";
}

async function loadSettings() {
  const settings = await api("/api/settings");
  applyVisualSettings(settings.visual);
  cookiePreviewEl.textContent = cookiePreviewText(settings);
  tagsEl.value = bootstrapText(settings.bootstrap_tags);
  pagesEl.value = settings.fetch_pages;
  detailLimitEl.value = settings.detail_fetch_limit;
  learnedLimitEl.value = settings.learned_query_limit;
  candidateLimitEl.value = settings.recommend_candidate_limit;
  sampleExtraPagesEl.value = settings.sample_extra_pages;
  minutesEl.value = settings.refresh_interval_minutes;
  networkProxyEl.value = settings.network_proxy || "";
  visualEncoderEl.value = settings.visual_encoder || visualDefaultEncoder;
  dinov2DeviceEl.value = settings.dinov2_device || "auto";
  autoRefreshEl.checked = settings.auto_refresh;
}

async function loadStatus() {
  const payload = await api("/api/status");
  applyVisualSettings(payload.visual);
  renderStatus(payload);
  await reloadRecommendationsAfterFetch(payload);
  return payload;
}

async function previewPlan() {
  const query = queryEl.value.trim();
  const payload = await api(`/api/plan${query ? `?query=${encodeURIComponent(query)}` : ""}`);
  renderStatus({ fetch: { running: false }, last_fetch: null, settings: {}, plan: payload });
  setStatus(`${payload.entries.length} planned fetch queries`);
}

async function saveSettings() {
  setStatus("Saving settings");
  const payload = {
    cookie_header: cookieEl.value,
    bootstrap_tags_raw: tagsEl.value,
    fetch_pages: Number(pagesEl.value),
    detail_fetch_limit: Number(detailLimitEl.value),
    learned_query_limit: Number(learnedLimitEl.value),
    recommend_candidate_limit: Number(candidateLimitEl.value),
    sample_extra_pages: Number(sampleExtraPagesEl.value),
    refresh_interval_minutes: Number(minutesEl.value),
    network_proxy: networkProxyEl.value.trim(),
    visual_encoder: visualEncoderEl.value,
    dinov2_device: dinov2DeviceEl.value.trim(),
    auto_refresh: autoRefreshEl.checked,
  };
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  cookieEl.value = "";
  cookiePreviewEl.textContent = cookiePreviewText(settings);
  await loadStatus();
  setStatus("Settings saved");
}

function cookiePreviewText(settings) {
  if (!settings.has_cookie) {
    return "No cookie stored.";
  }
  let text = `Stored cookie keys: ${settings.cookie_preview}`;
  if (settings.cookie_missing_keys && settings.cookie_missing_keys.length) {
    text += ` Missing common keys: ${settings.cookie_missing_keys.join(", ")}`;
  }
  if (settings.last_access_check) {
    text += ` Last check: ${settings.last_access_check.message}`;
  }
  return text;
}

function viewCopy(view) {
  if (view === "history") {
    return {
      title: "Reaction History",
      subtitle: "Galleries you already reacted to, newest reaction first.",
      empty: "No reaction history yet.",
      loaded: "history items loaded",
    };
  }
  if (view === "preview") {
    return {
      title: "Model Preview",
      subtitle: "Read-only full model ranking, including galleries with reactions.",
      empty: "No model recommendations yet.",
      loaded: "preview recommendations loaded",
    };
  }
  return {
    title: "Review Queue",
    subtitle: "Unrated galleries ready for feedback.",
    empty: "No unrated galleries yet. Save cookies and bootstrap tags, then fetch.",
    loaded: "review recommendations loaded",
  };
}

function setActiveView(view) {
  currentView = view;
  for (const tab of viewTabs) {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  const copy = viewCopy(view);
  viewTitleEl.textContent = copy.title;
  viewSubtitleEl.textContent = copy.subtitle;
}

async function loadCurrentPage(offset = 0, append = false) {
  if (currentView === "history") {
    return loadReactionHistory(offset, append);
  }
  return loadRecommendations(offset, append);
}

async function loadReactionHistory(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/reactions?limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

async function loadRecommendations(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const includeRated = currentView === "preview" ? "1" : "0";
  const payload = await api(
    `/api/recommendations?include_rated=${includeRated}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

async function fetchNew(query = "") {
  setStatus("Fetching ExHentai pages");
  const payload = await api("/api/fetch", {
    method: "POST",
    body: JSON.stringify({
      query,
      include_rated: false,
      filter_text: localFilterEl.value.trim(),
    }),
  });
  await loadCurrentPage();
  if (payload.last_fetch) {
    renderStatus({ fetch: { running: false }, last_fetch: payload.last_fetch, settings: {} });
  }
  if (payload.errors.length) {
    setStatus(`Fetched ${payload.fetched}; errors: ${payload.errors.join(" | ")}`, true);
  } else {
    setStatus(`Fetched ${payload.fetched}; stored ${payload.stored}; enriched ${payload.enriched}`);
  }
}

async function enrichTopRecommendations() {
  setStatus("Enriching recommended galleries");
  const payload = await api("/api/enrich", {
    method: "POST",
    body: JSON.stringify({
      include_rated: false,
      filter_text: localFilterEl.value.trim(),
      limit: Number(detailLimitEl.value),
    }),
  });
  await loadCurrentPage();
  if (payload.last_fetch) {
    renderStatus({ fetch: { running: false }, last_fetch: payload.last_fetch, settings: {} });
  }
  if (payload.errors.length) {
    setStatus(`Enriched ${payload.enriched}; errors: ${payload.errors.join(" | ")}`, true);
  } else {
    setStatus(`Enriched ${payload.enriched} recommended galleries`);
  }
}

async function refreshThumbnails() {
  const galleryUrls = [...renderedGalleryUrls];
  if (!galleryUrls.length) {
    setStatus("No galleries on the page to refresh", true);
    return;
  }
  setStatus(`Refreshing thumbnails for ${galleryUrls.length} galleries`);
  const payload = await api("/api/refresh-thumbs", {
    method: "POST",
    body: JSON.stringify({
      gallery_urls: galleryUrls,
      include_rated: false,
      filter_text: localFilterEl.value.trim(),
    }),
  });
  await loadCurrentPage();
  if (payload.errors.length) {
    setStatus(`Refreshed ${payload.updated} thumbnails; errors: ${payload.errors.join(" | ")}`, true);
  } else {
    setStatus(`Refreshed ${payload.updated} thumbnails`);
  }
}

async function downloadDinov2Model() {
  setStatus("Downloading DINOv2 model (this can take a while)");
  const payload = await api("/api/visual/download", {
    method: "POST",
    body: JSON.stringify({}),
  });
  applyVisualSettings(payload.visual);
  await loadStatus();
  if (payload.ok) {
    setStatus(`DINOv2 model ready (${payload.model})`);
  } else {
    setStatus(payload.reason || "DINOv2 model download failed", true);
  }
}

async function checkLogin() {
  setStatus("Checking ExHentai access");
  const payload = await api("/api/check", {
    method: "POST",
    body: JSON.stringify({}),
  });
  setStatus(payload.message, !payload.ok);
  await loadSettings();
  await loadStatus();
}

async function clearCookie() {
  if (!confirm("Clear the stored ExHentai cookie?")) {
    return;
  }
  setStatus("Clearing stored cookie");
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ clear_cookie: true }),
  });
  cookieEl.value = "";
  await loadSettings();
  await loadStatus();
  setStatus("Stored cookie cleared");
}

async function vote(galleryUrl, voteValue) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus(voteValue > 0 ? "Recording upvote" : "Recording downvote");
    const payload = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        vote: voteValue,
        include_rated: false,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(feedbackStatusMessage("Vote recorded", payload));
  });
}

async function score(galleryUrl, scoreValue) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus(`Recording score ${scoreValue}`);
    const payload = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        score: scoreValue,
        include_rated: false,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(feedbackStatusMessage("Score recorded", payload));
  });
}

async function skip(galleryUrl) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus("Skipping gallery");
    const payload = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        score: 3,
        include_rated: false,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(feedbackStatusMessage("Gallery skipped", payload));
  });
}

async function clearRating(galleryUrl) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus("Clearing rating");
    const payload = await api("/api/feedback/clear", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        include_rated: false,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(payload.removed ? "Rating cleared" : "No rating to clear");
  });
}

async function applyFeedbackResult(payload) {
  if (currentView === "history") {
    await loadReactionHistory();
  } else {
    applyGalleryPage(payload);
  }
}

async function showModel() {
  const payload = await api("/api/model");
  dialogTitle.textContent = "Learned Model";
  modelBody.textContent = JSON.stringify(payload, null, 2);
  modelDialog.showModal();
}

async function showFeedbackHistory(galleryUrl) {
  const payload = await api(`/api/feedback?gallery_url=${encodeURIComponent(galleryUrl)}`);
  dialogTitle.textContent = "Feedback History";
  modelBody.textContent = JSON.stringify(payload, null, 2);
  modelDialog.showModal();
}

async function retrain() {
  setStatus("Retraining model");
  const payload = await api("/api/retrain", {
    method: "POST",
    body: JSON.stringify({ include_rated: false, filter_text: localFilterEl.value.trim() }),
  });
  await loadCurrentPage();
  modelBody.textContent = JSON.stringify(payload.model, null, 2);
  setStatus("Model retrained");
}

async function resetLibrary() {
  if (
    !confirm(
      "Reset data?\n\nThis permanently deletes all fetched galleries, your votes, the learned model, and fetch history.\n\nYour cookie and bootstrap tags are kept. This cannot be undone."
    )
  ) {
    return;
  }
  setStatus("Resetting data");
  const payload = await api("/api/reset", {
    method: "POST",
    body: JSON.stringify({}),
  });
  applyGalleryPage(payload);
  await loadStatus();
  const removed = payload.removed || {};
  setStatus(`Data reset; removed ${removed.galleries || 0} galleries and ${removed.feedback || 0} votes`);
}

async function exportPreferences() {
  setStatus("Exporting preferences");
  const payload = await api("/api/export");
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `exh-rec-preferences-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  setStatus("Preferences exported");
}

async function importPreferences(file) {
  if (!file) return;
  setStatus("Importing preferences");
  const text = await file.text();
  const data = JSON.parse(text);
  const payload = await api("/api/import", {
    method: "POST",
    body: JSON.stringify({ data, replace: replaceImportEl.checked }),
  });
  await loadSettings();
  await loadCurrentPage();
  modelBody.textContent = JSON.stringify(payload.model, null, 2);
  setStatus(
    `Imported ${payload.imported.feedback} feedback, ${payload.imported.bootstrap_tags} bootstrap tags`
  );
}

function renderGalleryCards(items, append = false) {
  const mode = currentView;
  if (!append) {
    renderedGalleryUrls = [];
  }
  for (const item of items) {
    if (item && item.url) {
      renderedGalleryUrls.push(item.url);
    }
  }
  if (!append && !items.length) {
    recommendationsEl.innerHTML = `<div class="hint">${escapeHtml(viewCopy(mode).empty)}</div>`;
    return;
  }
  if (!append) {
    recommendationsEl.innerHTML = "";
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "card";
    const thumb = item.thumb_url
      ? `<img src="${escapeAttr(thumbnailSrc(item))}" alt="" loading="lazy">`
      : `<span>No thumbnail</span>`;
    const samples = item.samples || [];
    const samplesPreview = samples.length
      ? `<div class="samples">${samples
          .map((thumb, index) => `<img src="${escapeAttr(sampleSrc(item.url, index))}" alt="" loading="lazy">`)
          .join("")}</div>`
      : "";
    const tags = (item.tags || []).slice(0, 8).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("");
    const reasons = (item.reasons || []).map((reason) => `<span class="reason">${escapeHtml(reason)}</span>`).join(" ");
    const userFeedback = item.rated
      ? item.user_score
        ? `Your score ${item.user_score}`
        : `Your signal ${item.user_vote || 0}`
      : "No reaction";
    const detailStatus = item.detail_fetched_at ? "Full metadata" : "List metadata";
    const uploader = item.uploader ? `Uploader ${item.uploader}` : "Uploader unknown";
    const postedAt = item.posted_at ? `Posted ${item.posted_at}` : "";
    const reactionAt = item.feedback_created_at ? `Reacted ${item.feedback_created_at}` : "";
    const clearButton = item.rated && mode !== "preview"
      ? `<button class="clear" type="button" data-clear="1" data-url="${escapeAttr(item.url)}">Clear</button>`
      : "";
    const historyButton = item.rated && mode !== "preview"
      ? `<button class="clear" type="button" data-history="1" data-url="${escapeAttr(item.url)}">History</button>`
      : "";
    const feedbackActions = historyButton || clearButton ? `<div class="card-actions">${historyButton}${clearButton}</div>` : "";
    const feedbackControls = mode === "preview"
      ? ""
      : `<div class="votes">
          <button class="up" type="button" data-vote="1" data-url="${escapeAttr(item.url)}">Thumb up</button>
          <button class="skip" type="button" data-skip="1" data-url="${escapeAttr(item.url)}">Skip</button>
          <button class="down" type="button" data-vote="-1" data-url="${escapeAttr(item.url)}">Thumb down</button>
        </div>
        <div class="scorebar" aria-label="Score">
          ${[1, 2, 3, 4, 5]
            .map((value) => `<button type="button" data-score="${value}" data-url="${escapeAttr(item.url)}">${value}</button>`)
            .join("")}
        </div>
        ${feedbackActions}`;
    const pageCount = item.page_count ? ` · ${item.page_count} pages` : "";
    card.innerHTML = `
      <div class="media-preview">
        <div class="thumb">${thumb}</div>
        ${samplesPreview}
      </div>
      <div class="body">
        <a class="title" href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
        <div class="meta">${escapeHtml(item.category || "Unknown")} · score ${item.score}${escapeHtml(pageCount)}</div>
        <div class="meta">${escapeHtml([uploader, postedAt].filter(Boolean).join(" · "))}</div>
        <div class="meta">${escapeHtml(detailStatus)}</div>
        <div class="meta">${escapeHtml(userFeedback)}</div>
        ${reactionAt ? `<div class="meta">${escapeHtml(reactionAt)}</div>` : ""}
        <div class="pillrow">${tags}</div>
        <div class="reason">${reasons}</div>
        ${feedbackControls}
      </div>
    `;
    recommendationsEl.appendChild(card);
    if (mode !== "preview") {
      queueVisualEmbedding(item);
    }
  }
}

function feedbackStatusMessage(base, payload) {
  const enrichment = payload.feedback_enrichment || {};
  if (enrichment.status === "success") {
    return `${base}; full metadata learned`;
  }
  if (enrichment.status === "failed") {
    return `${base}; detail fetch failed`;
  }
  return base;
}

async function withPendingFeedback(galleryUrl, action) {
  if (pendingFeedbackUrls.has(galleryUrl)) {
    return;
  }
  pendingFeedbackUrls.add(galleryUrl);
  setGalleryFeedbackButtonsDisabled(galleryUrl, true);
  try {
    await action();
  } finally {
    pendingFeedbackUrls.delete(galleryUrl);
    setGalleryFeedbackButtonsDisabled(galleryUrl, false);
  }
}

function setGalleryFeedbackButtonsDisabled(galleryUrl, disabled) {
  for (const button of recommendationsEl.querySelectorAll("button[data-url]")) {
    if (button.dataset.url !== galleryUrl) {
      continue;
    }
    if (button.dataset.vote || button.dataset.score || button.dataset.skip || button.dataset.clear) {
      button.disabled = disabled;
    }
  }
}

function applyGalleryPage(payload, append = false) {
  renderGalleryCards(payload.items || [], append);
  nextRecommendationOffset = payload.next_offset || 0;
  hasMoreRecommendations = Boolean(payload.has_more);
  updateLoadMore(payload.total || 0);
  if (payload.last_fetch && payload.last_fetch.id) {
    lastRenderedFetchId = payload.last_fetch.id;
  }
}

async function reloadRecommendationsAfterFetch(statusPayload) {
  const fetchState = statusPayload.fetch || {};
  const lastFetch = statusPayload.last_fetch;
  if (fetchState.running || !lastFetch || !lastFetch.id || lastFetch.id === lastRenderedFetchId) {
    return;
  }
  lastRenderedFetchId = lastFetch.id;
  if (currentView !== "history") {
    await loadCurrentPage();
  }
}

function updateLoadMore(total = null) {
  loadMoreBtn.disabled = !hasMoreRecommendations;
  loadMoreBtn.textContent = hasMoreRecommendations
    ? "Load More"
    : total && total > 0
      ? "No More"
      : "Load More";
}

function renderStatus(payload) {
  const fetchState = payload.fetch || {};
  const last = payload.last_fetch;
  const rows = [
    ["State", fetchState.running ? `Fetching ${fetchState.fetched || 0}/${fetchState.stored || 0}` : "Idle"],
  ];
  if (last) {
    rows.push(["Last", `${last.status} at ${last.finished_at || last.started_at}`]);
    rows.push(["Fetched", `${last.fetched_count} fetched, ${last.stored_count} stored`]);
    rows.push(["Enriched", `${last.enriched_count || 0} detail pages`]);
    rows.push(["Queries", (last.queries || ["recent"]).map((query) => query || "recent").join(", ")]);
    if (last.errors && last.errors.length) {
      rows.push(["Errors", last.errors.join(" | ")]);
    }
  } else {
    rows.push(["Last", "No fetch yet"]);
  }
  if (payload.fetch_history && payload.fetch_history.length) {
    rows.push([
      "History",
      payload.fetch_history
        .slice(0, 5)
        .map((run) => `${run.status}:${run.fetched_count}/${run.enriched_count}`)
        .join(" | "),
    ]);
  }
  const plan = payload.plan;
  if (plan && plan.entries) {
    rows.push(["Plan", plan.entries.map((entry) => entry.query || "recent").join(", ")]);
    rows.push(["Scope", `${plan.pages} page(s), ${plan.detail_fetch_limit} details`]);
    rows.push(["Pool", `${plan.recommend_candidate_limit} local candidates`]);
  }
  if (payload.refresh) {
    rows.push(["Auto", payload.refresh.message]);
    if (payload.refresh.last_checked_at) {
      rows.push(["Checked", payload.refresh.last_checked_at]);
    }
    if (payload.refresh.next_check_at) {
      rows.push(["Next", payload.refresh.next_check_at]);
    }
    if (payload.refresh.last_error) {
      rows.push(["Auto Error", payload.refresh.last_error]);
    }
  }
  const access = payload.settings && payload.settings.last_access_check;
  if (access) {
    rows.push(["Access", `${access.ok ? "OK" : "Failed"} at ${access.checked_at}`]);
    rows.push(["Login", access.message]);
  }
  if (payload.settings && payload.settings.network_proxy_preview) {
    rows.push(["Proxy", payload.settings.network_proxy_preview]);
  }
  if (payload.visual) {
    rows.push(["Visual", `${payload.visual.default_encoder || "simple"} (${payload.visual.default_version || "unknown"})`]);
  }
  if (payload.visual && payload.visual.dinov2) {
    const dino = payload.visual.dinov2;
    rows.push(["DINOv2", `${dino.available ? "Available" : "Fallback"} on ${dino.device || dino.device_config || "auto"}`]);
    if (dino.cuda_available) {
      rows.push(["CUDA", `${dino.cuda_device_count || 0} device(s)${dino.cuda_device_name ? `, ${dino.cuda_device_name}` : ""}`]);
    }
    if (dino.error) {
      rows.push(["DINO Error", dino.error]);
    }
  }
  refreshStatusEl.innerHTML = rows
    .map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

document.querySelector("#saveBtn").addEventListener("click", () => saveSettings().catch((error) => setStatus(error.message, true)));
document.querySelector("#fetchBtn").addEventListener("click", () => fetchNew().catch((error) => setStatus(error.message, true)));
document.querySelector("#enrichBtn").addEventListener("click", () => enrichTopRecommendations().catch((error) => setStatus(error.message, true)));
document.querySelector("#refreshThumbsBtn").addEventListener("click", () => refreshThumbnails().catch((error) => setStatus(error.message, true)));
document.querySelector("#downloadDinov2Btn").addEventListener("click", () => downloadDinov2Model().catch((error) => setStatus(error.message, true)));
document.querySelector("#checkBtn").addEventListener("click", () => checkLogin().catch((error) => setStatus(error.message, true)));
document.querySelector("#clearCookieBtn").addEventListener("click", () => clearCookie().catch((error) => setStatus(error.message, true)));
document.querySelector("#searchFetchBtn").addEventListener("click", () => fetchNew(queryEl.value).catch((error) => setStatus(error.message, true)));
queryEl.addEventListener("change", () => previewPlan().catch((error) => setStatus(error.message, true)));
document.querySelector("#modelBtn").addEventListener("click", () => showModel().catch((error) => setStatus(error.message, true)));
document.querySelector("#retrainBtn").addEventListener("click", () => retrain().catch((error) => setStatus(error.message, true)));
document.querySelector("#exportBtn").addEventListener("click", () => exportPreferences().catch((error) => setStatus(error.message, true)));
document.querySelector("#importBtn").addEventListener("click", () => importFileEl.click());
document.querySelector("#resetBtn").addEventListener("click", () => resetLibrary().catch((error) => setStatus(error.message, true)));
loadMoreBtn.addEventListener("click", () => {
  if (!hasMoreRecommendations) return;
  loadCurrentPage(nextRecommendationOffset, true).catch((error) => setStatus(error.message, true));
});
importFileEl.addEventListener("change", () => {
  importPreferences(importFileEl.files[0]).catch((error) => setStatus(error.message, true));
  importFileEl.value = "";
});
localFilterEl.addEventListener("change", () => loadCurrentPage().catch((error) => setStatus(error.message, true)));
for (const tab of viewTabs) {
  tab.addEventListener("click", () => {
    setActiveView(tab.dataset.view);
    loadCurrentPage().catch((error) => setStatus(error.message, true));
  });
}
recommendationsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-vote]");
  if (button) {
    vote(button.dataset.url, Number(button.dataset.vote)).catch((error) => setStatus(error.message, true));
    return;
  }
  const scoreButton = event.target.closest("button[data-score]");
  if (scoreButton) {
    score(scoreButton.dataset.url, Number(scoreButton.dataset.score)).catch((error) => setStatus(error.message, true));
    return;
  }
  const skipButton = event.target.closest("button[data-skip]");
  if (skipButton) {
    skip(skipButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
  const clearButton = event.target.closest("button[data-clear]");
  if (clearButton) {
    clearRating(clearButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
  const historyButton = event.target.closest("button[data-history]");
  if (historyButton) {
    showFeedbackHistory(historyButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
});

loadSettings()
  .then(loadStatus)
  .then(() => {
    setActiveView(currentView);
    return loadCurrentPage();
  })
  .catch((error) => setStatus(error.message, true));

setInterval(() => {
  loadStatus().catch(() => {});
}, 10000);
