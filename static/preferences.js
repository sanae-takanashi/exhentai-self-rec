"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  let report = null;
  let preview = null;
  let restore = null;
  let busy = false;
  let generation = 0;
  const fields = ["preferenceSelect", "preferenceTag", "preferenceAction", "preferenceWeight"];

  function status(message, error = false) {
    el("preferencesStatus").textContent = message;
    el("preferencesStatus").classList.toggle("error", error);
  }
  function controls() {
    for (const id of fields) el(id).disabled = busy;
    el("preferenceWeight").disabled = busy || el("preferenceAction").value === "remove";
    el("previewPreferenceBtn").disabled = busy || !report;
    el("applyPreferenceBtn").disabled = busy || !preview;
    el("restorePreferenceBtn").disabled = busy || !restore;
    el("preferenceForm").setAttribute("aria-busy", String(busy));
  }
  function invalidate() {
    generation += 1;
    preview = null;
    el("preferencePreview").replaceChildren();
    controls();
  }
  async function api(path, payload) {
    const options = payload === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    };
    const response = await fetch(path, { ...options, cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.error === "string" ? data.error : "Request failed.");
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function cellRow(values) {
    const row = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    return row;
  }
  function renderReport(data) {
    report = data;
    el("explicitPreferences").replaceChildren(...data.preferences.map((item) => cellRow([
      item.tag, item.weight, item.weight === 0 ? "Paused" : item.weight > 0 ? "Positive" : "Negative",
    ])));
    if (!data.preferences.length) el("explicitPreferences").append(cellRow(["No explicit preferences", "", ""]));
    const selected = el("preferenceSelect").value;
    const add = document.createElement("option");
    add.value = "";
    add.textContent = "Add preference";
    const options = data.preferences.map((item) => {
      const option = document.createElement("option");
      option.value = item.tag;
      option.textContent = item.tag;
      return option;
    });
    el("preferenceSelect").replaceChildren(add, ...options);
    el("preferenceSelect").value = data.preferences.some((item) => item.tag === selected) ? selected : "";
    el("learnedFeatures").replaceChildren(...data.learned_features.map((item) => cellRow([
      item.feature, item.weight, item.positive_count, item.negative_count,
    ])));
    if (!data.learned_features.length) el("learnedFeatures").append(cellRow(["No learned features", "", "", ""]));
    controls();
  }
  function proposal() {
    return { tag: el("preferenceTag").value, action: el("preferenceAction").value,
      weight: el("preferenceAction").value === "set" ? Number(el("preferenceWeight").value) : null };
  }
  function showPreview(data) {
    const container = el("preferencePreview");
    const heading = document.createElement("h3");
    heading.textContent = `Preview: ${data.proposal.tag}`;
    const change = document.createElement("p");
    change.textContent = `Weight: ${data.previous_weight === null ? "not present" : data.previous_weight} to ${data.proposal.action === "remove" ? "removed" : data.proposal.weight}. Legacy score contribution per metadata match: ${data.previous_legacy_matching_contribution ?? data.previous_weight ?? 0} to ${data.proposed_legacy_matching_contribution ?? data.proposal.weight ?? 0} (change ${data.legacy_matching_score_delta}).`;
    const counts = document.createElement("p");
    counts.textContent = `${data.counts.matched} stored metadata matches: ${data.counts.positive} positive, ${data.counts.negative} negative, ${data.counts.neutral} neutral, ${data.counts.unknown} unrated, ${data.counts.duplicate_update} duplicate/update.`;
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = data.note;
    const examples = document.createElement("ul");
    for (const item of data.examples) {
      const li = document.createElement("li");
      li.textContent = `${item.title} (${item.outcome})`;
      examples.append(li);
    }
    container.replaceChildren(heading, change, counts, note, examples);
  }
  el("preferenceSelect").addEventListener("change", () => {
    const item = report.preferences.find((entry) => entry.tag === el("preferenceSelect").value);
    el("preferenceTag").value = item ? item.tag : "";
    el("preferenceWeight").value = item ? item.weight : 1;
    el("preferenceAction").value = "set";
    invalidate();
  });
  for (const id of fields.slice(1)) {
    el(id).addEventListener("input", invalidate);
    el(id).addEventListener("change", invalidate);
  }
  el("preferenceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !report || !el("preferenceForm").reportValidity()) return;
    invalidate();
    const current = generation;
    busy = true;
    controls();
    status("Previewing stored metadata...");
    try {
      const data = await api("/api/preferences/preview", proposal());
      if (current !== generation) return;
      preview = data;
      showPreview(data);
      status("Preview ready. No preference has been changed.");
    } catch (error) { status(error.message, true); }
    finally { busy = false; controls(); }
  });
  el("applyPreferenceBtn").addEventListener("click", async () => {
    if (busy || !preview) return;
    const approved = preview;
    busy = true;
    controls();
    status("Applying previewed preference...");
    let applied = false;
    try {
      await api("/api/preferences/apply", { ...approved.proposal, preview_id: approved.preview_id });
      applied = true;
      invalidate();
      const data = await api("/api/preferences");
      renderReport(data);
      const saved = data.preferences.find((item) => item.tag === approved.proposal.tag);
      const verified = approved.proposal.action === "remove" ? !saved : saved && saved.weight === approved.proposal.weight;
      if (!verified) throw new Error("Readback differs from the applied proposal. Reload preferences before another change.");
      restore = { tag: approved.proposal.tag, action: approved.previous_weight === null ? "remove" : "set", weight: approved.previous_weight };
      status("Preference saved and independently read back.");
    } catch (error) {
      invalidate();
      restore = null;
      status(error.status === 409 ? "Preferences or evidence changed. Preview again before applying." :
        applied ? `Apply succeeded, but verification failed. Reload before another change. ${error.message}` :
          `Apply could not be confirmed. Reload preferences before retrying. ${error.message}`, true);
      try { renderReport(await api("/api/preferences")); } catch (_) { report = null; }
    } finally { busy = false; controls(); }
  });
  el("restorePreferenceBtn").addEventListener("click", () => {
    if (!restore || busy) return;
    el("preferenceTag").value = restore.tag;
    el("preferenceAction").value = restore.action;
    el("preferenceWeight").value = restore.weight ?? 0;
    el("preferenceSelect").value = report.preferences.some((item) => item.tag === restore.tag) ? restore.tag : "";
    invalidate();
    status("Previous weight selected. Preview it before applying.");
    el("previewPreferenceBtn").focus();
  });
  controls();
  api("/api/preferences").then((data) => { renderReport(data); status("Preferences loaded."); })
    .catch(() => { status("Could not load preferences. Reload to retry.", true); controls(); });
})();
