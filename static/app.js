const statusEl = document.querySelector("#status");
const cookieEl = document.querySelector("#cookie");
const cookiePreviewEl = document.querySelector("#cookiePreview");
const tagsEl = document.querySelector("#tags");
const pagesEl = document.querySelector("#pages");
const staleFetchExtraPagesEl = document.querySelector("#staleFetchExtraPages");
const detailLimitEl = document.querySelector("#detailLimit");
const learnedLimitEl = document.querySelector("#learnedLimit");
const candidateLimitEl = document.querySelector("#candidateLimit");
const previewFreshnessWeightEl = document.querySelector("#previewFreshnessWeight");
const previewPostedAfterEl = document.querySelector("#previewPostedAfter");
const sampleExtraPagesEl = document.querySelector("#sampleExtraPages");
const requestIntervalEl = document.querySelector("#requestInterval");
const banPauseEl = document.querySelector("#banPause");
const minutesEl = document.querySelector("#minutes");
const networkProxyEl = document.querySelector("#networkProxy");
const languageFilterEl = document.querySelector("#languageFilter");
const modelModeEl = document.querySelector("#modelMode");
const reviewRequireBootstrapMatchEl = document.querySelector("#reviewRequireBootstrapMatch");
const visualEncoderEl = document.querySelector("#visualEncoder");
const dinov2DeviceEl = document.querySelector("#dinov2Device");
const autoRefreshEl = document.querySelector("#autoRefresh");
const recommendationsEl = document.querySelector("#recommendations");
const queryEl = document.querySelector("#query");
const localFilterEl = document.querySelector("#localFilter");
const defaultLocalFilterPlaceholder = localFilterEl.placeholder;
const backfillReviewParentsBtn = document.querySelector("#backfillReviewParentsBtn");
const historySearchBtn = document.querySelector("#historySearchBtn");
const backfillHistoryParentsBtn = document.querySelector("#backfillHistoryParentsBtn");
const recalcShortRepeatsBtn = document.querySelector("#recalcShortRepeatsBtn");
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
let reviewExploreSeed = "";
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
const staticTooltips = {
  fetchBtn: "Fetch gallery list pages using recent, bootstrap, and learned queries. Stores new local galleries.",
  enrichBtn: "Fetch full detail metadata and sample thumbnails for the current top recommendations.",
  refreshThumbsBtn: "Refresh cover thumbnails for galleries currently shown on the page.",
  retrainBtn: "Rebuild the recommendation model from your saved feedback, favorites, bans, and visual embeddings.",
  modelBtn: "Open the current learned model weights, counts, and visual model summary.",
  cookie: "Your ExHentai Cookie header. Leave blank when saving to keep the currently stored cookie.",
  checkBtn: "Test whether the stored cookie can access ExHentai gallery listings.",
  clearCookieBtn: "Remove the stored cookie and saved access-check result.",
  tags: "Seed tags for initial fetching and scoring. Use negative lines for dislikes and :weight for stronger signals.",
  pages: "Number of result pages to fetch for each query, from 1 to 5.",
  staleFetchExtraPages: "If the first max-page batch has no new galleries, fetch this many additional older pages.",
  detailLimit: "Maximum galleries per fetch to enrich with full detail metadata and sample thumbnails.",
  learnedLimit: "Maximum learned positive tags to add as extra remote fetch queries.",
  candidateLimit: "Number of local candidate galleries considered when ranking recommendations.",
  previewFreshnessWeight: "Freshness boost used only in Preview. Higher values push newer galleries above older strong matches.",
  previewPostedAfter: "Optional Preview cutoff. When set, Preview only shows galleries posted on or after this date.",
  sampleExtraPages: "Additional gallery sample pages to inspect for preview images on large galleries.",
  requestInterval: "Minimum delay in seconds between ExHentai-related network requests.",
  banPause: "Fallback pause in seconds after a request-rate ban when the ban page does not state an expiry.",
  minutes: "Background auto-refresh interval in minutes when Auto refresh is enabled.",
  networkProxy: "Optional HTTP, HTTPS, socks5, or socks5h proxy used for ExHentai and model downloads.",
  languageFilter: "Comma-separated languages allowed in recommendations, for example japanese,chinese.",
  modelMode: "Hybrid uses tags, title, marks, and visual signals. Visual only ranks by image embeddings.",
  reviewRequireBootstrapMatch: "When enabled, Review only shows galleries that match at least one bootstrap tag or keyword.",
  visualEncoder: "Simple is lightweight. DINOv2 is stronger visually but needs PyTorch and much more compute.",
  dinov2Device: "Device for DINOv2 visual embedding. Use auto, cpu, cuda, cuda:0, rocm, or hip. ROCm/HIP map to PyTorch's cuda device API.",
  downloadDinov2Btn: "Download the DINOv2 model files into the local cache using the configured proxy.",
  autoRefresh: "Periodically fetch new galleries in the background using the saved cookie and fetch plan.",
  saveBtn: "Save all settings in this panel.",
  exportBtn: "Download your preferences, feedback, bootstrap tags, marks, and model data as JSON.",
  importBtn: "Import a JSON backup created by Export.",
  replaceImport: "When importing, replace existing preference data instead of merging into it.",
  resetBtn: "Delete fetched galleries, feedback, learned model, marks, visual embeddings, and fetch history. Cookie and bootstrap tags remain.",
  reviewTab: "Show unrated recommendations to review and train the model.",
  shortRepeatsTab: "Show short new galleries that resemble older rated source-prefix galleries, with old reactions shown for reference.",
  historyTab: "Show galleries you already rated, skipped, or voted on.",
  favoriteTab: "Show favorite bookmarked galleries, which act as strong positive signals.",
  banTab: "Show banned bookmarked galleries, which act as strong negative signals.",
  previewTab: "Show the full model ranking, including already rated galleries, with stronger freshness weighting.",
  query: "Optional one-off ExHentai search query. It is used alone when you click Fetch Query.",
  searchFetchBtn: "Fetch galleries for only the one-off query in the search box.",
  localFilter: "Filter already stored local galleries by title, tag, category, or uploader.",
  backfillReviewParentsBtn: "Fetch gdata metadata for stored Review galleries matching the current filter and missing Parent links or alternate titles.",
  historySearchBtn: "Search only your reaction history using the local filter text.",
  backfillHistoryParentsBtn: "Fetch gdata metadata for stored galleries matching the current filter and missing Parent links or alternate titles.",
  recalcShortRepeatsBtn: "Recompute the Short Repeats queue using the current strict title and artist matching rules.",
  loadMoreBtn: "Load the next page of local results for the current view.",
};

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function applyStaticTooltips() {
  for (const [id, tooltip] of Object.entries(staticTooltips)) {
    const element = document.getElementById(id);
    if (!element) {
      continue;
    }
    element.title = tooltip;
    if (element.tagName === "INPUT" || element.tagName === "TEXTAREA" || element.tagName === "SELECT") {
      element.setAttribute("aria-label", tooltip);
      const label = element.closest("label");
      if (label) {
        label.title = tooltip;
      }
    }
  }
  const closeButton = modelDialog.querySelector('button[value="close"]');
  if (closeButton) {
    closeButton.title = "Close the model dialog.";
  }
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
  staleFetchExtraPagesEl.value = settings.stale_fetch_extra_pages;
  detailLimitEl.value = settings.detail_fetch_limit;
  learnedLimitEl.value = settings.learned_query_limit;
  candidateLimitEl.value = settings.recommend_candidate_limit;
  previewFreshnessWeightEl.value = settings.preview_freshness_weight ?? 8;
  previewPostedAfterEl.value = settings.preview_posted_after || "";
  sampleExtraPagesEl.value = settings.sample_extra_pages;
  requestIntervalEl.value = settings.request_interval_seconds;
  banPauseEl.value = settings.temporary_ban_pause_seconds;
  minutesEl.value = settings.refresh_interval_minutes;
  networkProxyEl.value = settings.network_proxy || "";
  languageFilterEl.value = settings.recommend_language_filter || "chinese,japanese";
  modelModeEl.value = settings.recommend_model_mode || "hybrid";
  reviewRequireBootstrapMatchEl.checked = settings.review_require_bootstrap_match !== false;
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
    stale_fetch_extra_pages: Number(staleFetchExtraPagesEl.value),
    detail_fetch_limit: Number(detailLimitEl.value),
    learned_query_limit: Number(learnedLimitEl.value),
    recommend_candidate_limit: Number(candidateLimitEl.value),
    preview_freshness_weight: Number(previewFreshnessWeightEl.value),
    preview_posted_after: previewPostedAfterEl.value,
    sample_extra_pages: Number(sampleExtraPagesEl.value),
    request_interval_seconds: Number(requestIntervalEl.value),
    temporary_ban_pause_seconds: Number(banPauseEl.value),
    refresh_interval_minutes: Number(minutesEl.value),
    network_proxy: networkProxyEl.value.trim(),
    recommend_language_filter: languageFilterEl.value.trim(),
    recommend_model_mode: modelModeEl.value,
    review_require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
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
  if (view === "short-repeats") {
    return {
      title: "Short Repeat Queue",
      subtitle: "Short unrated galleries related to older reactions, separated from the main review queue.",
      empty: "No short repeat galleries need review.",
      loaded: "short repeat galleries loaded",
    };
  }
  if (view === "history") {
    return {
      title: "Reaction History",
      subtitle: "Galleries you already reacted to, newest reaction first.",
      empty: "No reaction history yet.",
      loaded: "history items loaded",
    };
  }
  if (view === "favorite") {
    return {
      title: "Favorite Galleries",
      subtitle: "Bookmarked galleries that train the model as strong positive signals.",
      empty: "No favorite galleries yet.",
      loaded: "favorite galleries loaded",
    };
  }
  if (view === "ban") {
    return {
      title: "Banned Galleries",
      subtitle: "Bookmarked rejects that train the model as strong negative signals.",
      empty: "No banned galleries yet.",
      loaded: "banned galleries loaded",
    };
  }
  if (view === "preview") {
    return {
      title: "Model Preview",
      subtitle: "Read-only full model ranking with stronger freshness, including galleries with reactions.",
      empty: "No model recommendations yet.",
      loaded: "preview recommendations loaded",
    };
  }
  return {
    title: "Review Queue",
    subtitle: "Unrated galleries with a few random bootstrap-seed picks mixed in.",
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
  const historyView = view === "history";
  const reviewView = view === "review";
  backfillReviewParentsBtn.classList.toggle("hidden", !reviewView);
  historySearchBtn.classList.toggle("hidden", !historyView);
  backfillHistoryParentsBtn.classList.toggle("hidden", !historyView);
  recalcShortRepeatsBtn.classList.toggle("hidden", view !== "short-repeats");
  localFilterEl.placeholder = historyView ? "Search voted history by title, alt title, tag, category, uploader" : defaultLocalFilterPlaceholder;
}

async function loadCurrentPage(offset = 0, append = false) {
  if (currentView === "short-repeats") {
    return loadShortRepeats(offset, append);
  }
  if (currentView === "history") {
    return loadReactionHistory(offset, append);
  }
  if (currentView === "favorite" || currentView === "ban") {
    return loadMarkedGalleries(currentView, offset, append);
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

async function loadShortRepeats(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/short-repeats?limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

async function recalculateShortRepeats() {
  setStatus("Recalculating short repeat groups");
  const payload = await api("/api/short-repeats/recalculate", {
    method: "POST",
    body: JSON.stringify({ filter_text: localFilterEl.value.trim() }),
  });
  applyGalleryPage(payload);
  setStatus(`Recalculated ${payload.total} short repeat galleries`);
}

async function searchReactionHistory() {
  if (currentView !== "history") {
    setActiveView("history");
  }
  await loadReactionHistory();
  const query = localFilterEl.value.trim();
  setStatus(query ? `History search loaded for "${query}"` : "Reaction history loaded");
}

async function backfillParentsForCurrentFilter({ reloadView = currentView } = {}) {
  const payload = await api("/api/reactions/backfill-parents", {
    method: "POST",
    body: JSON.stringify({
      scope: "all",
      limit: 100,
      filter_text: localFilterEl.value.trim(),
    }),
  });
  if (reloadView === "history") {
    applyGalleryPage(payload);
  } else {
    await loadCurrentPage();
  }
  if (payload.errors.length) {
    setStatus(`Updated ${payload.updated} metadata rows; errors: ${payload.errors.join(" | ")}`, true);
  } else {
    setStatus(
      `Updated ${payload.updated} metadata rows (${payload.parent_updated} parents, ${payload.title_jpn_updated} alternate titles)`
    );
  }
}

async function backfillReviewParents() {
  setStatus("Updating parent metadata for Review galleries");
  await backfillParentsForCurrentFilter({ reloadView: "review" });
}

async function backfillHistoryParents() {
  setStatus("Updating parent metadata for stored galleries");
  await backfillParentsForCurrentFilter({ reloadView: "history" });
}

async function loadMarkedGalleries(kind, offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/marks?kind=${encodeURIComponent(kind)}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

async function loadRecommendations(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const includeRated = currentView === "preview" ? "1" : "0";
  const freshnessWeight = currentView === "preview" ? previewFreshnessWeightEl.value || "8" : "1";
  const postedAfter = currentView === "preview" ? previewPostedAfterEl.value || "" : "";
  const bootstrapExploreCount = currentView === "review" ? "6" : "0";
  const requireBootstrapMatch = currentView === "review" && reviewRequireBootstrapMatchEl.checked ? "1" : "0";
  const languageFilter = languageFilterEl.value.trim();
  const modelMode = modelModeEl.value || "hybrid";
  if (currentView === "review" && !append && offset === 0) {
    reviewExploreSeed = `${Date.now()}-${Math.random()}`;
  }
  const payload = await api(
    `/api/recommendations?include_rated=${includeRated}&freshness_weight=${encodeURIComponent(freshnessWeight)}&posted_after=${encodeURIComponent(postedAfter)}&bootstrap_explore_count=${bootstrapExploreCount}&require_bootstrap_match=${requireBootstrapMatch}&explore_seed=${encodeURIComponent(reviewExploreSeed)}&language_filter=${encodeURIComponent(languageFilter)}&model_mode=${encodeURIComponent(modelMode)}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
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
        view: currentView,
        include_rated: false,
        enrich_feedback: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
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
        view: currentView,
        include_rated: false,
        enrich_feedback: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
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
        view: currentView,
        include_rated: false,
        enrich_feedback: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(feedbackStatusMessage("Gallery skipped", payload));
  });
}

async function markGallery(galleryUrl, kind) {
  await withPendingFeedback(galleryUrl, async () => {
    const label = kind === "ban" ? "Banning gallery" : "Adding favorite";
    setStatus(label);
    const payload = await api("/api/mark", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        kind,
        view: currentView,
        include_rated: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(markStatusMessage(kind === "ban" ? "Gallery banned" : "Favorite saved", payload));
  });
}

async function clearMark(galleryUrl) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus("Clearing bookmark");
    const payload = await api("/api/mark/clear", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        view: currentView,
        include_rated: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload);
    setStatus(payload.removed ? "Bookmark cleared" : "No bookmark to clear");
  });
}

async function clearRating(galleryUrl) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus("Clearing rating");
    const payload = await api("/api/feedback/clear", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        view: currentView,
        include_rated: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
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

function scoreTooltip(value) {
  const labels = {
    1: "Record a strong negative score. Use this when you strongly dislike this gallery.",
    2: "Record a weak negative score.",
    3: "Record a neutral skip. It leaves the review queue without pushing the model positive or negative.",
    4: "Record a weak positive score.",
    5: "Record a strong positive score. Use this when you strongly like this gallery.",
  };
  return labels[value] || "Record a numeric preference score for this gallery.";
}

function relatedFeedbackSummary(entry) {
  const parts = [];
  if (entry.user_score) {
    parts.push(`score ${entry.user_score}`);
  } else if (entry.feedback_id) {
    parts.push(`signal ${entry.user_vote || 0}`);
  }
  if (entry.user_mark_kind === "favorite") {
    parts.push("favorite");
  } else if (entry.user_mark_kind === "ban") {
    parts.push("ban");
  }
  if (entry.page_count) {
    parts.push(`${entry.page_count} pages`);
  }
  if (entry.feedback_created_at) {
    parts.push(entry.feedback_created_at);
  } else if (entry.mark_updated_at) {
    parts.push(entry.mark_updated_at);
  }
  return parts.join(" · ") || "old reaction";
}

function renderRelatedFeedback(item) {
  const entries = item.related_feedback || [];
  if (!entries.length) {
    return "";
  }
  return `<div class="related-feedback">
    <div class="related-heading">Old reactions</div>
    ${entries
      .map((entry) => {
        const title = entry.title || entry.url || "Related gallery";
        const summary = relatedFeedbackSummary(entry);
        const href = entry.url
          ? `<a href="${escapeAttr(entry.url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
          : `<span>${escapeHtml(title)}</span>`;
        return `<div class="related-item">${href}<span>${escapeHtml(summary)}</span></div>`;
      })
      .join("")}
  </div>`;
}

function renderParentChain(item) {
  const entries = item.parent_chain || [];
  if (!entries.length) {
    return "";
  }
  const links = entries
    .map((entry) => {
      const title = entry.title || entry.url || "Parent gallery";
      const label = entry.known ? title : `Unknown parent ${entry.url}`;
      return entry.url
        ? `<a href="${escapeAttr(entry.url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`
        : `<span>${escapeHtml(label)}</span>`;
    })
    .join('<span class="chain-separator">&rarr;</span>');
  return `<div class="parent-chain"><span>Parent chain</span>${links}</div>`;
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
    const emptyText = mode === "history" && localFilterEl.value.trim()
      ? "No matching reaction history found."
      : viewCopy(mode).empty;
    recommendationsEl.innerHTML = `<div class="hint">${escapeHtml(emptyText)}</div>`;
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
    const relatedFeedback = renderRelatedFeedback(item);
    const parentChain = renderParentChain(item);
    const hasFeedback = Boolean(item.feedback_id);
    const userFeedback = hasFeedback
      ? item.user_score
        ? `Your score ${item.user_score}`
        : `Your signal ${item.user_vote || 0}`
      : "No reaction";
    const markStatus = item.user_mark_kind === "favorite"
      ? "Bookmarked favorite"
      : item.user_mark_kind === "ban"
        ? "Bookmarked ban"
        : "";
    const detailStatus = item.detail_fetched_at ? "Full metadata" : "List metadata";
    const uploader = item.uploader ? `Uploader ${item.uploader}` : "Uploader unknown";
    const postedAt = item.posted_at ? `Posted ${item.posted_at}` : "";
    const reactionAt = item.feedback_created_at ? `Reacted ${item.feedback_created_at}` : "";
    const markAt = item.mark_updated_at ? `Bookmarked ${item.mark_updated_at}` : "";
    const clearButton = hasFeedback && mode !== "preview"
      ? `<button class="clear" type="button" data-clear="1" data-url="${escapeAttr(item.url)}" title="Remove your rating, vote, or skip for this gallery.">Clear</button>`
      : "";
    const historyButton = hasFeedback && mode !== "preview"
      ? `<button class="clear" type="button" data-history="1" data-url="${escapeAttr(item.url)}" title="Show your feedback history for this gallery.">History</button>`
      : "";
    const feedbackActions = historyButton || clearButton ? `<div class="card-actions">${historyButton}${clearButton}</div>` : "";
    const favoriteButton = item.user_mark_kind === "favorite"
      ? ""
      : `<button class="up" type="button" data-mark="favorite" data-url="${escapeAttr(item.url)}" title="Bookmark this gallery as a favorite and train it as a strong positive signal.">Favorite</button>`;
    const banButton = item.user_mark_kind === "ban"
      ? ""
      : `<button class="down" type="button" data-mark="ban" data-url="${escapeAttr(item.url)}" title="Bookmark this gallery as banned and train it as a strong negative signal.">Ban</button>`;
    const clearMarkButton = item.marked
      ? `<button class="clear" type="button" data-clear-mark="1" data-url="${escapeAttr(item.url)}" title="Remove the favorite or ban bookmark from this gallery.">Clear bookmark</button>`
      : "";
    const markActions = mode === "preview"
      ? ""
      : `<div class="card-actions">${banButton}${favoriteButton}${clearMarkButton}</div>`;
    const feedbackControls = mode === "preview"
      ? ""
      : `<div class="votes">
          <button class="down" type="button" data-vote="-1" data-url="${escapeAttr(item.url)}" title="Record a mild negative signal for this gallery.">Thumb down</button>
          <button class="skip" type="button" data-skip="1" data-url="${escapeAttr(item.url)}" title="Mark this gallery as reviewed with a neutral score so it leaves the review queue.">Skip</button>
          <button class="up" type="button" data-vote="1" data-url="${escapeAttr(item.url)}" title="Record a mild positive signal for this gallery.">Thumb up</button>
        </div>
        <div class="scorebar" aria-label="Score">
          ${[1, 2, 3, 4, 5]
            .map((value) => `<button type="button" data-score="${value}" data-url="${escapeAttr(item.url)}" title="${escapeAttr(scoreTooltip(value))}">${value}</button>`)
            .join("")}
        </div>
        ${markActions}
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
        ${markStatus ? `<div class="meta">${escapeHtml(markStatus)}</div>` : ""}
        ${reactionAt ? `<div class="meta">${escapeHtml(reactionAt)}</div>` : ""}
        ${markAt ? `<div class="meta">${escapeHtml(markAt)}</div>` : ""}
        ${parentChain}
        <div class="pillrow">${tags}</div>
        <div class="reason">${reasons}</div>
        ${relatedFeedback}
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
  const update = payload.feedback_update || {};
  const details = [];
  if (update.latest_feedback_id) {
    details.push(`feedback #${update.latest_feedback_id}`);
  }
  if (update.retrained) {
    details.push(update.model_changed ? "model changed" : "model retrained no weight change");
  }
  if (Number.isFinite(update.elapsed_ms)) {
    details.push(`update ${update.elapsed_ms}ms`);
  }
  if (Number.isFinite(update.feedback_events_after)) {
    details.push(`events ${update.feedback_events_before || 0}->${update.feedback_events_after}`);
  }
  if (Number.isFinite(update.model_features_after)) {
    details.push(`features ${update.model_features_before || 0}->${update.model_features_after}`);
  }
  if (Number.isFinite(update.visual_rated_after)) {
    details.push(`visual rated ${update.visual_rated_before || 0}->${update.visual_rated_after}`);
  }
  const enrichment = payload.feedback_enrichment || {};
  if (enrichment.status === "success") {
    details.push("full metadata learned");
  } else if (enrichment.status === "failed") {
    details.push("detail fetch failed");
  }
  return details.length ? `${base}; ${details.join("; ")}` : base;
}

function markStatusMessage(base, payload) {
  const update = payload.mark_update || {};
  const details = [];
  if (update.current_kind) {
    details.push(update.current_kind);
  }
  if (update.model_changed) {
    details.push("model changed");
  }
  if (Number.isFinite(update.elapsed_ms)) {
    details.push(`update ${update.elapsed_ms}ms`);
  }
  if (Number.isFinite(update.favorite_galleries_after)) {
    details.push(`favorites ${update.favorite_galleries_after}`);
  }
  if (Number.isFinite(update.banned_galleries_after)) {
    details.push(`bans ${update.banned_galleries_after}`);
  }
  return details.length ? `${base}; ${details.join("; ")}` : base;
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
    if (button.dataset.vote || button.dataset.score || button.dataset.skip || button.dataset.clear || button.dataset.mark || button.dataset.clearMark) {
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
    ["State", fetchState.running ? fetchState.message || fetchState.stage || "Running" : "Idle"],
  ];
  if (fetchState.running) {
    rows.push(["Counts", `${fetchState.fetched || 0} fetched, ${fetchState.stored || 0} stored, ${fetchState.enriched || 0} enriched`]);
    if (fetchState.run_id) {
      rows.push(["Run", `#${fetchState.run_id} ${fetchState.trigger || "manual"}`]);
    } else if (fetchState.trigger) {
      rows.push(["Run", fetchState.trigger]);
    }
    if (fetchState.query_total) {
      rows.push([
        "Query",
        `${fetchState.query_index || 0}/${fetchState.query_total}: ${fetchState.current_query || "recent"}`,
      ]);
    }
    if (Number.isFinite(fetchState.page_start) || Number.isFinite(fetchState.next_page_start)) {
      const start = Number.isFinite(fetchState.page_start) ? fetchState.page_start : fetchState.next_page_start;
      const count = Number.isFinite(fetchState.page_count) ? fetchState.page_count : fetchState.next_page_count;
      rows.push(["Pages", `start ${start || 0}, count ${count || 0}, +${fetchState.remaining_extra_pages || 0} fallback left`]);
    }
    if (Number.isFinite(fetchState.fetched_batch) || Number.isFinite(fetchState.stored_batch)) {
      rows.push(["Batch", `${fetchState.fetched_batch || 0} fetched, ${fetchState.stored_batch || 0} new`]);
    }
    if (Number.isFinite(fetchState.detail_total)) {
      rows.push(["Details", `${fetchState.detail_done || 0}/${fetchState.detail_total}`]);
    }
    if (fetchState.current_gallery_title || fetchState.current_gallery_url) {
      rows.push(["Current", fetchState.current_gallery_title || fetchState.current_gallery_url]);
    }
    if (fetchState.updated_at) {
      rows.push(["Progress At", fetchState.updated_at]);
    }
    if (fetchState.errors && fetchState.errors.length) {
      rows.push(["Current Errors", fetchState.errors.join(" | ")]);
    }
  }
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
    rows.push(["Scope", `${plan.pages} page(s), +${plan.stale_fetch_extra_pages || 0} stale fallback, ${plan.detail_fetch_limit} details`]);
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
  if (payload.settings && payload.settings.recommend_language_filter) {
    rows.push(["Languages", payload.settings.recommend_language_filter]);
  }
  if (payload.settings && payload.settings.recommend_model_mode) {
    rows.push(["Model", payload.settings.recommend_model_mode]);
  }
  if (payload.settings && Number.isFinite(payload.settings.preview_freshness_weight)) {
    const cutoff = payload.settings.preview_posted_after || "none";
    rows.push(["Preview", `freshness ${payload.settings.preview_freshness_weight}, after ${cutoff}`]);
  }
  if (payload.settings && typeof payload.settings.review_require_bootstrap_match === "boolean") {
    rows.push(["Review", payload.settings.review_require_bootstrap_match ? "Bootstrap match required" : "Any model match"]);
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
backfillReviewParentsBtn.addEventListener("click", () => backfillReviewParents().catch((error) => setStatus(error.message, true)));
historySearchBtn.addEventListener("click", () => searchReactionHistory().catch((error) => setStatus(error.message, true)));
backfillHistoryParentsBtn.addEventListener("click", () => backfillHistoryParents().catch((error) => setStatus(error.message, true)));
recalcShortRepeatsBtn.addEventListener("click", () => recalculateShortRepeats().catch((error) => setStatus(error.message, true)));
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
localFilterEl.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  loadCurrentPage().catch((error) => setStatus(error.message, true));
});
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
  const markButton = event.target.closest("button[data-mark]");
  if (markButton) {
    markGallery(markButton.dataset.url, markButton.dataset.mark).catch((error) => setStatus(error.message, true));
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
  const clearMarkButton = event.target.closest("button[data-clear-mark]");
  if (clearMarkButton) {
    clearMark(clearMarkButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
  const historyButton = event.target.closest("button[data-history]");
  if (historyButton) {
    showFeedbackHistory(historyButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
});

applyStaticTooltips();

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
