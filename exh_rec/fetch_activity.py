"""Bounded in-memory fetch telemetry, independent of database and private errors."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time


MESSAGES = {
    "fetch started": "Fetch started",
    "fetch run recorded": "Run recorded",
    "query started": "Query started",
    "fetching query page": "Requesting list page",
    "query page fetched": "List page stored",
    "query page failed": "List page failed",
    "cover metadata checked": "Cover metadata checked",
    "cover metadata failed": "Cover metadata failed",
    "query stored": "Query processed",
    "query failed": "Query failed",
    "detail enrichment selected": "Detail candidates selected",
    "detail fetch started": "Fetching details and samples",
    "detail samples started": "Collecting sample pages",
    "detail cache started": "Caching sample thumbnails",
    "detail storing": "Storing detail metadata",
    "parent chain started": "Fetching parent metadata",
    "detail fetch finished": "Details and samples stored",
    "detail fetch failed": "Detail enrichment failed",
    "parent chain fetch failed": "Parent metadata failed",
    "fetch returned no galleries": "No galleries returned",
    "retraining model": "Retraining recommendation model",
    "refreshing recommendations": "Refreshing recommendations",
    "fetch finished": "Fetch finished",
    "fetch failed": "Fetch failed",
    "enrichment started": "Detail enrichment started",
    "enrichment run recorded": "Enrichment run recorded",
    "enrichment finished": "Detail enrichment finished",
    "enrichment failed": "Detail enrichment failed",
}
STAGES = frozenset({"starting", "running", "fetching_pages", "error", "cover_metadata",
                    "storing", "selecting_details", "enriching_details", "retraining", "finalizing", "finished", "failed"})
COUNTERS = ("run_id", "fetched", "stored", "enriched", "parent_enriched", "detail_limit",
            "detail_total", "detail_done", "detail_index", "sample_extra_pages", "query_index", "query_total")


def number(value):
    return max(0, value) if type(value) is int else 0


def display_text(value, limit=256):
    normalized = " ".join(str(value or "").split())
    return "".join(char for char in normalized if char.isprintable())[:limit]


class FetchActivity:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"running": False, "stage": "idle", "queries": [], "events": []}
        self.started = None
        self.updated = None
        self.finished = None

    def update(self, message, state, plan=None):
        with self.lock:
            now = time.monotonic()
            if message in {"fetch started", "enrichment started"}:
                self.started, self.updated, self.finished = now, now, None
                self.data = {"queries": [], "events": [], "initial_pages": number(state.get("page_count")),
                             "extra_pages": number(state.get("stale_extra_pages")), "queries_done": 0}
                for index, entry in enumerate(plan or [], 1):
                    self.data["queries"].append({
                        "index": index, "label": display_text(entry.get("query") or "Recent galleries"),
                        "source": entry.get("source") if entry.get("source") in {"recent", "bootstrap", "learned", "manual"} else "other",
                        "state": "pending", "pages_done": 0, "page_budget": number(entry.get("page_budget")),
                        "fetched": 0, "stored": 0, "resuming": bool(entry.get("resuming")),
                    })
            if self.started is None:
                return
            data = self.data
            for key in COUNTERS:
                data[key] = number(state.get(key))
            data["running"] = state.get("running") is True
            data["stage"] = state.get("stage") if state.get("stage") in STAGES else "running"
            data["trigger"] = state.get("trigger") if state.get("trigger") in {"manual", "background", "enrich"} else "other"
            data["status"] = state.get("status") if state.get("status") in {"success", "partial", "failed"} else ""
            data["error_count"] = len(state.get("errors") or [])
            data["parent_error_count"] = len(state.get("parent_errors") or [])
            data["incomplete_count"] = len(state.get("cursor_incomplete_queries") or [])
            data["model_retrained"] = state.get("model_retrained") is True
            data["current_item"] = display_text(state.get("current_gallery_title")) if data["stage"] == "enriching_details" else ""
            data["started_at"] = state.get("started_at", "")
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            index = data["query_index"]
            if 0 < index <= len(data["queries"]):
                row = data["queries"][index - 1]
                if message == "query started":
                    row.update(state="running", resuming=bool(state.get("cursor_resuming")),
                               page_budget=number(state.get("query_page_limit")), error=False)
                if message == "query page fetched":
                    row["pages_done"] += 1
                    row["fetched"] += number(state.get("fetched_batch"))
                    row["stored"] += number(state.get("stored_page"))
                if message in {"query page failed", "cover metadata failed", "query failed"}:
                    row["error"] = True
                if message in {"query stored", "query failed"}:
                    row["state"] = "failed" if message == "query failed" else "partial" if row.get("error") or not state.get("cursor_caught_up") else "done"
                    row["incomplete"] = not state.get("cursor_caught_up") and row["source"] in {"recent", "bootstrap", "learned"}
                row["anchor_matches"] = number(state.get("cursor_matches"))
                row["anchor_target"] = number(state.get("cursor_match_target"))
            data["queries_done"] = sum(row["state"] in {"done", "partial", "failed"} for row in data["queries"])
            data["pages_done"] = sum(row["pages_done"] for row in data["queries"])
            data["page_budget"] = sum(row["page_budget"] for row in data["queries"])
            data["incomplete_count"] = max(data["incomplete_count"], sum(bool(row.get("incomplete")) for row in data["queries"]))
            if message in MESSAGES:
                data["message"] = MESSAGES[message]
                data["events"] = [*data["events"][-119:], {
                    "at": data["updated_at"], "message": MESSAGES[message], "query_index": index,
                    "detail_index": number(state.get("detail_index")) if "detail" in message or "parent chain" in message else 0,
                    "page": number(state.get("page_start")) + 1 if message.startswith("query page") or message == "fetching query page" else 0,
                    "warning": "failed" in message or message == "fetch returned no galleries",
                }]
            self.updated = now
            if not data["running"] and self.finished is None:
                self.finished = now
                if data["stage"] != "finished":
                    data["stage"], data["status"] = "failed", "failed"
                    for row in data["queries"]:
                        if row["state"] in {"pending", "running"}:
                            row["state"] = "failed" if row["state"] == "running" else "skipped"
                    data["queries_done"] = sum(row["state"] in {"done", "partial", "failed"} for row in data["queries"])

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.data)
            now = time.monotonic()
            result["elapsed_seconds"] = int((self.finished or now) - self.started) if self.started is not None else 0
            result["quiet_seconds"] = int(now - self.updated) if self.updated is not None else 0
            return {"service": "exh-rec", "schema": 1, "activity": result}
