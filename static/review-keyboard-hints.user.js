// ==UserScript==
// @name         Review Keyboard Hints
// @namespace    exhentai-self-rec
// @version      1.2.0
// @description  Read-only keyboard reminders for the native gallery and Archiver.
// @match        https://exhentai.org/g/*
// @match        https://exhentai.org/archiver.php*
// @grant        none
// @run-at       document-idle
// @noframes
// ==/UserScript==

(() => {
  "use strict";
  const archive = location.pathname === "/archiver.php";
  if (location.protocol !== "https:" || location.hostname !== "exhentai.org" ||
      (!archive && !/^\/g\/\d+\/[a-z0-9]+\/?$/.test(location.pathname)) ||
      window.top !== window.self || document.getElementById("review-keyboard-hints")) return;

  const host = document.createElement("div");
  host.id = "review-keyboard-hints";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host { display: block; clear: both; }
      * { box-sizing: border-box; letter-spacing: 0; }
      .dock { display: flow-root; min-height: 42px; position: fixed; bottom: 8px; left: 8px; right: 8px; max-width: 560px; margin: auto; z-index: 2147483000; color: #edf2f0; background: #202624; border: 1px solid #62746c; border-radius: 4px; padding: 6px 8px; font: 12px/24px system-ui, sans-serif; text-align: left; }
      summary { float: right; display: grid; place-items: center; width: 28px; height: 28px; cursor: pointer; list-style: none; color: #93d8b5; }
      summary::-webkit-details-marker { display: none; }
      summary:focus-visible { outline: 2px solid #93d8b5; }
      svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
      .items { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-right: 36px; }
      .items > span { white-space: nowrap; }
      kbd { font: inherit; color: #fff; border: 1px solid #62746c; border-radius: 3px; padding: 1px 4px; margin-right: 3px; }
      .status { clear: both; color: #b9cdc2; overflow-wrap: anywhere; }
      details:not([open]) { left: auto; width: 46px; }
      details:not([open]) summary { float: none; }
      @media (prefers-color-scheme: light) { .dock { background: #f6faf8; color: #27332d; } summary { color: #286143; } kbd { color: #162c20; } .status { color: #425e4e; } }
    </style>
    <details class="dock" open aria-label="Keyboard hints">
      <summary title="Toggle keyboard hints" aria-label="Toggle keyboard hints"><svg aria-hidden="true" viewBox="0 0 24 24"><rect width="20" height="14" x="2" y="5" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 16h8"/></svg></summary>
      <div class="items">${archive ? '<span><kbd>d</kbd> Nearest 1280x</span><span><kbd>Enter</kbd> Confirm download</span>' : '<span><kbd>d</kbd> Archive Download</span><span><kbd>f</kbd> Link hints</span>'}<span><kbd>j</kbd>/<kbd>k</kbd> Page down / up</span><span><kbd>x</kbd> Close</span><span><kbd>Ctrl+W</kbd> Close (browser)</span></div>
      <div class="status" role="status" aria-live="polite"></div>
    </details>`;
  document.body.append(host);
  const dock = shadow.querySelector(".dock");
  const status = shadow.querySelector(".status");

  function numericSize(element) {
    if (!element?.matches?.("a[onclick]")) return null;
    const match = element.getAttribute("onclick").match(/^\s*return\s+do_hathdl\(['"](800|1280|1920|2560)['"]\)\s*;?\s*$/);
    return match ? Number(match[1]) : null;
  }

  function update() {
    const active = document.activeElement;
    let message = "";
    if (active?.matches("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) {
      message = "Typing: press Esc before using single-key shortcuts.";
    } else if (archive) {
      const selected = numericSize(active);
      const available = [...document.querySelectorAll("a[onclick]")]
        .filter((link) => link.getClientRects().length && getComputedStyle(link).visibility === "visible")
        .map(numericSize).filter(Boolean).sort((a, b) => Math.abs(a - 1280) - Math.abs(b - 1280));
      const original = active?.getAttribute("onclick")?.match(/^\s*return\s+do_hathdl\(['"]org['"]\)\s*;?\s*$/);
      message = selected ? `${selected}x focused. Enter submits this option.`
        : original ? "Original focused. Press d for a numeric option, or f to choose manually."
        : available.length ? `${available[0]}x available. Press d to focus it.`
        : "No supported numeric H@H option. Choose manually with f.";
    }
    if (status.textContent !== message) status.textContent = message;
    // Reserve scroll space using our own element, without restyling site content.
    host.style.height = `${dock.getBoundingClientRect().height + 16}px`;
  }
  let pending = false;
  const schedule = () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; update(); });
  };
  document.addEventListener("focusin", schedule, true);
  document.addEventListener("focusout", schedule, true);
  window.addEventListener("resize", schedule);
  window.addEventListener("pageshow", schedule);
  dock.addEventListener("toggle", update);
  new ResizeObserver(schedule).observe(dock);
  new MutationObserver((records) => {
    if (records.some((record) => record.target !== host && !host.contains(record.target))) schedule();
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["onclick", "hidden", "style", "class"] });
  update();
})();
