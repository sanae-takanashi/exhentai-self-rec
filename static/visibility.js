// A delivered page is not a visible impression. Require continuous foreground
// dwell; leaving the viewport or tab resets, rather than accumulates, time.
function observeVisibleCards(entries, send) {
  if (typeof IntersectionObserver === "undefined") return () => {};
  const states = new Map(entries.map(({ element, gallery_url }) => [
    element, { gallery_url, inView: false, timer: null, sent: false, attempts: 0 },
  ]));
  function cancel(state) {
    clearTimeout(state.timer);
    state.timer = null;
  }
  function start(element, state) {
    if (state.timer || state.sent || !state.inView || document.hidden || !element.isConnected) return;
    state.timer = setTimeout(async () => {
      state.timer = null;
      if (document.hidden || !state.inView || !element.isConnected || state.sent) return;
      state.sent = true;
      state.attempts += 1;
      try {
        await send(state.gallery_url);
        observer.unobserve(element);
        states.delete(element);
        if (!states.size) dispose();
      } catch {
        if (state.attempts < 2) {
          state.sent = false;
          start(element, state);
        }
      }
    }, 1000);
  }
  const observer = new IntersectionObserver((changes) => {
    for (const change of changes) {
      const state = states.get(change.target);
      if (!state) continue;
      state.inView = change.isIntersecting && change.intersectionRatio >= 0.5;
      if (state.inView) start(change.target, state);
      else cancel(state);
    }
  }, { threshold: [0, 0.5], rootMargin: "0px" });
  function visibilityChanged() {
    for (const [element, state] of states) {
      cancel(state);
      if (!document.hidden) start(element, state);
    }
  }
  const mutation = new MutationObserver(() => {
    for (const [element, state] of states) {
      if (!element.isConnected) {
        cancel(state);
        observer.unobserve(element);
        states.delete(element);
      }
    }
    if (!states.size) dispose();
  });
  function dispose() {
    observer.disconnect();
    mutation.disconnect();
    document.removeEventListener("visibilitychange", visibilityChanged);
    for (const state of states.values()) cancel(state);
    states.clear();
  }
  document.addEventListener("visibilitychange", visibilityChanged);
  mutation.observe(document.body, { childList: true, subtree: true });
  for (const element of states.keys()) observer.observe(element);
  if (!states.size) dispose();
  return dispose;
}
