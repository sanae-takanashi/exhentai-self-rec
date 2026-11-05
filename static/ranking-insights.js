"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    element.textContent = String(value);
    if (className) element.className = className;
    return element;
  };
  const number = (value) => Number(value).toLocaleString();
  const ratio = (value, scale = 1) => value == null ? "No denominator" : `${(value * scale).toFixed(1)}%`;
  let request = 0;
  function metric(label, value) {
    const node = text("div", "", "metric");
    node.append(text("strong", number(value)), text("span", label));
    return node;
  }
  function card(cohort) {
    const node = text("article", "", "cohort");
    node.append(text("h3", `${cohort.surface} · ${cohort.policy}`));
    node.append(text("p", `Version: ${cohort.model_version}\nArtifact: ${cohort.artifact_id}\nTrained: ${cohort.trained_at}`, "identity"));
    const values = [
      ["Delivered galleries / events", `${number(cohort.delivered_galleries)} / ${number(cohort.delivered_events)}`],
      ["Visible galleries / events", `${number(cohort.visible_galleries)} / ${number(cohort.visible_events)}`],
      ["Positive / negative / neutral", `${number(cohort.positive)} / ${number(cohort.negative)} / ${number(cohort.neutral)}`],
      ["Unknown preference galleries", number(cohort.unknown_preference_galleries)],
      ["Duplicate/update exclusions", number(cohort.excluded_duplicate_update)],
      ["Invalid linked outcomes", number(cohort.invalid_outcome)],
      ["Positive fraction of explicit outcomes", ratio(cohort.positive_fraction_of_explicit, 100)],
      ["Observed positives per 100 visible", cohort.observed_positive_per_100_visible == null ? "No denominator" : cohort.observed_positive_per_100_visible.toFixed(1)],
      ["Fallback events", number(cohort.fallback_events)],
      ["Events without visibility instrumentation", number(cohort.visibility_uninstrumented_events)],
    ];
    const list = document.createElement("dl");
    for (const [label, value] of values) list.append(text("dt", label), text("dd", value));
    node.append(list);
    const reasons = Object.entries(cohort.negative_reasons).map(([reason, count]) => `${reason}: ${number(count)}`);
    node.append(text("p", `Negative reasons: ${reasons.join(" · ") || "None recorded"}`));
    return node;
  }
  async function refresh(event) {
    event?.preventDefault();
    const ownRequest = ++request;
    const button = byId("filters").querySelector("button");
    button.disabled = true;
    byId("status").textContent = "Loading observations…";
    byId("report").hidden = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const query = new URLSearchParams({ days: byId("days").value, surface: byId("surface").value });
      const response = await fetch(`/api/insights/ranking?${query}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`);
      const report = await response.json();
      if (ownRequest !== request) return;
      if (report.schema !== "ranking-insights-v1" || !Array.isArray(report.cohorts)) throw new Error("Unexpected report format.");
      byId("summary").replaceChildren(
        metric("Distinct delivered galleries", report.summary.delivered_galleries),
        metric("Distinct visible galleries", report.summary.visible_galleries),
        metric("Latest feedback galleries in window", report.summary.latest_feedback_galleries),
        metric("Feedback galleries without exact attribution", report.summary.unknown_attribution_galleries));
      byId("window").textContent = `${report.window.start_utc} ≤ delivery / latest feedback < ${report.window.end_utc} UTC · ${report.window.surface}`;
      const reasons = Object.entries(report.summary.unknown_attribution_reasons).map(([reason, count]) => `${reason}: ${number(count)}`);
      byId("attribution").textContent = `Unknown attribution: ${reasons.join(" · ") || "None in this window"}. Unlinked feedback is never assigned by timing.`;
      const milestone = report.review_milestone;
      byId("milestone").textContent = `${number(milestone.window_attributed_explicit_galleries)} / ${number(milestone.target)} exactly attributed explicit Review galleries in this window. ${milestone.review_due ? "Review reminder reached." : "Review reminder not reached."} This is not a count since the last assessment and never activates a model.`;
      byId("cohorts").replaceChildren(...report.cohorts.map(card));
      if (!report.cohorts.length) byId("cohorts").append(text("p", "No delivery cohorts in this window. Older data may have no visibility or serving identity recorded."));
      byId("definitions").replaceChildren(...report.definitions.map((definition) => text("li", definition)));
      byId("report").hidden = false;
      byId("status").textContent = `Loaded ${report.cohorts.length} serving cohorts. Read-only observations.`;
    } catch (error) {
      if (ownRequest === request) byId("status").textContent = error.name === "AbortError"
        ? "The report timed out. Refresh to retry." : `Unable to load observations. ${error.message} Refresh to retry.`;
    } finally {
      clearTimeout(timeout);
      if (ownRequest === request) button.disabled = false;
    }
  }
  byId("filters").addEventListener("submit", refresh);
  refresh();
})();
