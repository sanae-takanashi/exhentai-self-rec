const statusEl = document.querySelector("#status");
const cookieEl = document.querySelector("#cookie");
const cookiePreviewEl = document.querySelector("#cookiePreview");
const tagsEl = document.querySelector("#tags");
const pagesEl = document.querySelector("#pages");
const detailLimitEl = document.querySelector("#detailLimit");
const learnedLimitEl = document.querySelector("#learnedLimit");
const candidateLimitEl = document.querySelector("#candidateLimit");
const minutesEl = document.querySelector("#minutes");
const autoRefreshEl = document.querySelector("#autoRefresh");
const recommendationsEl = document.querySelector("#recommendations");
const queryEl = document.querySelector("#query");
const localFilterEl = document.querySelector("#localFilter");
const includeRatedEl = document.querySelector("#includeRated");
const importFileEl = document.querySelector("#importFile");
const replaceImportEl = document.querySelector("#replaceImport");
const loadMoreBtn = document.querySelector("#loadMoreBtn");
const modelDialog = document.querySelector("#modelDialog");
const dialogTitle = document.querySelector("#dialogTitle");
const modelBody = document.querySelector("#modelBody");
const refreshStatusEl = document.querySelector("#refreshStatus");
let nextRecommendationOffset = 0;
let hasMoreRecommendations = false;
let lastRenderedFetchId = null;
const recommendationLimit = 40;
const pendingFeedbackUrls = new Set();

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

function bootstrapText(tags) {
  return tags.map((item) => `${item.tag}:${item.weight}`).join("\n");
}

async function loadSettings() {
  const settings = await api("/api/settings");
  cookiePreviewEl.textContent = settings.has_cookie
    ? `Stored cookie keys: ${settings.cookie_preview}`
    : "No cookie stored.";
  if (settings.last_access_check) {
    cookiePreviewEl.textContent += ` Last check: ${settings.last_access_check.message}`;
  }
  tagsEl.value = bootstrapText(settings.bootstrap_tags);
  pagesEl.value = settings.fetch_pages;
  detailLimitEl.value = settings.detail_fetch_limit;
  learnedLimitEl.value = settings.learned_query_limit;
  candidateLimitEl.value = settings.recommend_candidate_limit;
  minutesEl.value = settings.refresh_interval_minutes;
  autoRefreshEl.checked = settings.auto_refresh;
}

async function loadStatus() {
  const payload = await api("/api/status");
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
    refresh_interval_minutes: Number(minutesEl.value),
    auto_refresh: autoRefreshEl.checked,
  };
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  cookieEl.value = "";
  cookiePreviewEl.textContent = settings.has_cookie
    ? `Stored cookie keys: ${settings.cookie_preview}`
    : "No cookie stored.";
  await loadStatus();
  setStatus("Settings saved");
}

async function loadRecommendations(offset = 0, append = false) {
  const localFilter = localFilterEl.value.trim();
  const payload = await api(
    `/api/recommendations?include_rated=${includeRatedEl.checked ? "1" : "0"}&limit=${recommendationLimit}&offset=${offset}&filter=${encodeURIComponent(localFilter)}`
  );
  applyRecommendationPage(payload, append);
  setStatus(`${append ? nextRecommendationOffset : payload.items.length} of ${payload.total} recommendations loaded`);
}

async function fetchNew(query = "") {
  setStatus("Fetching ExHentai pages");
  const payload = await api("/api/fetch", {
    method: "POST",
    body: JSON.stringify({
      query,
      include_rated: includeRatedEl.checked,
      filter_text: localFilterEl.value.trim(),
    }),
  });
  applyRecommendationPage(payload);
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
      include_rated: includeRatedEl.checked,
      filter_text: localFilterEl.value.trim(),
      limit: Number(detailLimitEl.value),
    }),
  });
  applyRecommendationPage(payload);
  if (payload.last_fetch) {
    renderStatus({ fetch: { running: false }, last_fetch: payload.last_fetch, settings: {} });
  }
  if (payload.errors.length) {
    setStatus(`Enriched ${payload.enriched}; errors: ${payload.errors.join(" | ")}`, true);
  } else {
    setStatus(`Enriched ${payload.enriched} recommended galleries`);
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
        include_rated: includeRatedEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyRecommendationPage(payload);
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
        include_rated: includeRatedEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyRecommendationPage(payload);
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
        include_rated: includeRatedEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyRecommendationPage(payload);
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
        include_rated: includeRatedEl.checked,
        filter_text: localFilterEl.value.trim(),
      }),
    });
    applyRecommendationPage(payload);
    setStatus(payload.removed ? "Rating cleared" : "No rating to clear");
  });
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
    body: JSON.stringify({ include_rated: includeRatedEl.checked, filter_text: localFilterEl.value.trim() }),
  });
  applyRecommendationPage(payload);
  modelBody.textContent = JSON.stringify(payload.model, null, 2);
  setStatus("Model retrained");
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
  await loadRecommendations();
  modelBody.textContent = JSON.stringify(payload.model, null, 2);
  setStatus(
    `Imported ${payload.imported.feedback} feedback, ${payload.imported.bootstrap_tags} bootstrap tags`
  );
}

function renderRecommendations(items, append = false) {
  if (!append && !items.length) {
    recommendationsEl.innerHTML = `<div class="hint">No galleries yet. Save cookies and bootstrap tags, then fetch.</div>`;
    return;
  }
  if (!append) {
    recommendationsEl.innerHTML = "";
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "card";
    const thumb = item.thumb_url
      ? `<img src="${escapeAttr(item.thumb_url)}" alt="">`
      : `<span>No thumbnail</span>`;
    const tags = (item.tags || []).slice(0, 8).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("");
    const reasons = (item.reasons || []).map((reason) => `<span class="reason">${escapeHtml(reason)}</span>`).join(" ");
    const userFeedback = item.user_score
      ? `Your score ${item.user_score}`
      : `Your signal ${item.user_vote || 0}`;
    const detailStatus = item.detail_fetched_at ? "Full metadata" : "List metadata";
    const uploader = item.uploader ? `Uploader ${item.uploader}` : "Uploader unknown";
    const postedAt = item.posted_at ? `Posted ${item.posted_at}` : "";
    const clearButton = item.rated
      ? `<button class="clear" type="button" data-clear="1" data-url="${escapeAttr(item.url)}">Clear</button>`
      : "";
    const historyButton = item.rated
      ? `<button class="clear" type="button" data-history="1" data-url="${escapeAttr(item.url)}">History</button>`
      : "";
    const feedbackActions = item.rated ? `<div class="card-actions">${historyButton}${clearButton}</div>` : "";
    card.innerHTML = `
      <div class="thumb">${thumb}</div>
      <div class="body">
        <a class="title" href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
        <div class="meta">${escapeHtml(item.category || "Unknown")} · score ${item.score}</div>
        <div class="meta">${escapeHtml([uploader, postedAt].filter(Boolean).join(" · "))}</div>
        <div class="meta">${escapeHtml(detailStatus)}</div>
        <div class="meta">${escapeHtml(userFeedback)}</div>
        <div class="pillrow">${tags}</div>
        <div class="reason">${reasons}</div>
        <div class="votes">
          <button class="up" type="button" data-vote="1" data-url="${escapeAttr(item.url)}">Thumb up</button>
          <button class="skip" type="button" data-skip="1" data-url="${escapeAttr(item.url)}">Skip</button>
          <button class="down" type="button" data-vote="-1" data-url="${escapeAttr(item.url)}">Thumb down</button>
        </div>
        <div class="scorebar" aria-label="Score">
          ${[1, 2, 3, 4, 5]
            .map((value) => `<button type="button" data-score="${value}" data-url="${escapeAttr(item.url)}">${value}</button>`)
            .join("")}
        </div>
        ${feedbackActions}
      </div>
    `;
    recommendationsEl.appendChild(card);
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

function applyRecommendationPage(payload, append = false) {
  renderRecommendations(payload.items || [], append);
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
  await loadRecommendations();
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
document.querySelector("#checkBtn").addEventListener("click", () => checkLogin().catch((error) => setStatus(error.message, true)));
document.querySelector("#clearCookieBtn").addEventListener("click", () => clearCookie().catch((error) => setStatus(error.message, true)));
document.querySelector("#searchFetchBtn").addEventListener("click", () => fetchNew(queryEl.value).catch((error) => setStatus(error.message, true)));
queryEl.addEventListener("change", () => previewPlan().catch((error) => setStatus(error.message, true)));
document.querySelector("#modelBtn").addEventListener("click", () => showModel().catch((error) => setStatus(error.message, true)));
document.querySelector("#retrainBtn").addEventListener("click", () => retrain().catch((error) => setStatus(error.message, true)));
document.querySelector("#exportBtn").addEventListener("click", () => exportPreferences().catch((error) => setStatus(error.message, true)));
document.querySelector("#importBtn").addEventListener("click", () => importFileEl.click());
loadMoreBtn.addEventListener("click", () => {
  if (!hasMoreRecommendations) return;
  loadRecommendations(nextRecommendationOffset, true).catch((error) => setStatus(error.message, true));
});
importFileEl.addEventListener("change", () => {
  importPreferences(importFileEl.files[0]).catch((error) => setStatus(error.message, true));
  importFileEl.value = "";
});
includeRatedEl.addEventListener("change", () => loadRecommendations().catch((error) => setStatus(error.message, true)));
localFilterEl.addEventListener("change", () => loadRecommendations().catch((error) => setStatus(error.message, true)));
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
  }
});

loadSettings()
  .then(loadStatus)
  .then(loadRecommendations)
  .catch((error) => setStatus(error.message, true));

setInterval(() => {
  loadStatus().catch(() => {});
}, 10000);
