const statusEl = document.querySelector("#status");
const cookieEl = document.querySelector("#cookie");
const cookiePreviewEl = document.querySelector("#cookiePreview");
const tagsEl = document.querySelector("#tags");
const pagesEl = document.querySelector("#pages");
const staleFetchExtraPagesEl = document.querySelector("#staleFetchExtraPages");
const detailLimitEl = document.querySelector("#detailLimit");
const learnedLimitEl = document.querySelector("#learnedLimit");
const candidateLimitEl = document.querySelector("#candidateLimit");
const reviewLowInterestPercentEl = document.querySelector("#reviewLowInterestPercent");
const reviewLowInterestMaxPercentEl = document.querySelector("#reviewLowInterestMaxPercent");
const reviewLowInterestAutoThresholdEl = document.querySelector("#reviewLowInterestAutoThreshold");
const updatesShortlistLimitEl = document.querySelector("#updatesShortlistLimit");
const updatesMinNewPagesEl = document.querySelector("#updatesMinNewPages");
const previewFreshnessWeightEl = document.querySelector("#previewFreshnessWeight");
const previewPostedAfterEl = document.querySelector("#previewPostedAfter");
const sampleExtraPagesEl = document.querySelector("#sampleExtraPages");
const requestIntervalEl = document.querySelector("#requestInterval");
const banPauseEl = document.querySelector("#banPause");
const hathDownloadSignalWeightEl = document.querySelector("#hathDownloadSignalWeight");
const minutesEl = document.querySelector("#minutes");
const modelRetrainModeEl = document.querySelector("#modelRetrainMode");
const modelRetrainThresholdEl = document.querySelector("#modelRetrainThreshold");
const modelRetrainIntervalEl = document.querySelector("#modelRetrainInterval");
const networkProxyEl = document.querySelector("#networkProxy");
const languageFilterEl = document.querySelector("#languageFilter");
const modelModeEl = document.querySelector("#modelMode");
const reviewRequireBootstrapMatchEl = document.querySelector("#reviewRequireBootstrapMatch");
const visualEncoderEl = document.querySelector("#visualEncoder");
const dinov2DeviceEl = document.querySelector("#dinov2Device");
const autoRefreshEl = document.querySelector("#autoRefresh");
const recommendationsEl = document.querySelector("#recommendations");
const queryEl = document.querySelector("#query");
const searchFetchBtn = document.querySelector("#searchFetchBtn");
const localFilterEl = document.querySelector("#localFilter");
const defaultLocalFilterPlaceholder = localFilterEl.placeholder;
const backfillReviewParentsBtn = document.querySelector("#backfillReviewParentsBtn");
const historySearchBtn = document.querySelector("#historySearchBtn");
const backfillHistoryParentsBtn = document.querySelector("#backfillHistoryParentsBtn");
const recalcShortRepeatsBtn = document.querySelector("#recalcShortRepeatsBtn");
const parentProgressEl = document.querySelector("#parentProgress");
const parentProgressTitleEl = document.querySelector("#parentProgressTitle");
const parentProgressSummaryEl = document.querySelector("#parentProgressSummary");
const parentProgressCountsEl = document.querySelector("#parentProgressCounts");
const parentProgressBarEl = document.querySelector("#parentProgressBar");
const parentProgressLogEl = document.querySelector("#parentProgressLog");
const classifierSummaryEl = document.querySelector("#classifierSummary");
const classifierSummaryStatusEl = document.querySelector("#classifierSummaryStatus");
const classifierSummaryCountsEl = document.querySelector("#classifierSummaryCounts");
const importFileEl = document.querySelector("#importFile");
const replaceImportEl = document.querySelector("#replaceImport");
const loadMoreBtn = document.querySelector("#loadMoreBtn");
const modelDialog = document.querySelector("#modelDialog");
const dialogTitle = document.querySelector("#dialogTitle");
const modelBody = document.querySelector("#modelBody");
const refreshStatusEl = document.querySelector("#refreshStatus");
const galleryViewEl = document.querySelector("#galleryView");
const hathViewEl = document.querySelector("#hathView");
const hathClientCountEl = document.querySelector("#hathClientCount");
const hathConnectionSummaryEl = document.querySelector("#hathConnectionSummary");
const hathUpdatedAtEl = document.querySelector("#hathUpdatedAt");
const hathSummaryEl = document.querySelector("#hathSummary");
const hathP2pSummaryEl = document.querySelector("#hathP2pSummary");
const hathP2pOverviewEl = document.querySelector("#hathP2pOverview");
const hathP2pClientsEl = document.querySelector("#hathP2pClients");
const hathClientSummaryEl = document.querySelector("#hathClientSummary");
const hathClientsEl = document.querySelector("#hathClients");
const hathDownloadSummaryEl = document.querySelector("#hathDownloadSummary");
const hathDownloadSearchEl = document.querySelector("#hathDownloadSearch");
const hathDownloadFilterEl = document.querySelector("#hathDownloadFilter");
const hathDownloadsEl = document.querySelector("#hathDownloads");
const hathRefreshBtn = document.querySelector("#hathRefreshBtn");
const hathPackBtn = document.querySelector("#hathPackBtn");
const hathPackRefreshBtn = document.querySelector("#hathPackRefreshBtn");
const hathPackPreviewBtn = document.querySelector("#hathPackPreviewBtn");
const hathPackSelectAllBtn = document.querySelector("#hathPackSelectAllBtn");
const hathPackSelectNoneBtn = document.querySelector("#hathPackSelectNoneBtn");
const hathPackArchiveNameEl = document.querySelector("#hathPackArchiveName");
const hathPackMegaAccountEl = document.querySelector("#hathPackMegaAccount");
const hathPackMegaDestinationEl = document.querySelector("#hathPackMegaDestination");
const hathPackUploadEl = document.querySelector("#hathPackUpload");
const hathPackCleanupModeEl = document.querySelector("#hathPackCleanupMode");
const hathPackTrashSourcesEl = document.querySelector("#hathPackTrashSources");
const hathPackTrashArchiveEl = document.querySelector("#hathPackTrashArchive");
const hathPackSelectionSummaryEl = document.querySelector("#hathPackSelectionSummary");
const hathPackCandidatesEl = document.querySelector("#hathPackCandidates");
const hathPackPlanEl = document.querySelector("#hathPackPlan");
const hathPackSummaryEl = document.querySelector("#hathPackSummary");
const hathPackBadgeEl = document.querySelector("#hathPackBadge");
const hathPackTimingEl = document.querySelector("#hathPackTiming");
const hathPackLogEl = document.querySelector("#hathPackLog");
const hathTrashSummaryEl = document.querySelector("#hathTrashSummary");
const hathTrashCandidatesEl = document.querySelector("#hathTrashCandidates");
const hathTrashSelectAllBtn = document.querySelector("#hathTrashSelectAllBtn");
const hathTrashSelectNoneBtn = document.querySelector("#hathTrashSelectNoneBtn");
const hathTrashPreviewBtn = document.querySelector("#hathTrashPreviewBtn");
const hathTrashDeleteBtn = document.querySelector("#hathTrashDeleteBtn");
const hathTrashPlanEl = document.querySelector("#hathTrashPlan");
const viewTitleEl = document.querySelector("#viewTitle");
const viewSubtitleEl = document.querySelector("#viewSubtitle");
const reviewQueueCountEl = document.querySelector("#reviewQueueCount");
const lowInterestQueueCountEl = document.querySelector("#lowInterestQueueCount");
const lowInterestTab = document.querySelector("#lowInterestTab");
const lowInterestPolicyStatusEl = document.querySelector("#lowInterestPolicyStatus");
const lowInterestPolicyTitleEl = document.querySelector("#lowInterestPolicyTitle");
const lowInterestPolicyDetailEl = document.querySelector("#lowInterestPolicyDetail");
const continuingUpdatesQueueCountEl = document.querySelector("#continuingUpdatesQueueCount");
const classifierQueueCountEl = document.querySelector("#classifierQueueCount");
const shortRepeatsQueueCountEl = document.querySelector("#shortRepeatsQueueCount");
const viewTabs = [...document.querySelectorAll("[data-view]")];
let nextRecommendationOffset = 0;
let hasMoreRecommendations = false;
let queueCountsPromise = null;
let queueCountsRefreshQueued = false;
let hathStatusPromise = null;
let hathStatusPayload = null;
let hathPackStatusPromise = null;
let hathPackInventoryPromise = null;
let hathPackPollTimer = null;
let hathPackInventory = null;
let hathPackPreview = null;
let hathTrashPreview = null;
let hathPackStatusPayload = null;
let lastRenderedFetchId = null;
let currentView = "review";
let reviewExploreSeed = "";
let recommendationRequestId = 0;
const recommendationLimit = 40;
const pendingFeedbackUrls = new Set();
const pendingClassificationSampleIds = new Set();
let renderedGalleryUrls = [];
let renderedGalleryItems = [];
let parentProgressVisible = false;
let parentProgressPollTimer = null;
const pendingGalleryRefreshUrls = new Set();
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
  retrainBtn: "Immediately rebuild the recommendation model and clear all pending review feedback.",
  modelBtn: "Open the current learned model weights, counts, and visual model summary.",
  cookie: "Your ExHentai Cookie header. Leave blank when saving to keep the currently stored cookie.",
  checkBtn: "Test whether the stored cookie can access ExHentai gallery listings.",
  clearCookieBtn: "Remove the stored cookie and saved access-check result.",
  tags: "Seed tags for initial fetching and scoring. Use negative lines for dislikes and :weight for stronger signals.",
  pages: "Result pages fetched when an automatic query first establishes its cursor, from 1 to 5.",
  staleFetchExtraPages: "Additional pages allowed while an automatic query catches up to its previous cursor.",
  detailLimit: "Maximum galleries per fetch to enrich with full detail metadata and sample thumbnails.",
  learnedLimit: "Maximum learned positive tags to add as extra remote fetch queries.",
  candidateLimit: "Number of local candidate galleries considered when ranking recommendations.",
  reviewLowInterestPercent: "Move this bottom percentage of the current Review ranking into Low Interest. Set to 0 to disable the percentile split.",
  reviewLowInterestMaxPercent: "Maximum combined Low Interest share after the learned threshold adds galleries beyond the bottom percentage.",
  reviewLowInterestAutoThreshold: "Use a validated probability threshold learned during periodic model training. It falls back to the bottom percentage when validation is unavailable.",
  previewFreshnessWeight: "Freshness boost used only in Preview. Higher values push newer galleries above older strong matches.",
  previewPostedAfter: "Optional Preview cutoff. When set, Preview only shows galleries posted on or after this date.",
  sampleExtraPages: "Additional gallery sample pages to inspect for preview images on large galleries.",
  requestInterval: "Minimum delay in seconds between ExHentai-related network requests.",
  banPause: "Fallback pause in seconds after a request-rate ban when the ban page does not state an expiry.",
  hathDownloadSignalWeight: "Learning weight for completed H@H downloads. Set to 0 to disable download-based positive signals.",
  minutes: "Background auto-refresh interval in minutes when Auto refresh is enabled.",
  modelRetrainMode: "Batch trains after enough reviews or a time limit. Each review trains in the background. Manual waits for Retrain.",
  modelRetrainThreshold: "In Batch mode, start background training after this many model-changing reviews.",
  modelRetrainInterval: "In Batch mode, train pending review feedback after this many minutes even below the threshold.",
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
  lowInterestTab: "Cover-first triage for the bottom part of the current model ranking. Items move automatically when the model changes.",
  discoveryTab: "Explore uncertain, under-estimated, and less-covered interests without diluting Review.",
  continuingUpdatesTab: "Show the latest version of galleries detected as cumulative Pixiv, Fanbox, Patreon, archive, or ongoing series.",
  classifierTab: "Label sampled galleries as normal Review items or continuing Updates.",
  shortRepeatsTab: "Show short new galleries that resemble older rated source-prefix galleries, with old reactions shown for reference.",
  historyTab: "Show galleries you already rated, skipped, or voted on.",
  favoriteTab: "Show favorite bookmarked galleries, which act as strong positive signals.",
  banTab: "Show banned bookmarked galleries, which act as strong negative signals.",
  previewTab: "Show the full model ranking, including already rated galleries, with stronger freshness weighting.",
  hathTab: "Show H@H observer connectivity, client health, and recent download activity.",
  hathRefreshBtn: "Refresh H@H client and download status now.",
  hathPackBtn: "Archive completed download directories on the H@H host and upload the daily archive to MEGA.",
  hathPackPreviewBtn: "Validate the selected sources and show the exact archive, upload, and cleanup plan without changing files.",
  hathPackRefreshBtn: "Rescan remote gallery directories, ZIP files, and the current MEGA account.",
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
  const imageEmbeddings = [];
  for (const source of task.sources) {
    try {
      const embedding = await imageEmbedding(source);
      vectors.push(embedding);
      imageEmbeddings.push({ image_url: source, embedding });
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
      image_embeddings: imageEmbeddings,
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
  const supportsLowInterest = Object.prototype.hasOwnProperty.call(settings, "review_low_interest_percent");
  const supportsLowInterestThreshold = Object.prototype.hasOwnProperty.call(settings, "review_low_interest_auto_threshold");
  lowInterestTab.hidden = !supportsLowInterest;
  reviewLowInterestPercentEl.closest("label").hidden = !supportsLowInterest;
  reviewLowInterestMaxPercentEl.closest("label").hidden = !supportsLowInterestThreshold;
  reviewLowInterestAutoThresholdEl.closest("label").hidden = !supportsLowInterestThreshold;
  applyVisualSettings(settings.visual);
  cookiePreviewEl.textContent = cookiePreviewText(settings);
  tagsEl.value = bootstrapText(settings.bootstrap_tags);
  pagesEl.value = settings.fetch_pages;
  staleFetchExtraPagesEl.value = settings.stale_fetch_extra_pages;
  detailLimitEl.value = settings.detail_fetch_limit;
  learnedLimitEl.value = settings.learned_query_limit;
  candidateLimitEl.value = settings.recommend_candidate_limit;
  reviewLowInterestPercentEl.value = supportsLowInterest ? settings.review_low_interest_percent : 20;
  reviewLowInterestMaxPercentEl.value = supportsLowInterestThreshold ? settings.review_low_interest_max_percent : 35;
  reviewLowInterestAutoThresholdEl.checked = supportsLowInterestThreshold && settings.review_low_interest_auto_threshold !== false;
  updatesShortlistLimitEl.value = settings.updates_shortlist_limit ?? 40;
  updatesMinNewPagesEl.value = settings.updates_min_new_pages ?? 50;
  previewFreshnessWeightEl.value = settings.preview_freshness_weight ?? 8;
  previewPostedAfterEl.value = settings.preview_posted_after || "";
  sampleExtraPagesEl.value = settings.sample_extra_pages;
  requestIntervalEl.value = settings.request_interval_seconds;
  banPauseEl.value = settings.temporary_ban_pause_seconds;
  hathDownloadSignalWeightEl.value = settings.hath_download_signal_weight ?? 1.25;
  minutesEl.value = settings.refresh_interval_minutes;
  modelRetrainModeEl.value = settings.model_retrain_mode || "batched";
  modelRetrainThresholdEl.value = settings.model_retrain_feedback_threshold ?? 10;
  modelRetrainIntervalEl.value = settings.model_retrain_interval_minutes ?? 10;
  updateModelRetrainControls();
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
  renderParentUpdateProgress(payload.parent_update || {});
  await reloadRecommendationsAfterFetch(payload);
  return payload;
}

async function previewPlan() {
  const query = queryEl.value.trim();
  const payload = await api(`/api/plan${query ? `?query=${encodeURIComponent(query)}` : ""}`);
  renderStatus({ fetch: { running: false }, last_fetch: null, settings: {}, plan: payload });
  setStatus(`${payload.entries.length} planned fetch queries`);
}

function updateModelRetrainControls() {
  const batched = modelRetrainModeEl.value === "batched";
  modelRetrainThresholdEl.disabled = !batched;
  modelRetrainIntervalEl.disabled = !batched;
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
    review_low_interest_percent: Number(reviewLowInterestPercentEl.value),
    review_low_interest_max_percent: Number(reviewLowInterestMaxPercentEl.value),
    review_low_interest_auto_threshold: reviewLowInterestAutoThresholdEl.checked,
    updates_shortlist_limit: Number(updatesShortlistLimitEl.value),
    updates_min_new_pages: Number(updatesMinNewPagesEl.value),
    preview_freshness_weight: Number(previewFreshnessWeightEl.value),
    preview_posted_after: previewPostedAfterEl.value,
    sample_extra_pages: Number(sampleExtraPagesEl.value),
    request_interval_seconds: Number(requestIntervalEl.value),
    temporary_ban_pause_seconds: Number(banPauseEl.value),
    hath_download_signal_weight: Number(hathDownloadSignalWeightEl.value),
    refresh_interval_minutes: Number(minutesEl.value),
    model_retrain_mode: modelRetrainModeEl.value,
    model_retrain_feedback_threshold: Number(modelRetrainThresholdEl.value),
    model_retrain_interval_minutes: Number(modelRetrainIntervalEl.value),
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
  refreshQueueCounts();
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
  if (view === "hath") {
    return {
      title: "H@H Status",
      subtitle: "Observer connectivity, downloader health, and recent transfer activity.",
      empty: "No H@H observer has connected yet.",
      loaded: "H@H status loaded",
    };
  }
  if (view === "discovery") {
    return {
      title: "Discovery",
      subtitle: "Uncertain, model-disagreement, and less-covered candidates for finding missed interests.",
      empty: "No discovery candidates yet.",
      loaded: "discovery candidates loaded",
    };
  }
  if (view === "continuing-updates") {
    return {
      title: "Continuing Updates",
      subtitle: "A ranked shortlist of unseen continuing galleries and substantially expanded series.",
      empty: "No continuing updates currently pass the shortlist and growth filters.",
      loaded: "continuing gallery series loaded",
    };
  }
  if (view === "classifier") {
    return {
      title: "Classifier Labels",
      subtitle: "Decide whether each sampled gallery is a normal Review item or a cumulative continuing Update.",
      empty: "All current classifier samples are labeled.",
      loaded: "classifier samples loaded",
    };
  }
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
  if (view === "low-interest") {
    return {
      title: "Low Interest",
      subtitle: "Cover-first triage for the bottom part of the current model ranking.",
      empty: "No galleries are currently in the low-interest band.",
      loaded: "low-interest galleries loaded",
    };
  }
  return {
    title: "Review Queue",
    subtitle: "Unrated galleries with a few random bootstrap-seed picks mixed in.",
    empty: "No unrated galleries yet. Save cookies and bootstrap tags, then fetch.",
    loaded: "review recommendations loaded",
  };
}

function renderQueueCount(element, value, label) {
  if (!element || !Number.isFinite(Number(value))) {
    return;
  }
  const count = Math.max(0, Number(value));
  element.textContent = count.toLocaleString();
  element.title = `${count.toLocaleString()} ${label} need review`;
}

function renderQueueCounts(payload) {
  renderQueueCount(reviewQueueCountEl, payload.review, "galleries");
  renderQueueCount(lowInterestQueueCountEl, payload.low_interest, "low-interest galleries");
  renderQueueCount(continuingUpdatesQueueCountEl, payload.continuing_updates, "continuing series");
  renderQueueCount(classifierQueueCountEl, payload.classification_samples, "classifier samples");
  renderQueueCount(shortRepeatsQueueCountEl, payload.short_repeats, "short repeats");
}

function renderLowInterestPolicy(policy, view = currentView) {
  const visible = (view === "review" || view === "low-interest") && policy;
  lowInterestPolicyStatusEl.classList.toggle("hidden", !visible);
  if (!visible) {
    return;
  }
  const validation = policy.validation || {};
  const fallbackPercent = Number(reviewLowInterestPercentEl.value || 0);
  if (policy.active) {
    const thresholdPercent = Math.round(Number(policy.threshold || 0) * 100);
    const sampleCount = Number(validation.sample_count || 0);
    const positiveRate = Number(validation.positive_rate || 0) * 100;
    lowInterestPolicyTitleEl.textContent = `Auto threshold <= ${thresholdPercent}%`;
    lowInterestPolicyDetailEl.textContent = `${sampleCount} temporal validation samples · estimated positives ${positiveRate.toFixed(1)}% · combined cap ${Number(policy.max_percent || 0)}%`;
    return;
  }
  const reasonText = {
    disabled: "automatic threshold is off",
    "model-not-ready": "personalized model is not ready",
    "model-not-accepted": "personalized model has not passed the release gate",
    "model-calibration-not-ready": "probability calibration is not ready",
    "insufficient-oof-data": "not enough temporal validation samples",
    "oof-calibration-unavailable": "temporal folds are not fully calibrated",
    "safety-target-not-met": "no threshold currently meets the false-omission limits",
    "requires-hybrid-mode": "automatic threshold requires Hybrid mode",
  }[policy.status] || "validated threshold is unavailable";
  lowInterestPolicyTitleEl.textContent = `Bottom ${fallbackPercent}% only`;
  lowInterestPolicyDetailEl.textContent = reasonText;
}

function loadQueueCounts() {
  if (queueCountsPromise) {
    return queueCountsPromise;
  }
  queueCountsPromise = api("/api/queue-counts")
    .then((payload) => {
      renderQueueCounts(payload);
      return payload;
    })
    .catch(() => null)
    .finally(() => {
      queueCountsPromise = null;
      if (queueCountsRefreshQueued) {
        queueCountsRefreshQueued = false;
        loadQueueCounts();
      }
    });
  return queueCountsPromise;
}

function refreshQueueCounts() {
  if (queueCountsPromise) {
    queueCountsRefreshQueued = true;
    return queueCountsPromise;
  }
  return loadQueueCounts();
}

function updateCurrentQueueCount(payload) {
  if (localFilterEl.value.trim() || !Number.isFinite(Number(payload.total))) {
    return;
  }
  if (currentView === "review") {
    renderQueueCount(reviewQueueCountEl, payload.total, "galleries");
  } else if (currentView === "low-interest") {
    renderQueueCount(lowInterestQueueCountEl, payload.total, "low-interest galleries");
  } else if (currentView === "short-repeats") {
    renderQueueCount(shortRepeatsQueueCountEl, payload.total, "short repeats");
  } else if (currentView === "classifier") {
    renderQueueCount(classifierQueueCountEl, payload.counts?.pending, "classifier samples");
  }
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
  const hathView = view === "hath";
  galleryViewEl.classList.toggle("hidden", hathView);
  hathViewEl.classList.toggle("hidden", !hathView);
  const historyView = view === "history";
  const reviewView = view === "review" || view === "low-interest";
  const classifierView = view === "classifier";
  backfillReviewParentsBtn.classList.toggle("hidden", !reviewView);
  historySearchBtn.classList.toggle("hidden", !historyView);
  backfillHistoryParentsBtn.classList.toggle("hidden", !historyView);
  recalcShortRepeatsBtn.classList.toggle("hidden", view !== "short-repeats");
  queryEl.classList.toggle("hidden", classifierView);
  searchFetchBtn.classList.toggle("hidden", classifierView);
  classifierSummaryEl.classList.toggle("hidden", !classifierView);
  lowInterestPolicyStatusEl.classList.toggle("hidden", !reviewView);
  localFilterEl.placeholder = historyView
    ? "Search voted history by title, alt title, tag, category, uploader"
    : classifierView
      ? "Filter classifier samples by title, tag, category, uploader"
      : defaultLocalFilterPlaceholder;
}

async function loadCurrentPage(offset = 0, append = false) {
  if (currentView === "hath") {
    return loadHathStatus();
  }
  if (currentView === "discovery") {
    return loadDiscovery(offset, append);
  }
  if (currentView === "continuing-updates") {
    return loadContinuingUpdates(offset, append);
  }
  if (currentView === "classifier") {
    return loadClassificationSamples(offset, append);
  }
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

const hathStaleAfterMs = 150000;

async function loadHathStatus({ announce = true } = {}) {
  if (hathStatusPromise) {
    return hathStatusPromise;
  }
  hathRefreshBtn.disabled = true;
  hathStatusPromise = api("/api/integrations/hath/status")
    .then(async (payload) => {
      renderHathStatus(payload);
      await loadHathPackStatus();
      if (!hathPackInventory) {
        await loadHathPackInventory();
      }
      if (announce) {
        const clientCount = (payload.clients || []).length;
        const downloadCount = Object.values(payload.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
        setStatus(`H@H status loaded: ${clientCount} client${clientCount === 1 ? "" : "s"}, ${downloadCount} downloads`);
      }
      return payload;
    })
    .finally(() => {
      hathStatusPromise = null;
      hathRefreshBtn.disabled = false;
    });
  return hathStatusPromise;
}

async function loadHathPackStatus() {
  if (hathPackStatusPromise) return hathPackStatusPromise;
  hathPackStatusPromise = api("/api/integrations/hath/pack/status")
    .then((payload) => {
      renderHathPackStatus(payload);
      return payload;
    })
    .finally(() => {
      hathPackStatusPromise = null;
    });
  return hathPackStatusPromise;
}

async function loadHathPackInventory() {
  if (hathPackInventoryPromise) return hathPackInventoryPromise;
  hathPackRefreshBtn.disabled = true;
  hathPackInventoryPromise = api("/api/integrations/hath/pack/inventory")
    .then((payload) => {
      hathPackInventory = payload;
      renderHathPackInventory(payload);
      return payload;
    })
    .finally(() => {
      hathPackInventoryPromise = null;
      hathPackRefreshBtn.disabled = false;
    });
  return hathPackInventoryPromise;
}

function defaultHathArchiveName() {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}.zip`;
}

function renderHathPackInventory(payload) {
  const candidates = payload.candidates || [];
  const mega = payload.mega || {};
  const trash = payload.trash || {};
  if (!hathPackArchiveNameEl.value.trim()) {
    hathPackArchiveNameEl.value = defaultHathArchiveName();
  }
  hathPackMegaAccountEl.textContent = mega.account || "Not connected";
  hathPackMegaAccountEl.title = mega.remote_cwd ? `Current MEGA folder: ${mega.remote_cwd}` : (mega.error || "");
  if (!mega.available) {
    hathPackUploadEl.checked = false;
  }
  hathPackUploadEl.disabled = !mega.available;
  hathPackSummaryEl.textContent = `${candidates.length} archive candidate${candidates.length === 1 ? "" : "s"} on ${payload.download_dir || "the H@H host"} · trash ${formatHathBytes(trash.size_bytes || 0)}`;
  hathPackCandidatesEl.innerHTML = candidates.length
    ? `
      <div class="hath-pack-candidate hath-pack-candidate-header" aria-hidden="true">
        <span></span><span>Source</span><span>Kind</span><span>Files</span><span>Size</span><span>Modified</span>
      </div>
      ${candidates.map((candidate) => `
        <label class="hath-pack-candidate${candidate.active ? " is-active" : ""}">
          <input type="checkbox" data-hath-pack-source value="${escapeAttr(candidate.name)}" ${candidate.active ? "disabled" : ""}>
          <span title="${escapeAttr(candidate.name)}">${escapeHtml(candidate.name)}</span>
          <span>${candidate.active ? '<span class="hath-badge hath-tone-active">Active</span>' : escapeHtml(candidate.kind || "gallery")}</span>
          <span>${escapeHtml(Number(candidate.file_count || 0).toLocaleString())}</span>
          <span>${escapeHtml(formatHathBytes(candidate.size_bytes || 0))}</span>
          <span title="${escapeAttr(formatHathDate(candidate.modified_at))}">${escapeHtml(relativeHathTime(candidate.modified_at))}</span>
        </label>
      `).join("")}
    `
    : '<div class="hath-empty">No gallery directories or ZIP files are available.</div>';
  renderHathTrashInventory(trash);
  invalidateHathPackPreview();
  updateHathPackSelection();
}

function renderHathTrashInventory(trash) {
  const jobs = trash.jobs || [];
  hathTrashCandidatesEl.innerHTML = jobs.length
    ? `
      <div class="hath-pack-candidate hath-pack-candidate-header hath-trash-candidate" aria-hidden="true">
        <span></span><span>Archive job</span><span>Files</span><span>Size</span><span>Modified</span>
      </div>
      ${jobs.map((job) => `
        <label class="hath-pack-candidate hath-trash-candidate">
          <input type="checkbox" data-hath-trash-job value="${escapeAttr(job.name)}">
          <span title="${escapeAttr(job.name)}">${escapeHtml(job.name)}</span>
          <span>${escapeHtml(Number(job.file_count || 0).toLocaleString())}</span>
          <span>${escapeHtml(formatHathBytes(job.size_bytes || 0))}</span>
          <span title="${escapeAttr(formatHathDate(job.modified_at))}">${escapeHtml(relativeHathTime(job.modified_at))}</span>
        </label>
      `).join("")}
    `
    : '<div class="hath-empty">Recoverable trash is empty.</div>';
  invalidateHathTrashPreview();
  updateHathTrashSelection();
}

function selectedHathTrashNames() {
  return [...hathTrashCandidatesEl.querySelectorAll("input[data-hath-trash-job]:checked")]
    .map((input) => input.value);
}

function updateHathTrashSelection() {
  const selectedNames = new Set(selectedHathTrashNames());
  const trash = hathPackInventory?.trash || {};
  const jobs = trash.jobs || [];
  const selected = jobs.filter((job) => selectedNames.has(job.name));
  const selectedBytes = selected.reduce((sum, job) => sum + Number(job.size_bytes || 0), 0);
  hathTrashSummaryEl.textContent = selected.length
    ? `${selected.length} selected · ${formatHathBytes(selectedBytes)}`
    : `${jobs.length} job${jobs.length === 1 ? "" : "s"} · ${formatHathBytes(trash.size_bytes || 0)} · ${Number(trash.file_count || 0).toLocaleString()} files`;
  updateHathTrashControls();
}

function updateHathTrashControls() {
  const running = hathPackStatusPayload?.state === "running";
  const configured = hathPackStatusPayload?.configured === true;
  hathTrashPreviewBtn.disabled = running || !configured || selectedHathTrashNames().length === 0;
  hathTrashDeleteBtn.disabled = running || !configured || !hathTrashPreview;
}

function invalidateHathTrashPreview() {
  hathTrashPreview = null;
  hathTrashPlanEl.classList.add("hidden");
  hathTrashPlanEl.innerHTML = "";
  updateHathTrashControls();
}

function selectedHathPackNames() {
  return [...hathPackCandidatesEl.querySelectorAll("input[data-hath-pack-source]:checked")]
    .map((input) => input.value);
}

function updateHathPackSelection() {
  const selectedNames = new Set(selectedHathPackNames());
  const candidates = hathPackInventory?.candidates || [];
  const selected = candidates.filter((candidate) => selectedNames.has(candidate.name));
  const selectedBytes = selected.reduce((sum, candidate) => sum + Number(candidate.size_bytes || 0), 0);
  hathPackSelectionSummaryEl.textContent = `${selected.length} selected · ${formatHathBytes(selectedBytes)}`;
  updateHathPackControls();
}

function updateHathPackControls() {
  const running = hathPackStatusPayload?.state === "running";
  const hasSelection = selectedHathPackNames().length > 0;
  const configured = hathPackStatusPayload?.configured === true;
  hathPackPreviewBtn.disabled = running || !configured || !hasSelection;
  hathPackBtn.disabled = running || !configured || !hathPackPreview;
  hathPackMegaDestinationEl.disabled = !hathPackUploadEl.checked;
  hathPackTrashArchiveEl.disabled = !hathPackUploadEl.checked;
  updateHathTrashControls();
}

function invalidateHathPackPreview() {
  hathPackPreview = null;
  hathPackPlanEl.classList.add("hidden");
  hathPackPlanEl.innerHTML = "";
  updateHathPackControls();
}

function hathPackRequest() {
  return {
    selected: selectedHathPackNames(),
    archive_name: hathPackArchiveNameEl.value.trim(),
    upload: hathPackUploadEl.checked,
    mega_destination: hathPackMegaDestinationEl.value.trim(),
    trash_sources: hathPackTrashSourcesEl.checked,
    trash_archive_after_upload: hathPackTrashArchiveEl.checked,
    cleanup_mode: hathPackCleanupModeEl.value,
  };
}

async function previewHathPack() {
  invalidateHathPackPreview();
  hathPackPreviewBtn.disabled = true;
  setStatus("Generating H@H archive dry run");
  const payload = await api("/api/integrations/hath/pack/preview", {
    method: "POST",
    body: JSON.stringify(hathPackRequest()),
  });
  hathPackPreview = payload;
  renderHathPackPlan(payload);
  updateHathPackControls();
  setStatus(`Dry run ready for ${payload.selected_count || 0} archive sources`);
}

function renderHathPackPlan(plan) {
  const request = plan.request || {};
  hathPackPlanEl.classList.remove("hidden");
  hathPackPlanEl.innerHTML = `
    <div class="hath-pack-plan-summary">
      <span class="hath-badge hath-tone-good">Dry Run</span>
      <strong>${escapeHtml(request.archive_name || "Archive")}</strong>
      <span>${escapeHtml(Number(plan.selected_count || 0).toLocaleString())} sources</span>
      <span>${escapeHtml(Number(plan.input_files || 0).toLocaleString())} files</span>
      <span>${escapeHtml(formatHathBytes(plan.input_bytes || 0))}</span>
    </div>
    <ol>${(plan.actions || []).map((action) => `<li>${escapeHtml(action.message)}</li>`).join("")}</ol>
  `;
}

async function previewHathTrash() {
  invalidateHathTrashPreview();
  hathTrashPreviewBtn.disabled = true;
  setStatus("Generating archive trash deletion dry run");
  const payload = await api("/api/integrations/hath/trash/preview", {
    method: "POST",
    body: JSON.stringify({ selected: selectedHathTrashNames() }),
  });
  hathTrashPreview = payload;
  hathTrashPlanEl.classList.remove("hidden");
  hathTrashPlanEl.innerHTML = `
    <div class="hath-pack-plan-summary">
      <span class="hath-badge hath-tone-danger">Permanent</span>
      <strong>${escapeHtml(Number(payload.selected_count || 0).toLocaleString())} trash jobs</strong>
      <span>${escapeHtml(Number(payload.input_files || 0).toLocaleString())} files</span>
      <span>${escapeHtml(formatHathBytes(payload.input_bytes || 0))} to free</span>
    </div>
    <ol>${(payload.actions || []).map((action) => `<li>${escapeHtml(action.message)}</li>`).join("")}</ol>
  `;
  updateHathTrashControls();
  setStatus(`Trash dry run ready for ${payload.selected_count || 0} jobs`);
}

async function startHathTrashDelete() {
  if (!hathTrashPreview) return;
  if (!confirm(
    `Permanently delete ${hathTrashPreview.selected_count || 0} trash jobs?\n\nFiles: ${Number(hathTrashPreview.input_files || 0).toLocaleString()}\nSpace to free: ${formatHathBytes(hathTrashPreview.input_bytes || 0)}\n\nThis cannot be undone.`
  )) {
    return;
  }
  hathTrashDeleteBtn.disabled = true;
  setStatus("Starting permanent archive trash deletion");
  const payload = await api("/api/integrations/hath/trash/start", {
    method: "POST",
    body: JSON.stringify({ preview_id: hathTrashPreview.preview_id }),
  });
  hathTrashPreview = null;
  renderHathPackStatus(payload);
  setStatus("Archive trash deletion started");
}

function renderHathPackStatus(payload) {
  const previousState = hathPackStatusPayload?.state;
  hathPackStatusPayload = payload;
  const state = String(payload.state || "idle");
  const states = {
    idle: { label: "Idle", tone: "neutral" },
    running: { label: "Running", tone: "active" },
    succeeded: { label: "Succeeded", tone: "good" },
    failed: { label: "Failed", tone: "danger" },
  };
  const display = states[state] || states.idle;
  hathPackBadgeEl.textContent = payload.configured ? display.label : "Unavailable";
  hathPackBadgeEl.className = `hath-badge hath-tone-${payload.configured ? display.tone : "warning"}`;

  if (!payload.configured) {
    hathPackSummaryEl.textContent = payload.configuration_error || "Remote archive task is not configured";
  } else if (state === "running") {
    hathPackSummaryEl.textContent = payload.message || `Running on ${payload.remote_host || "the H@H host"}`;
  } else if (state === "succeeded") {
    hathPackSummaryEl.textContent = payload.message || "Archive job completed successfully";
  } else if (state === "failed") {
    hathPackSummaryEl.textContent = payload.error || "The remote archive task failed";
  } else if (!hathPackInventory) {
    hathPackSummaryEl.textContent = `Ready on ${payload.remote_host || "the H@H host"}`;
  }

  const timing = [];
  if (payload.started_at) timing.push(`Started ${formatHathDate(payload.started_at)}`);
  if (payload.finished_at) timing.push(`Finished ${formatHathDate(payload.finished_at)}`);
  if (Number.isInteger(payload.exit_code)) timing.push(`Exit ${payload.exit_code}`);
  if (payload.completed != null && payload.total != null) {
    timing.push(`${Number(payload.completed).toLocaleString()}/${Number(payload.total).toLocaleString()}`);
  }
  hathPackTimingEl.textContent = timing.join(" · ") || "Not run in this server session";
  const logs = payload.logs || [];
  hathPackLogEl.textContent = logs.length ? logs.join("\n") : "No archive job has run in this server session.";
  hathPackLogEl.scrollTop = hathPackLogEl.scrollHeight;

  if (hathPackPollTimer) {
    clearTimeout(hathPackPollTimer);
    hathPackPollTimer = null;
  }
  if (state === "running") {
    hathPackPollTimer = setTimeout(() => {
      loadHathPackStatus().catch((error) => setStatus(error.message, true));
    }, 2000);
  }
  if (previousState === "running" && state !== "running") {
    loadHathPackInventory().catch((error) => setStatus(error.message, true));
  }
  updateHathPackControls();
}

async function startHathPack() {
  if (!hathPackPreview) return;
  const request = hathPackPreview.request || {};
  const cleanup = [];
  if (request.trash_sources) cleanup.push("selected sources");
  if (request.trash_archive_after_upload) cleanup.push("the local archive");
  const permanentCleanup = cleanup.length && request.cleanup_mode === "delete";
  if (!confirm(
    `Run the previewed archive plan?\n\nArchive: ${request.archive_name}\nSources: ${hathPackPreview.selected_count}\nMEGA: ${request.upload ? `${hathPackMegaAccountEl.textContent} · ${hathPackPreview.mega_upload_path || request.mega_destination}` : "No upload"}`
    + (cleanup.length
      ? `\nCleanup: ${permanentCleanup ? "PERMANENTLY DELETE" : "move to recoverable trash"} ${cleanup.join(" and ")}`
      : "")
    + (permanentCleanup ? "\n\nThis cleanup cannot be undone." : "")
  )) {
    return;
  }
  hathPackBtn.disabled = true;
  setStatus("Starting H@H archive job");
  const payload = await api("/api/integrations/hath/pack/start", {
    method: "POST",
    body: JSON.stringify({ preview_id: hathPackPreview.preview_id }),
  });
  hathPackPreview = null;
  renderHathPackStatus(payload);
  setStatus("H@H archive job started");
}

function renderHathStatus(payload) {
  hathStatusPayload = payload;
  const clients = payload.clients || [];
  const downloads = payload.downloads || [];
  const counts = payload.counts || {};
  const summary = payload.summary || {};
  const signals = summary.signals || {};
  const now = Date.now();
  const connectedClients = clients.filter((client) => !hathClientIsStale(client, now));
  const activeDownloads = connectedClients.reduce(
    (sum, client) => sum + Math.max(0, Number((client.metrics || {}).active_downloads || 0)),
    0,
  );
  const completed = Number(counts.completed || 0);
  const failed = Number(counts.failed || 0);
  const lastActivity = newestHathTimestamp(clients, downloads, summary);
  const downloadedFiles = Number(summary.downloaded_files || 0);
  const downloadedBytes = Number(summary.downloaded_bytes || 0);
  const implicitLikes = Number(signals["hath-download"] || 0);

  hathClientCountEl.textContent = connectedClients.length.toLocaleString();
  hathClientCountEl.title = `${connectedClients.length} of ${clients.length} observers connected`;
  if (!clients.length) {
    hathConnectionSummaryEl.textContent = "No observer connected";
  } else if (connectedClients.length === clients.length) {
    hathConnectionSummaryEl.textContent = `${connectedClients.length} observer${connectedClients.length === 1 ? "" : "s"} connected`;
  } else {
    hathConnectionSummaryEl.textContent = `${connectedClients.length} of ${clients.length} observers connected`;
  }
  hathUpdatedAtEl.textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
  hathSummaryEl.innerHTML = [
    hathSummaryItem("Connected", `${connectedClients.length}/${clients.length}`, clients.length && connectedClients.length === clients.length ? "good" : connectedClients.length ? "warning" : "neutral"),
    hathSummaryItem("Active", activeDownloads.toLocaleString(), activeDownloads ? "active" : "neutral"),
    hathSummaryItem("Completed", completed.toLocaleString(), completed ? "good" : "neutral"),
    hathSummaryItem("Failed", failed.toLocaleString(), failed ? "danger" : "neutral"),
    hathSummaryItem("Stored", formatHathBytes(downloadedBytes), downloadedBytes ? "good" : "neutral"),
    hathSummaryItem("Files", downloadedFiles.toLocaleString(), downloadedFiles ? "good" : "neutral"),
    hathSummaryItem("Implicit Likes", implicitLikes.toLocaleString(), implicitLikes ? "active" : "neutral"),
    hathSummaryItem("Last Event", lastActivity ? relativeHathTime(lastActivity, now) : "Never", "neutral"),
  ].join("");

  hathClientSummaryEl.textContent = clients.length
    ? `${connectedClients.length} connected, ${clients.length - connectedClients.length} stale`
    : "No clients";
  hathClientsEl.innerHTML = clients.length
    ? clients.map((client) => renderHathClient(client, now)).join("")
    : '<div class="hath-empty">No H@H observer has connected yet.</div>';
  renderHathP2p(clients, connectedClients, now);
  renderHathDownloadList(payload, now);
}

function hathSummaryItem(label, value, tone) {
  return `
    <div class="hath-stat">
      <span>${escapeHtml(label)}</span>
      <strong class="hath-tone-${escapeAttr(tone)}">${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderHathClient(client, now) {
  const metrics = client.metrics || {};
  const statistics = client.statistics || {};
  const health = hathClientHealth(client, now);
  const activeCount = Math.max(0, Number(metrics.active_downloads || 0));
  const currentTitle = client.active_title || (client.active_gid ? `Gallery ${client.active_gid}` : "");
  const recentServes = Math.max(0, Number(metrics.serve_requests_5m || 0));
  const current = currentTitle
    ? `${currentTitle}${client.active_gid ? ` (#${client.active_gid})` : ""}`
    : activeCount
      ? `${activeCount} active download${activeCount === 1 ? "" : "s"}`
      : recentServes
        ? `Serving cached images · ${recentServes.toLocaleString()} in 5 min`
        : "Idle";
  const process = typeof metrics.process_running === "boolean"
    ? metrics.process_running ? "Running" : "Stopped"
    : client.status === "offline" ? "Stopped" : "Unknown";
  const lastSeen = client.last_event_at || client.last_seen_at;
  const hostname = client.hostname || "Unknown host";
  const agentVersion = client.agent_version ? `observer ${client.agent_version}` : "observer version unknown";
  const disk = Number.isFinite(Number(metrics.disk_free_bytes)) ? formatHathBytes(metrics.disk_free_bytes) : "Unknown";
  const logState = metrics.log_available === true ? "Available" : metrics.log_available === false ? "Unavailable" : "Unknown";
  const reportedStatus = String(client.status || "unknown").replaceAll("-", " ");
  const eventType = formatHathEventType(statistics.last_event_type);
  const eventTime = statistics.last_event_received_at || client.last_event_at;
  return `
    <article class="hath-client">
      <header>
        <div>
          <strong>${escapeHtml(client.client_id || "Unnamed client")}</strong>
          <span>${escapeHtml(hostname)} · ${escapeHtml(agentVersion)}</span>
        </div>
        <span class="hath-badge hath-tone-${escapeAttr(health.tone)}">${escapeHtml(health.label)}</span>
      </header>
      <div class="hath-current">
        <span>Current</span>
        <strong>${escapeHtml(current)}</strong>
      </div>
      <dl class="hath-client-metrics">
        <div><dt>H@H Process</dt><dd>${escapeHtml(process)}</dd></div>
        <div><dt>Observer State</dt><dd>${escapeHtml(reportedStatus)}</dd></div>
        <div><dt>Last Heartbeat</dt><dd title="${escapeAttr(formatHathDate(lastSeen))}">${escapeHtml(relativeHathTime(lastSeen, now))}</dd></div>
        <div><dt>Disk Available</dt><dd>${escapeHtml(disk)}</dd></div>
        <div><dt>Active</dt><dd>${escapeHtml(activeCount.toLocaleString())}</dd></div>
        <div><dt>Tracked</dt><dd>${escapeHtml(Number(statistics.tracked_downloads ?? metrics.downloads_seen ?? 0).toLocaleString())}</dd></div>
        <div><dt>Completed</dt><dd>${escapeHtml(Number(statistics.completed_downloads || 0).toLocaleString())}</dd></div>
        <div><dt>Failed</dt><dd class="${Number(statistics.failed_downloads || 0) ? "hath-tone-danger" : ""}">${escapeHtml(Number(statistics.failed_downloads || 0).toLocaleString())}</dd></div>
        <div><dt>Downloaded</dt><dd>${escapeHtml(formatHathBytes(statistics.downloaded_bytes || 0))}</dd></div>
        <div><dt>Files</dt><dd>${escapeHtml(Number(statistics.downloaded_files || 0).toLocaleString())}</dd></div>
        <div><dt>Log</dt><dd>${escapeHtml(logState)}</dd></div>
        <div><dt>Events</dt><dd>${escapeHtml(Number(statistics.event_count || 0).toLocaleString())}</dd></div>
        <div><dt>Latest Event</dt><dd title="${escapeAttr(formatHathDate(eventTime))}">${escapeHtml(eventType)} · ${escapeHtml(relativeHathTime(eventTime, now))}</dd></div>
      </dl>
      ${client.last_error ? `<div class="hath-error">${escapeHtml(client.last_error)}</div>` : ""}
    </article>
  `;
}

function formatHathEventType(value) {
  const eventType = String(value || "").trim();
  if (!eventType) return "None";
  return eventType.split(".").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function hathClientHealth(client, now = Date.now()) {
  if (hathClientIsStale(client, now)) {
    return { label: "Stale", tone: "danger" };
  }
  const metrics = client.metrics || {};
  if (metrics.process_running === false || client.status === "offline") {
    return { label: "H@H stopped", tone: "danger" };
  }
  if (String(client.status || "").startsWith("suspended")) {
    return { label: "Suspended", tone: "warning" };
  }
  if (Number(metrics.active_downloads || 0) > 0 || client.active_gid) {
    return { label: "Downloading", tone: "active" };
  }
  if (Number(metrics.serve_requests_5m || 0) > 0) {
    return { label: "Serving", tone: "active" };
  }
  return { label: client.status === "idle" ? "Idle" : "Connected", tone: "good" };
}

function renderHathP2p(clients, connectedClients, now) {
  const allMetrics = clients.map((client) => client.metrics || {});
  const liveMetrics = connectedClients.map((client) => client.metrics || {});
  const totalRequests = sumHathMetric(allMetrics, "serve_requests_total");
  const totalBytes = sumHathMetric(allMetrics, "serve_bytes_total");
  const totalSuccesses = sumHathMetric(allMetrics, "serve_successes_total");
  const requests5m = sumHathMetric(liveMetrics, "serve_requests_5m");
  const bytes5m = sumHathMetric(liveMetrics, "serve_bytes_5m");
  const requests1h = sumHathMetric(liveMetrics, "serve_requests_1h");
  const bytes1h = sumHathMetric(liveMetrics, "serve_bytes_1h");
  const errorsTotal = Math.max(0, totalRequests - totalSuccesses);
  const successRate = totalRequests ? totalSuccesses / totalRequests * 100 : null;
  const activeServers = connectedClients.filter((client) => Number((client.metrics || {}).serve_requests_5m || 0) > 0).length;

  hathP2pSummaryEl.textContent = clients.length
    ? `${activeServers} serving recently · ${formatHathBytes(bytes1h)} in the last hour`
    : "No serving telemetry";
  hathP2pOverviewEl.innerHTML = [
    hathSummaryItem("Requests · 5 min", requests5m.toLocaleString(), requests5m ? "active" : "neutral"),
    hathSummaryItem("Traffic · 5 min", formatHathBytes(bytes5m), bytes5m ? "active" : "neutral"),
    hathSummaryItem("Average · 5 min", formatHathRate(bytes5m / 300), bytes5m ? "good" : "neutral"),
    hathSummaryItem("Requests · 1 hour", requests1h.toLocaleString(), requests1h ? "good" : "neutral"),
    hathSummaryItem("Traffic · 1 hour", formatHathBytes(bytes1h), bytes1h ? "good" : "neutral"),
    hathSummaryItem("Requests · total", totalRequests.toLocaleString(), totalRequests ? "good" : "neutral"),
    hathSummaryItem("Traffic · total", formatHathBytes(totalBytes), totalBytes ? "good" : "neutral"),
    hathSummaryItem("Success", successRate == null ? "No data" : `${successRate.toFixed(2)}%`, errorsTotal ? "warning" : totalRequests ? "good" : "neutral"),
  ].join("");
  hathP2pClientsEl.innerHTML = clients.length
    ? clients.map((client) => renderHathP2pClient(client, now)).join("")
    : '<div class="hath-empty">No H@H serving telemetry has been reported.</div>';
}

function renderHathP2pClient(client, now) {
  const metrics = client.metrics || {};
  const totalRequests = Math.max(0, Number(metrics.serve_requests_total || 0));
  const totalSuccesses = Math.max(0, Number(metrics.serve_successes_total || 0));
  const successRate = totalRequests ? totalSuccesses / totalRequests * 100 : null;
  const cacheSize = Math.max(0, Number(metrics.cache_size_bytes || 0));
  const cacheOverhead = Math.max(cacheSize, Number(metrics.cache_size_with_overhead_bytes || 0));
  const cacheLimit = Math.max(0, Number(metrics.cache_limit_bytes || 0));
  const cachePercent = cacheLimit ? Math.max(0, Math.min(100, cacheOverhead / cacheLimit * 100)) : 0;
  const jvmTotal = Math.max(0, Number(metrics.jvm_memory_total_bytes || 0));
  const jvmFree = Math.max(0, Number(metrics.jvm_memory_free_bytes || 0));
  const jvmUsed = Math.max(0, jvmTotal - jvmFree);
  const jvmMax = Math.max(0, Number(metrics.jvm_memory_max_bytes || 0));
  const requests1h = Math.max(0, Number(metrics.serve_requests_1h || 0));
  const bytes1h = Math.max(0, Number(metrics.serve_bytes_1h || 0));
  const lastServe = metrics.last_serve_at;
  const isStale = hathClientIsStale(client, now);
  const serving = !isStale && Number(metrics.serve_requests_5m || 0) > 0;
  const stateLabel = isStale ? "Telemetry stale" : serving ? "Serving now" : "Ready";
  const stateTone = isStale ? "danger" : serving ? "active" : "good";
  return `
    <article class="hath-p2p-client">
      <header>
        <div>
          <strong>${escapeHtml(client.client_id || "Unnamed client")}</strong>
          <span>${lastServe ? `Last served ${escapeHtml(relativeHathTime(lastServe, now))}` : "No image request observed"}</span>
        </div>
        <span class="hath-badge hath-tone-${stateTone}">${stateLabel}</span>
      </header>
      <div class="hath-p2p-metrics">
        <div><span>Requests · 1 hour</span><strong>${requests1h.toLocaleString()}</strong></div>
        <div><span>Traffic · 1 hour</span><strong>${escapeHtml(formatHathBytes(bytes1h))}</strong></div>
        <div><span>Average · 1 hour</span><strong>${escapeHtml(formatHathRate(bytes1h / 3600))}</strong></div>
        <div><span>Requests · total</span><strong>${totalRequests.toLocaleString()}</strong></div>
        <div><span>Traffic · total</span><strong>${escapeHtml(formatHathBytes(metrics.serve_bytes_total || 0))}</strong></div>
        <div><span>Success</span><strong>${successRate == null ? "No data" : `${successRate.toFixed(2)}%`}</strong></div>
        <div><span>Proxy tests</span><strong>${Math.max(0, Number(metrics.proxy_tests_total || 0)).toLocaleString()}</strong></div>
        <div><span>Handshake stops · 1 hour</span><strong class="${Number(metrics.handshake_interrupts_1h || 0) ? "hath-tone-warning" : ""}">${Math.max(0, Number(metrics.handshake_interrupts_1h || 0)).toLocaleString()}</strong></div>
        <div><span>H@H uptime</span><strong>${escapeHtml(formatHathDuration(metrics.process_uptime_seconds))}</strong></div>
      </div>
      <div class="hath-resource-bars">
        ${hathResourceBar("Cache", cacheOverhead, cacheLimit, cachePercent, `${formatHathBytes(cacheSize)} data · ${formatHathBytes(metrics.cache_free_bytes || 0)} free`)}
        ${hathResourceBar("JVM memory", jvmUsed, jvmMax, jvmMax ? Math.max(0, Math.min(100, jvmUsed / jvmMax * 100)) : 0, `${formatHathBytes(jvmUsed)} used · ${formatHathBytes(jvmMax)} max`)}
      </div>
      <span class="hath-metrics-since">Observed since ${escapeHtml(formatHathDate(metrics.metrics_started_at))}</span>
    </article>
  `;
}

function hathResourceBar(label, value, maximum, percent, detail) {
  const hasLimit = Number(maximum) > 0;
  return `
    <div class="hath-resource-bar">
      <div><span>${escapeHtml(label)}</span><strong>${hasLimit ? `${percent.toFixed(1)}%` : "Unknown"}</strong></div>
      <progress max="100" value="${hasLimit ? percent : 0}" aria-label="${escapeAttr(label)} utilization"></progress>
      <span>${escapeHtml(hasLimit ? detail : "Not reported")}</span>
    </div>
  `;
}

function sumHathMetric(metrics, key) {
  return metrics.reduce((sum, item) => sum + Math.max(0, Number(item[key] || 0)), 0);
}

function formatHathRate(bytesPerSecond) {
  return `${formatHathBytes(Math.max(0, Number(bytesPerSecond || 0)))}/s`;
}

function formatHathDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "Unknown";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function hathClientIsStale(client, now = Date.now()) {
  const timestamp = parseHathTimestamp(client.last_event_at || client.last_seen_at);
  return !timestamp || now - timestamp > hathStaleAfterMs;
}

function renderHathDownloadList(payload, now = Date.now()) {
  const downloads = payload.downloads || [];
  const summary = payload.summary || {};
  const filter = hathDownloadFilterEl.value;
  const query = hathDownloadSearchEl.value.trim().toLocaleLowerCase();
  const filtered = downloads.filter((download) => {
    const status = String(download.status || "discovered");
    const statusMatches = filter === "all"
      || filter === status
      || (filter === "active" && (status === "discovered" || status === "downloading"));
    if (!statusMatches) return false;
    if (!query) return true;
    const haystack = [
      download.gallery_title,
      download.title,
      download.gid,
      download.directory_name,
      download.client_id,
      download.category,
      download.uploader,
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    return haystack.includes(query);
  });
  const totalDownloads = Number(summary.tracked_downloads
    ?? Object.values(payload.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0));
  const linked = Number(summary.linked_downloads || 0);
  const scope = filtered.length === downloads.length ? `${downloads.length} shown` : `${filtered.length} of ${downloads.length} shown`;
  hathDownloadSummaryEl.textContent = totalDownloads
    ? `${totalDownloads.toLocaleString()} tracked · ${linked.toLocaleString()} linked · ${scope}`
    : "No downloads";
  hathDownloadsEl.innerHTML = filtered.length
    ? renderHathDownloads(filtered, now)
    : `<div class="hath-empty">${downloads.length ? "No downloads match the current filter." : "No download activity has been reported."}</div>`;
}

function renderHathDownloads(downloads, now) {
  const rows = downloads.map((download) => {
    const status = String(download.status || "discovered");
    const tone = status === "completed" ? "good" : status === "failed" ? "danger" : status === "downloading" ? "active" : "neutral";
    const title = download.gallery_title || download.title || `Gallery ${download.gid || "unknown"}`;
    const resolution = download.resolution === "org" ? "Original" : download.resolution ? `${download.resolution}px` : "Unknown";
    const downloadedFiles = Math.max(0, Number(download.downloaded_files || 0));
    const totalFiles = Number(download.total_files);
    const progress = Number.isFinite(totalFiles) && totalFiles > 0
      ? `${downloadedFiles.toLocaleString()} / ${totalFiles.toLocaleString()} files`
      : `${downloadedFiles.toLocaleString()} files`;
    const progressValue = Number.isFinite(totalFiles) && totalFiles > 0
      ? Math.max(0, Math.min(100, downloadedFiles / totalFiles * 100))
      : null;
    const titleMarkup = download.gallery_url
      ? `<a href="${escapeAttr(download.gallery_url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
      : `<strong>${escapeHtml(title)}</strong>`;
    const galleryMeta = [
      download.category,
      download.uploader ? `by ${download.uploader}` : "",
      Number(download.page_count) > 0 ? `${Number(download.page_count).toLocaleString()} pages` : "",
      download.rating != null && Number.isFinite(Number(download.rating)) ? `rating ${Number(download.rating).toFixed(2)}` : "",
    ].filter(Boolean).join(" · ");
    const directory = download.directory_name
      ? `<span class="hath-directory" title="${escapeAttr(download.directory_name)}">${escapeHtml(download.directory_name)}</span>`
      : "";
    const signal = hathDownloadSignal(download);
    const error = download.last_error ? `<span class="hath-row-error">${escapeHtml(download.last_error)}</span>` : "";
    const activityAt = download.completed_at || download.failed_at || download.started_at || download.updated_at;
    const activityLabel = download.completed_at ? "Completed" : download.failed_at ? "Failed" : download.started_at ? "Started" : "Updated";
    return `
      <tr>
        <td><span class="hath-badge hath-tone-${escapeAttr(tone)}">${escapeHtml(status)}</span></td>
        <td class="hath-gallery-cell">
          ${titleMarkup}
          <span>gid ${escapeHtml(download.gid || "-")} · ${escapeHtml(resolution)}${galleryMeta ? ` · ${escapeHtml(galleryMeta)}` : ""}</span>
          ${directory}${error}
        </td>
        <td><span class="hath-badge hath-tone-${escapeAttr(signal.tone)}">${escapeHtml(signal.label)}</span></td>
        <td class="hath-progress-cell">
          <span>${escapeHtml(progress)}</span>
          ${progressValue == null ? "" : `<progress max="100" value="${escapeAttr(progressValue.toFixed(2))}"></progress>`}
        </td>
        <td>${escapeHtml(formatHathBytes(download.downloaded_bytes || 0))}</td>
        <td>${escapeHtml(download.client_id || "-")}</td>
        <td class="hath-time-cell">
          <span title="${escapeAttr(formatHathDate(activityAt))}">${escapeHtml(activityLabel)} ${escapeHtml(relativeHathTime(activityAt, now))}</span>
          <span title="${escapeAttr(formatHathDate(download.updated_at))}">Updated ${escapeHtml(relativeHathTime(download.updated_at, now))}</span>
        </td>
      </tr>
    `;
  }).join("");
  return `
    <div class="hath-table-wrap">
      <table class="hath-table">
        <thead><tr><th>Status</th><th>Gallery</th><th>Signal</th><th>Progress</th><th>Size</th><th>Client</th><th>Activity</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function hathDownloadSignal(download) {
  const signal = String(download.recommendation_signal || "");
  if (signal === "favorite") return { label: "Favorite", tone: "good" };
  if (signal === "ban") return { label: "Ban", tone: "danger" };
  if (signal === "positive-vote") {
    return download.feedback_score != null
      ? { label: `Score ${download.feedback_score}`, tone: "good" }
      : { label: "Thumbs up", tone: "good" };
  }
  if (signal === "negative-vote") {
    return download.feedback_score != null
      ? { label: `Score ${download.feedback_score}`, tone: "danger" }
      : { label: "Thumbs down", tone: "danger" };
  }
  if (signal === "hath-download") return { label: "Implicit like", tone: "active" };
  return { label: "None", tone: "neutral" };
}

function newestHathTimestamp(clients, downloads, summary = {}) {
  const timestamps = [
    ...clients.map((client) => client.last_event_at || client.last_seen_at),
    ...downloads.map((download) => download.updated_at),
    summary.last_event_received_at,
    summary.last_download_at,
  ].map(parseHathTimestamp).filter(Boolean);
  return timestamps.length ? Math.max(...timestamps) : null;
}

function parseHathTimestamp(value) {
  if (!value) {
    return null;
  }
  const text = String(value).trim();
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(text) ? text : `${text.replace(" ", "T")}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function relativeHathTime(value, now = Date.now()) {
  const timestamp = typeof value === "number" ? value : parseHathTimestamp(value);
  if (!timestamp) {
    return "Unknown";
  }
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatHathDate(value) {
  const timestamp = parseHathTimestamp(value);
  return timestamp ? new Date(timestamp).toLocaleString() : "Unknown";
}

function formatHathBytes(value) {
  let size = Math.max(0, Number(value || 0));
  if (!Number.isFinite(size)) {
    return "Unknown";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const precision = unit === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(precision)} ${units[unit]}`;
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
  refreshQueueCounts();
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
  parentProgressVisible = true;
  renderParentUpdateProgress({
    running: true,
    stage: "starting",
    message: "Starting parent update",
    logs: [],
  });
  startParentProgressPolling();
  try {
    const payload = await api("/api/reactions/backfill-parents", {
      method: "POST",
      body: JSON.stringify({
        scope: "all",
        limit: 100,
        filter_text: localFilterEl.value.trim(),
        gallery_urls: [...renderedGalleryUrls],
      }),
    });
    if (reloadView === "history") {
      applyGalleryPage(payload);
    } else {
      await loadCurrentPage();
    }
    const detailText = payload.detail_checked ? `; checked ${payload.detail_checked} source pages` : "";
    const errors = [...(payload.errors || []), ...(payload.parent_errors || [])];
    if (errors.length) {
      setStatus(`Updated ${payload.updated} metadata rows${detailText}; errors: ${errors.join(" | ")}`, true);
    } else {
      setStatus(
        `Updated ${payload.updated} metadata rows (${payload.parent_updated} parent links, ${payload.parent_enriched || 0} ancestor records, ${payload.title_jpn_updated} alternate titles)${detailText}`
      );
    }
  } finally {
    await loadStatus().catch(() => {});
  }
}

async function backfillReviewParents() {
  setStatus("Updating parent metadata for Review galleries");
  await backfillParentsForCurrentFilter({ reloadView: currentView });
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
  const requestedView = currentView;
  const requestId = ++recommendationRequestId;
  const localFilter = localFilterEl.value.trim();
  const includeRated = requestedView === "preview" ? "1" : "0";
  const freshnessWeight = requestedView === "preview" ? previewFreshnessWeightEl.value || "8" : "1";
  const postedAfter = requestedView === "preview" ? previewPostedAfterEl.value || "" : "";
  const bootstrapExploreCount = "0";
  const reviewBand = requestedView === "review" || requestedView === "low-interest";
  const requireBootstrapMatch = reviewBand && reviewRequireBootstrapMatchEl.checked ? "1" : "0";
  const interestBand = requestedView === "review" ? "primary" : requestedView === "low-interest" ? "low" : "all";
  const languageFilter = languageFilterEl.value.trim();
  const modelMode = modelModeEl.value || "hybrid";
  if (requestedView === "review" && !append && offset === 0) {
    reviewExploreSeed = `${Date.now()}-${Math.random()}`;
  }
  const payload = await api(
    `/api/recommendations?include_rated=${includeRated}&freshness_weight=${encodeURIComponent(freshnessWeight)}&posted_after=${encodeURIComponent(postedAfter)}&bootstrap_explore_count=${bootstrapExploreCount}&require_bootstrap_match=${requireBootstrapMatch}&interest_band=${encodeURIComponent(interestBand)}&explore_seed=${encodeURIComponent(reviewExploreSeed)}&language_filter=${encodeURIComponent(languageFilter)}&model_mode=${encodeURIComponent(modelMode)}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  if (requestId !== recommendationRequestId || currentView !== requestedView) {
    return;
  }
  applyGalleryPage(payload, append);
  renderLowInterestPolicy(payload.low_interest_policy, requestedView);
  recordPageImpressions(payload, requestedView === "preview" ? "preview" : "review", offset);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(requestedView).loaded}`);
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
  refreshQueueCounts();
  if (payload.last_fetch) {
    renderStatus({ fetch: { running: false }, last_fetch: payload.last_fetch, settings: {} });
  }
  if (payload.errors.length) {
    setStatus(`Fetched ${payload.fetched}; errors: ${payload.errors.join(" | ")}`, true);
  } else if (payload.cursor_incomplete_queries && payload.cursor_incomplete_queries.length) {
    setStatus(
      `Fetched ${payload.fetched}; stored ${payload.stored}; catch-up limit reached for ${payload.cursor_incomplete_queries.join(", ")}`,
      true,
    );
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

async function refreshGalleryMetadata(galleryUrl) {
  if (pendingGalleryRefreshUrls.has(galleryUrl)) {
    return;
  }
  pendingGalleryRefreshUrls.add(galleryUrl);
  setGalleryFeedbackButtonsDisabled(galleryUrl, true);
  setStatus("Refreshing gallery metadata");
  try {
    const payload = await api("/api/gallery/refresh", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
      }),
    });
    const replaced = payload.item ? replaceRenderedGallery(payload.item) : false;
    if (!replaced) {
      await loadCurrentPage();
    }
    const parentText = payload.parent_url ? "parent found" : "no parent found";
    setStatus(`Refreshed gallery metadata (${parentText}, ${payload.sample_count || 0} samples)`);
  } finally {
    pendingGalleryRefreshUrls.delete(galleryUrl);
    setGalleryFeedbackButtonsDisabled(galleryUrl, false);
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
        reason_code: voteValue < 0 ? optionalNegativeReason(galleryUrl) : null,
        surface: currentView,
        view: currentView,
        include_rated: false,
        enrich_feedback: true,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload, galleryUrl);
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
        reason_code: scoreValue < 3 ? optionalNegativeReason(galleryUrl) : null,
        surface: currentView,
        view: currentView,
        include_rated: false,
        enrich_feedback: true,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload, galleryUrl);
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
        surface: currentView,
        view: currentView,
        include_rated: false,
        enrich_feedback: false,
        require_bootstrap_match: reviewRequireBootstrapMatchEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    await applyFeedbackResult(payload, galleryUrl);
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
    await applyFeedbackResult(payload, galleryUrl);
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
    await applyFeedbackResult(payload, galleryUrl);
    setStatus(markStatusMessage(payload.removed ? "Bookmark cleared" : "No bookmark to clear", payload));
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

async function applyFeedbackResult(payload, galleryUrl) {
  if (currentView === "history") {
    await loadReactionHistory();
  } else if (Array.isArray(payload.items)) {
    applyGalleryPage(payload);
  } else {
    const removedUrl = payload.removed_gallery_url || galleryUrl;
    renderedGalleryItems = renderedGalleryItems.filter((item) => item && item.url !== removedUrl);
    renderedGalleryUrls = renderedGalleryUrls.filter((url) => url !== removedUrl);
    nextRecommendationOffset = Math.max(0, nextRecommendationOffset - 1);
    renderGalleryCards(renderedGalleryItems);
  }
  refreshQueueCounts();
}

async function copyContinuingFeedback(galleryUrl) {
  await withPendingFeedback(galleryUrl, async () => {
    setStatus("Copying previous series rating");
    const payload = await api("/api/feedback/copy-continuing", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyGalleryPage(payload);
    refreshQueueCounts();
    setStatus(`Copied ${feedbackLabel(payload.copied_from)} from previous ${payload.copied_from.is_parent ? "parent" : "series version"}`);
  });
}

async function classifyGallery(galleryUrl, classification) {
  await withPendingFeedback(galleryUrl, async () => {
    const label = classification === "updates" ? "Updates" : classification === "review" ? "Review" : "Auto";
    setStatus(`Classifying gallery as ${label}`);
    const payload = await api("/api/classification", {
      method: "POST",
      body: JSON.stringify({
        gallery_url: galleryUrl,
        classification,
        filter_text: localFilterEl.value.trim(),
        interest_band: currentView === "low-interest" ? "low" : "primary",
      }),
    });
    applyGalleryPage(currentView === "continuing-updates" ? payload.updates : payload.review);
    refreshQueueCounts();
    setStatus(`Classification saved as ${label}`);
  });
}

function feedbackLabel(feedback) {
  if (feedback && Number.isFinite(Number(feedback.user_score))) {
    return `score ${Number(feedback.user_score)}`;
  }
  const vote = Number(feedback && feedback.user_vote);
  return vote > 0 ? "upvote" : vote < 0 ? "downvote" : "rating";
}

function optionalNegativeReason(galleryUrl) {
  const select = recommendationsEl.querySelector(`select[data-reason-for="${CSS.escape(galleryUrl)}"]`);
  return select && select.value ? select.value : null;
}

async function loadDiscovery(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const languageFilter = languageFilterEl.value.trim();
  const payload = await api(
    `/api/discovery?language_filter=${encodeURIComponent(languageFilter)}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  recordPageImpressions(payload, "discovery", offset);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

function recordPageImpressions(payload, surface, offset = 0) {
  if (!payload.request_id || !Array.isArray(payload.items) || !payload.items.length) {
    return;
  }
  api("/api/impressions", {
    method: "POST",
    body: JSON.stringify({
      request_id: payload.request_id,
      surface,
      items: payload.items.map((item, index) => ({
        gallery_url: item.url,
        position: offset + index,
        model_version: item.model_version,
        like_probability: item.like_probability,
      })),
    }),
  }).catch(() => null);
}

async function loadContinuingUpdates(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/continuing-updates?limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

function renderClassifierSummary(payload) {
  const counts = payload.counts || {};
  const classifier = payload.classifier || {};
  const status = classifier.accepted
    ? "Active"
    : classifier.ready
      ? "Shadow"
      : "Waiting for labels";
  classifierSummaryStatusEl.textContent = `${status} · ${Number(counts.pending || 0)} pending`;
  const trainingReviews = Number(classifier.review_count || 0);
  const trainingUpdates = Number(classifier.updates_count || 0);
  const holdoutTotal = Number(counts.random_labeled || 0);
  const holdoutReviews = Number(counts.random_review || 0);
  const holdoutUpdates = Number(counts.random_updates || 0);
  classifierSummaryCountsEl.textContent = `Training: ${trainingReviews} Review / ${trainingUpdates} Updates · Random holdout: ${holdoutTotal}/20 (${holdoutReviews} Review / ${holdoutUpdates} Updates)`;
}

async function loadClassificationSamples(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/classification-samples?limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyGalleryPage(payload, append);
  renderClassifierSummary(payload);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} ${viewCopy(currentView).loaded}`);
}

async function labelClassificationSample(sampleId, classification) {
  const normalizedSampleId = Number(sampleId);
  if (!Number.isInteger(normalizedSampleId) || pendingClassificationSampleIds.has(normalizedSampleId)) {
    return;
  }
  pendingClassificationSampleIds.add(normalizedSampleId);
  for (const button of recommendationsEl.querySelectorAll("button[data-sample-id]")) {
    if (Number(button.dataset.sampleId) === normalizedSampleId) {
      button.disabled = true;
    }
  }
  const label = classification === "updates" ? "Updates" : "Review";
  setStatus(`Saving sample #${normalizedSampleId} as ${label}`);
  try {
    const payload = await api("/api/classification-samples/label", {
      method: "POST",
      body: JSON.stringify({
        sample_id: normalizedSampleId,
        classification,
        limit: recommendationLimit,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyGalleryPage(payload.page);
    renderClassifierSummary(payload.page);
    refreshQueueCounts();
    setStatus(`Sample #${normalizedSampleId} labeled ${label}; ${Number(payload.page.counts?.pending || 0)} remaining`);
  } finally {
    pendingClassificationSampleIds.delete(normalizedSampleId);
    for (const button of recommendationsEl.querySelectorAll("button[data-sample-id]")) {
      if (Number(button.dataset.sampleId) === normalizedSampleId) {
        button.disabled = false;
      }
    }
  }
}

function deferClassificationSample(sampleId) {
  const index = renderedGalleryItems.findIndex(
    (item) => Number(item.classification_sample_id) === Number(sampleId)
  );
  if (index < 0) {
    return;
  }
  const [item] = renderedGalleryItems.splice(index, 1);
  renderedGalleryItems.push(item);
  renderGalleryCards(renderedGalleryItems);
  setStatus(`Sample #${sampleId} moved to the end of this page`);
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
  refreshQueueCounts();
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
  renderQueueCounts({ review: 0, low_interest: 0, continuing_updates: 0, short_repeats: 0, classification_samples: 0 });
  await loadStatus();
  refreshQueueCounts();
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
  refreshQueueCounts();
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
      if (entry.omitted) {
        return `<span class="chain-omitted">${Number(entry.omitted)} older ${Number(entry.omitted) === 1 ? "version" : "versions"} omitted</span>`;
      }
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
  const classifierMode = mode === "classifier";
  const lowInterestMode = mode === "low-interest";
  if (!append) {
    renderedGalleryUrls = [];
    renderedGalleryItems = [];
  }
  for (const item of items) {
    if (item && item.url) {
      renderedGalleryUrls.push(item.url);
      renderedGalleryItems.push(item);
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
    card.className = lowInterestMode ? "card low-interest-card" : "card";
    const thumbContent = item.thumb_url
      ? `<img src="${escapeAttr(thumbnailSrc(item))}" alt="" loading="lazy">`
      : `<span>No thumbnail</span>`;
    const thumb = item.thumb_url && (mode === "review" || lowInterestMode || classifierMode)
      ? `<a class="thumb" href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer" aria-label="Open ${escapeAttr(item.title)}">${thumbContent}</a>`
      : `<div class="thumb">${thumbContent}</div>`;
    const samples = item.samples || [];
    const samplesPreview = samples.length && !lowInterestMode
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
    const continuingSeries = item.continuing_series || null;
    const continuingMeta = continuingSeries
      ? `<div class="meta">Continuing series · ${Number(continuingSeries.version_count || 1)} stored versions · ${escapeHtml(continuingSeries.reason || "cumulative updates")}</div>`
      : "";
    const previousFeedback = item.previous_feedback || null;
    const copyPreviousFeedback = currentView === "continuing-updates" && previousFeedback && !hasFeedback
      ? `<div class="copy-feedback">
          <span>Previous ${previousFeedback.is_parent ? "parent" : "series version"}: ${escapeHtml(previousFeedback.title || previousFeedback.url)} · ${escapeHtml(feedbackLabel(previousFeedback))}</span>
          <button class="up" type="button" data-copy-continuing="1" data-url="${escapeAttr(item.url)}" title="Apply this previous rating to the current update. You can still rate it manually instead.">Copy ${escapeHtml(feedbackLabel(previousFeedback))}</button>
        </div>`
      : "";
    const classificationOverride = item.classification_override || null;
    const classificationPrediction = item.classification_prediction || null;
    const classificationMeta = classificationOverride
      ? `<div class="meta">Manual classification: ${escapeHtml(classificationOverride === "updates" ? "Updates" : "Review")}</div>`
      : classificationPrediction
        ? `<div class="meta">Learned classification: ${escapeHtml(classificationPrediction.classification)} · ${Math.round(Number(classificationPrediction.confidence || 0) * 100)}% confidence</div>`
        : "";
    const classifierSampleKind = item.sampling_strategy === "random"
      ? "Random evaluation holdout"
      : "Active learning sample";
    const classifierSampleMeta = classifierMode
      ? `<div class="meta classifier-sample-kind">Sample #${Number(item.classification_sample_id)} · ${escapeHtml(classifierSampleKind)}</div>`
      : "";
    const classificationActions = classifierMode
      ? `<div class="card-actions classifier-actions">
          <button class="classifier-review" type="button" data-sample-classification="review" data-sample-id="${Number(item.classification_sample_id)}" title="Label this as a normal or one-off gallery that belongs in Review.">Review</button>
          <button class="classifier-updates" type="button" data-sample-classification="updates" data-sample-id="${Number(item.classification_sample_id)}" title="Label this as a cumulative or ongoing gallery that belongs in Updates.">Updates</button>
          <button class="clear" type="button" data-sample-defer="1" data-sample-id="${Number(item.classification_sample_id)}" title="Move this sample to the end of the current page without labeling it.">Later</button>
        </div>`
      : currentView === "review" || currentView === "low-interest"
      ? `<div class="card-actions"><button class="clear" type="button" data-classification="updates" data-url="${escapeAttr(item.url)}" title="This gallery is a continuing update and should be shown in Updates.">Mark as Updates</button>${classificationOverride ? `<button class="clear" type="button" data-classification="auto" data-url="${escapeAttr(item.url)}">Use Auto</button>` : ""}</div>`
      : currentView === "continuing-updates"
        ? `<div class="card-actions"><button class="clear" type="button" data-classification="review" data-url="${escapeAttr(item.url)}" title="This is a normal gallery and should be shown in Review.">Move to Review</button>${classificationOverride ? `<button class="clear" type="button" data-classification="auto" data-url="${escapeAttr(item.url)}">Use Auto</button>` : ""}</div>`
        : "";
    const reactionAt = item.feedback_created_at ? `Reacted ${item.feedback_created_at}` : "";
    const markAt = item.mark_updated_at ? `Bookmarked ${item.mark_updated_at}` : "";
    const clearButton = hasFeedback && mode !== "preview"
      ? `<button class="clear" type="button" data-clear="1" data-url="${escapeAttr(item.url)}" title="Remove your rating, vote, or skip for this gallery.">Clear</button>`
      : "";
    const historyButton = hasFeedback && mode !== "preview"
      ? `<button class="clear" type="button" data-history="1" data-url="${escapeAttr(item.url)}" title="Show your feedback history for this gallery.">History</button>`
      : "";
    const refreshMetadataButton = `<button class="clear" type="button" data-refresh-gallery="1" data-url="${escapeAttr(item.url)}" title="Fetch this gallery detail page and refresh thumbnail, samples, and parent chain.">Refresh metadata</button>`;
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
    const compactFeedbackControls = `<div class="votes">
          <button class="down" type="button" data-vote="-1" data-url="${escapeAttr(item.url)}" title="Record a mild negative signal for this gallery.">Thumb down</button>
          <button class="skip" type="button" data-skip="1" data-url="${escapeAttr(item.url)}" title="Mark this gallery as reviewed with a neutral score so it leaves the queue.">Skip</button>
          <button class="up" type="button" data-vote="1" data-url="${escapeAttr(item.url)}" title="Record a mild positive signal and correct the low-interest ranking.">Thumb up</button>
        </div>`;
    const feedbackControls = mode === "preview" || classifierMode
      ? ""
      : lowInterestMode
        ? compactFeedbackControls
      : `<div class="votes">
          <button class="down" type="button" data-vote="-1" data-url="${escapeAttr(item.url)}" title="Record a mild negative signal for this gallery.">Thumb down</button>
          <button class="skip" type="button" data-skip="1" data-url="${escapeAttr(item.url)}" title="Mark this gallery as reviewed with a neutral score so it leaves the review queue.">Skip</button>
          <button class="up" type="button" data-vote="1" data-url="${escapeAttr(item.url)}" title="Record a mild positive signal for this gallery.">Thumb up</button>
        </div>
        <label class="feedback-reason">
          <span>Why not?</span>
          <select data-reason-for="${escapeAttr(item.url)}" aria-label="Optional negative feedback reason">
            <option value="">Optional reason</option>
            <option value="visual_style">Visual style</option>
            <option value="content_tags">Content or tags</option>
            <option value="creator_character">Creator or character</option>
            <option value="quality">Quality</option>
            <option value="gallery_too_small">Gallery too small</option>
            <option value="too_few_relevant_images">Too few relevant images</option>
            <option value="duplicate_update">Duplicate or update</option>
            <option value="other">Other</option>
          </select>
        </label>
        <div class="scorebar" aria-label="Score">
          ${[1, 2, 3, 4, 5]
            .map((value) => `<button type="button" data-score="${value}" data-url="${escapeAttr(item.url)}" title="${escapeAttr(scoreTooltip(value))}">${value}</button>`)
            .join("")}
        </div>
        ${markActions}
        <div class="card-actions">${refreshMetadataButton}</div>
        ${feedbackActions}`;
    const pageCount = item.page_count ? ` · ${item.page_count} pages` : "";
    const scoreMeta = classifierMode
      ? `${escapeHtml(item.category || "Unknown")}${escapeHtml(pageCount)}`
      : `${escapeHtml(item.category || "Unknown")} · ${item.like_probability != null && Number.isFinite(Number(item.like_probability)) ? `match ${Math.round(Number(item.like_probability) * 100)}%` : `score ${item.score}`}${escapeHtml(pageCount)}`;
    const lowInterestBadge = lowInterestMode
      ? item.low_interest_reason === "learned-threshold"
        ? `<div class="interest-badge learned-threshold-badge">Very Low · model &le; ${Math.round(Number(item.low_interest_threshold || 0) * 100)}% · rank ${Number(item.interest_rank || 0)} of ${Number(item.interest_total || 0)}</div>`
        : `<div class="interest-badge">Bottom ${Number(item.low_interest_cutoff_percent || 0)}% · rank ${Number(item.interest_rank || 0)} of ${Number(item.interest_total || 0)}</div>`
      : "";
    card.innerHTML = `
      <div class="media-preview">
        ${thumb}
        ${samplesPreview}
      </div>
      <div class="body">
        ${lowInterestBadge}
        <a class="title" href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
        <div class="meta">${scoreMeta}</div>
        ${classifierSampleMeta}
        ${Number.isFinite(Number(item.uncertainty)) ? `<div class="meta">Confidence ${Math.round(Number(item.confidence || 0) * 100)}% · uncertainty ${Number(item.uncertainty).toFixed(3)}</div>` : ""}
        <div class="meta">${escapeHtml([uploader, postedAt].filter(Boolean).join(" · "))}</div>
        <div class="meta">${escapeHtml(detailStatus)}</div>
        ${continuingMeta}
        ${copyPreviousFeedback}
        ${classificationMeta}
        <div class="meta">${escapeHtml(userFeedback)}</div>
        ${markStatus ? `<div class="meta">${escapeHtml(markStatus)}</div>` : ""}
        ${reactionAt ? `<div class="meta">${escapeHtml(reactionAt)}</div>` : ""}
        ${markAt ? `<div class="meta">${escapeHtml(markAt)}</div>` : ""}
        ${lowInterestMode ? "" : parentChain}
        ${lowInterestMode ? "" : `<div class="pillrow">${tags}</div>`}
        <div class="reason">${reasons}</div>
        ${lowInterestMode ? "" : relatedFeedback}
        ${classificationActions}
        ${feedbackControls}
      </div>
    `;
    recommendationsEl.appendChild(card);
    if (["review", "low-interest", "discovery", "short-repeats"].includes(mode)) {
      queueVisualEmbedding(item);
    }
  }
}

function replaceRenderedGallery(item) {
  if (!item || !item.url) {
    return false;
  }
  const index = renderedGalleryItems.findIndex((entry) => entry && entry.url === item.url);
  if (index < 0) {
    return false;
  }
  renderedGalleryItems[index] = {
    ...renderedGalleryItems[index],
    ...item,
  };
  renderGalleryCards(renderedGalleryItems);
  return true;
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
  const retrain = update.model_retrain || {};
  if (retrain.running) {
    details.push("model training in background");
  } else if (Number(retrain.pending_count || 0) > 0) {
    details.push(`${Number(retrain.pending_count)} pending model update${Number(retrain.pending_count) === 1 ? "" : "s"}`);
  }
  const enrichment = payload.feedback_enrichment || {};
  if (enrichment.status === "success") {
    details.push("full metadata learned");
  } else if (enrichment.status === "queued") {
    details.push("detail queued");
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
  const retrain = update.model_retrain || {};
  if (retrain.running) {
    details.push("model training in background");
  } else if (Number(retrain.pending_count || 0) > 0) {
    details.push(`${Number(retrain.pending_count)} pending model update${Number(retrain.pending_count) === 1 ? "" : "s"}`);
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
    if (
      button.dataset.vote ||
      button.dataset.score ||
      button.dataset.skip ||
      button.dataset.clear ||
      button.dataset.mark ||
      button.dataset.clearMark ||
      button.dataset.refreshGallery
    ) {
      button.disabled = disabled;
    }
  }
}

function applyGalleryPage(payload, append = false) {
  renderGalleryCards(payload.items || [], append);
  updateCurrentQueueCount(payload);
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
  refreshQueueCounts();
}

function updateLoadMore(total = null) {
  loadMoreBtn.disabled = !hasMoreRecommendations;
  loadMoreBtn.textContent = hasMoreRecommendations
    ? "Load More"
    : total && total > 0
      ? "No More"
      : "Load More";
}

function startParentProgressPolling() {
  if (parentProgressPollTimer) {
    return;
  }
  parentProgressPollTimer = setInterval(() => {
    loadStatus().catch(() => {});
  }, 1000);
}

function stopParentProgressPolling() {
  if (!parentProgressPollTimer) {
    return;
  }
  clearInterval(parentProgressPollTimer);
  parentProgressPollTimer = null;
}

function renderParentUpdateProgress(state) {
  const hasLogs = Boolean(state.logs && state.logs.length);
  const shouldShow = parentProgressVisible || state.running || hasLogs;
  parentProgressEl.classList.toggle("hidden", !shouldShow);
  backfillReviewParentsBtn.disabled = Boolean(state.running);
  backfillHistoryParentsBtn.disabled = Boolean(state.running);
  if (!shouldShow) {
    return;
  }
  parentProgressVisible = true;
  if (state.running) {
    startParentProgressPolling();
  } else {
    stopParentProgressPolling();
  }
  parentProgressTitleEl.textContent = state.running ? "Parent Update Running" : "Parent Update";
  parentProgressSummaryEl.textContent = parentProgressSummary(state);
  parentProgressCountsEl.textContent = parentProgressCounts(state);
  parentProgressBarEl.value = Math.round(parentProgressPercent(state));
  parentProgressLogEl.innerHTML = (state.logs || [])
    .slice(-30)
    .map((entry) => `<div>${escapeHtml(parentProgressLogLine(entry))}</div>`)
    .join("");
  parentProgressLogEl.scrollTop = parentProgressLogEl.scrollHeight;
}

function parentProgressSummary(state) {
  const stage = state.stage || "idle";
  const message = state.message || (state.running ? "Running" : "Idle");
  if (stage === "failed") {
    return `Failed: ${state.error || message}`;
  }
  if (state.updated_at) {
    return `${message} (${state.updated_at})`;
  }
  return message;
}

function parentProgressCounts(state) {
  const parts = [];
  if (Number.isFinite(state.checked) || Number.isFinite(state.total)) {
    parts.push(`${state.checked || 0}/${state.total || 0} candidates`);
  }
  if (Number.isFinite(state.detail_total) && state.detail_total > 0) {
    parts.push(`${state.detail_done || 0}/${state.detail_total} details`);
  }
  if (Number.isFinite(state.persisted) && Number.isFinite(state.total) && state.total > 0) {
    parts.push(`${state.persisted || 0}/${state.total} saved`);
  }
  if (Number.isFinite(state.updated)) {
    parts.push(`${state.updated || 0} rows`);
  }
  if (Number.isFinite(state.parent_updated)) {
    parts.push(`${state.parent_updated || 0} parents`);
  }
  if (Number.isFinite(state.parent_enriched)) {
    parts.push(`${state.parent_enriched || 0} ancestors`);
  }
  return parts.join(" | ");
}

function parentProgressPercent(state) {
  if (state.stage === "finished") {
    return 100;
  }
  if (state.stage === "failed") {
    return Math.max(5, parentProgressPercentFromCounts(state));
  }
  if (state.stage === "persisting" && Number.isFinite(state.total) && state.total > 0) {
    return 75 + (Math.min(state.persisted || 0, state.total) / state.total) * 15;
  }
  if (state.stage === "parent_chain" && Number.isFinite(state.parent_chain_total) && state.parent_chain_total > 0) {
    return 90 + (Math.min(state.parent_chain_done || 0, state.parent_chain_total) / state.parent_chain_total) * 10;
  }
  if (state.stage === "details" && Number.isFinite(state.detail_total)) {
    if (state.detail_total <= 0) {
      return 70;
    }
    return 35 + (Math.min(state.detail_done || 0, state.detail_total) / state.detail_total) * 40;
  }
  if (state.stage === "gdata") {
    return 25;
  }
  if (state.stage === "selected") {
    return 15;
  }
  if (state.stage === "selecting") {
    return 8;
  }
  return state.running ? 4 : parentProgressPercentFromCounts(state);
}

function parentProgressPercentFromCounts(state) {
  if (Number.isFinite(state.total) && state.total > 0 && Number.isFinite(state.persisted)) {
    return Math.min(100, (state.persisted / state.total) * 100);
  }
  return 0;
}

function parentProgressLogLine(entry) {
  const fields = entry.fields || {};
  const details = [];
  if (fields.current_gallery_title) {
    details.push(fields.current_gallery_title);
  } else if (fields.current_gallery_url) {
    details.push(fields.current_gallery_url);
  }
  if (fields.current_parent_url) {
    details.push(`parent ${fields.current_parent_url}`);
  }
  if (fields.error) {
    details.push(`error ${fields.error}`);
  }
  const suffix = details.length ? ` - ${details.join(" | ")}` : "";
  return `${entry.at || ""} ${entry.message || ""}${suffix}`.trim();
}

function renderStatus(payload) {
  const fetchState = payload.fetch || {};
  const parentUpdate = payload.parent_update || {};
  const last = payload.last_fetch;
  const rows = [
    ["State", fetchState.running ? fetchState.message || fetchState.stage || "Running" : "Idle"],
  ];
  if (parentUpdate.running || parentUpdate.updated_at) {
    rows.push(["Parents", parentUpdate.running ? parentUpdate.message || "Running" : parentUpdate.message || "Idle"]);
    if (parentUpdate.total || parentUpdate.checked) {
      rows.push(["Parent Candidates", `${parentUpdate.checked || 0}/${parentUpdate.total || 0}`]);
    }
    if (Number.isFinite(parentUpdate.detail_total)) {
      rows.push(["Parent Details", `${parentUpdate.detail_done || 0}/${parentUpdate.detail_total}`]);
    }
    if (Number.isFinite(parentUpdate.updated)) {
      rows.push(["Parent Updates", `${parentUpdate.updated || 0} rows, ${parentUpdate.parent_updated || 0} parents`]);
    }
    if (parentUpdate.errors && parentUpdate.errors.length) {
      rows.push(["Parent Errors", parentUpdate.errors.join(" | ")]);
    }
  }
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
      rows.push(["Pages", `start ${start || 0}, count ${count || 0}, ${fetchState.remaining_extra_pages || 0} catch-up left`]);
    }
    if (fetchState.cursor_active) {
      rows.push([
        "Cursor",
        fetchState.cursor_caught_up
          ? "caught up"
          : `${fetchState.cursor_matches || 0}/${fetchState.cursor_match_target || 0} anchors`,
      ]);
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
    rows.push([
      "Plan",
      plan.entries
        .map((entry) => `${entry.query || "recent"}${entry.cursor_caught_up === false ? " [catch-up pending]" : ""}`)
        .join(", "),
    ]);
    rows.push(["Scope", `${plan.pages} initial page(s), +${plan.stale_fetch_extra_pages || 0} cursor catch-up, ${plan.detail_fetch_limit} details`]);
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
  if (payload.model_retrain) {
    const training = payload.model_retrain;
    const modeLabels = { immediate: "Each review", batched: "Batch", manual: "Manual" };
    const state = training.running
      ? `Training (${training.trigger || "background"})`
      : `${Number(training.pending_count || 0)} pending`;
    rows.push(["Training", `${modeLabels[training.mode] || training.mode}: ${state}`]);
    if (training.next_check_at) {
      rows.push(["Next Train", training.next_check_at]);
    }
    if (training.last_completed_at) {
      rows.push(["Last Train", training.last_completed_at]);
    }
    if (training.last_error) {
      rows.push(["Train Error", training.last_error]);
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
modelRetrainModeEl.addEventListener("change", updateModelRetrainControls);
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
hathRefreshBtn.addEventListener("click", () => loadHathStatus().catch((error) => setStatus(error.message, true)));
hathPackBtn.addEventListener("click", () => startHathPack().catch((error) => {
  setStatus(error.message, true);
  updateHathPackControls();
  loadHathPackStatus().catch(() => {});
}));
hathPackPreviewBtn.addEventListener("click", () => previewHathPack().catch((error) => {
  setStatus(error.message, true);
  updateHathPackControls();
}));
hathPackRefreshBtn.addEventListener("click", () => loadHathPackInventory().catch((error) => setStatus(error.message, true)));
hathTrashPreviewBtn.addEventListener("click", () => previewHathTrash().catch((error) => {
  setStatus(error.message, true);
  updateHathTrashControls();
}));
hathTrashDeleteBtn.addEventListener("click", () => startHathTrashDelete().catch((error) => {
  setStatus(error.message, true);
  updateHathTrashControls();
  loadHathPackStatus().catch(() => {});
}));
hathTrashSelectAllBtn.addEventListener("click", () => {
  for (const input of hathTrashCandidatesEl.querySelectorAll("input[data-hath-trash-job]")) {
    input.checked = true;
  }
  invalidateHathTrashPreview();
  updateHathTrashSelection();
});
hathTrashSelectNoneBtn.addEventListener("click", () => {
  for (const input of hathTrashCandidatesEl.querySelectorAll("input[data-hath-trash-job]")) {
    input.checked = false;
  }
  invalidateHathTrashPreview();
  updateHathTrashSelection();
});
hathTrashCandidatesEl.addEventListener("change", (event) => {
  if (!event.target.matches("input[data-hath-trash-job]")) return;
  invalidateHathTrashPreview();
  updateHathTrashSelection();
});
hathPackSelectAllBtn.addEventListener("click", () => {
  for (const input of hathPackCandidatesEl.querySelectorAll("input[data-hath-pack-source]:not(:disabled)")) {
    input.checked = true;
  }
  invalidateHathPackPreview();
  updateHathPackSelection();
});
hathPackSelectNoneBtn.addEventListener("click", () => {
  for (const input of hathPackCandidatesEl.querySelectorAll("input[data-hath-pack-source]")) {
    input.checked = false;
  }
  invalidateHathPackPreview();
  updateHathPackSelection();
});
hathPackCandidatesEl.addEventListener("change", (event) => {
  if (!event.target.matches("input[data-hath-pack-source]")) return;
  invalidateHathPackPreview();
  updateHathPackSelection();
});
for (const element of [
  hathPackArchiveNameEl,
  hathPackMegaDestinationEl,
  hathPackUploadEl,
  hathPackCleanupModeEl,
  hathPackTrashSourcesEl,
  hathPackTrashArchiveEl,
]) {
  element.addEventListener("change", () => {
    if (!hathPackUploadEl.checked) {
      hathPackTrashArchiveEl.checked = false;
    }
    invalidateHathPackPreview();
    updateHathPackControls();
  });
}
for (const element of [hathPackArchiveNameEl, hathPackMegaDestinationEl]) {
  element.addEventListener("input", () => {
    invalidateHathPackPreview();
    updateHathPackControls();
  });
}
hathDownloadSearchEl.addEventListener("input", () => {
  if (hathStatusPayload) renderHathDownloadList(hathStatusPayload);
});
hathDownloadFilterEl.addEventListener("change", () => {
  if (hathStatusPayload) renderHathDownloadList(hathStatusPayload);
});
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
  const sampleClassificationButton = event.target.closest("button[data-sample-classification]");
  if (sampleClassificationButton) {
    labelClassificationSample(
      sampleClassificationButton.dataset.sampleId,
      sampleClassificationButton.dataset.sampleClassification
    ).catch((error) => setStatus(error.message, true));
    return;
  }
  const sampleDeferButton = event.target.closest("button[data-sample-defer]");
  if (sampleDeferButton) {
    deferClassificationSample(sampleDeferButton.dataset.sampleId);
    return;
  }
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
  const copyContinuingButton = event.target.closest("button[data-copy-continuing]");
  if (copyContinuingButton) {
    copyContinuingFeedback(copyContinuingButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
  const classificationButton = event.target.closest("button[data-classification]");
  if (classificationButton) {
    classifyGallery(classificationButton.dataset.url, classificationButton.dataset.classification).catch((error) => setStatus(error.message, true));
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
  const refreshGalleryButton = event.target.closest("button[data-refresh-gallery]");
  if (refreshGalleryButton) {
    refreshGalleryMetadata(refreshGalleryButton.dataset.url).catch((error) => setStatus(error.message, true));
    return;
  }
});

applyStaticTooltips();

loadSettings()
  .then(loadStatus)
  .then(async () => {
    setActiveView(currentView);
    const counts = loadQueueCounts();
    const hathStatus = loadHathStatus({ announce: false }).catch(() => null);
    await loadCurrentPage();
    await Promise.all([counts, hathStatus]);
  })
  .catch((error) => setStatus(error.message, true));

setInterval(() => {
  loadStatus().catch(() => {});
  if (currentView === "hath") {
    loadHathStatus({ announce: false }).catch(() => {});
  }
}, 10000);
