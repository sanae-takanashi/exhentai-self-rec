"""Isolated fetch telemetry, HTTP and native view checks."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from exh_rec.fetch_activity import FetchActivity
from scripts.launcher_fetch import read_fetch_activity, validate_activity


def running_activity():
    activity = FetchActivity()
    state = {"running": True, "stage": "starting", "trigger": "manual", "started_at": "2026-10-28 12:00:00",
             "page_count": 1, "stale_extra_pages": 2, "query_total": 2, "detail_limit": 3,
             "sample_extra_pages": 2, "errors": [], "run_id": 42}
    activity.update("fetch started", state, plan=[
        {"query": None, "source": "recent", "page_budget": 3, "resuming": True},
        {"query": "artist:fixture", "source": "manual", "page_budget": 1},
    ])
    return activity, state


@contextmanager
def telemetry_server(payload, mode="normal"):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        server_version = "Other/fixture" if mode == "other" else "ExhRec/fixture"

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if mode == "slow":
                time.sleep(.15)
            body = json.dumps(payload).encode("utf-8")
            if mode == "large":
                body = b"x" * (256 * 1024 + 1)
            if mode == "invalid":
                body = b"not-json-private-output"
            self.send_response(404 if mode == "old" else 302 if mode == "redirect" else 200)
            if mode == "redirect":
                self.send_header("Location", "/should-not-follow")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class FetchActivityTests(unittest.TestCase):
    def test_completion_reasons_and_scope_are_retained_per_query(self):
        activity, state = running_activity()
        state.update(query_index=1, min_posted_date="2026-08-05", query_page_limit=25,
                     cursor_caught_up=True, page_limit_reached=True, query_completion_reason="page_limit")
        activity.update("query started", state)
        activity.update("query stored", state)
        state.update(query_index=2, query_completion_reason="age_limit", page_limit_reached=False, age_limit_reached=True)
        activity.update("query started", state)
        activity.update("query stored", state)
        data = validate_activity(activity.snapshot())
        self.assertEqual([row["completion_reason"] for row in data["queries"]], ["page_limit", "age_limit"])
        self.assertEqual(data["min_posted_date"], "2026-08-05")

    def test_metadata_errors_do_not_look_like_successful_page_cap(self):
        activity, state = running_activity()
        state.update(query_index=1, query_page_limit=25, cursor_caught_up=True, query_completion_reason="page_limit")
        activity.update("query started", state)
        activity.update("cover metadata failed", state)
        activity.update("query stored", state)
        row = validate_activity(activity.snapshot())["queries"][0]
        self.assertEqual(row["completion_reason"], "metadata_failed")
        self.assertEqual(row["state"], "partial")

    def test_phase_time_and_wait_countdown_are_independent_of_polling(self):
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=100):
            activity, state = running_activity()
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=105):
            state.update(stage="cover_metadata", metadata_batch=2, metadata_total_batches=10, metadata_batches_done=1)
            activity.update("cover metadata batch", state)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=110):
            state.update(wait_reason="metadata_pause", wait_seconds=5)
            activity.update("request activity", state)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=112):
            data = validate_activity(activity.snapshot())
        self.assertEqual(data["phase_elapsed_seconds"], 7)
        self.assertEqual(data["wait_remaining_seconds"], 3)
        self.assertEqual(data["quiet_seconds"], 2)
        self.assertEqual(data["metadata_batches_done"], 1)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=113):
            activity.update("cover metadata batch", state)
            self.assertEqual(activity.snapshot()["activity"]["wait_remaining_seconds"], 0)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=116):
            state.update(stage="finished", running=False, status="success")
            activity.update("fetch finished", state)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=150):
            data = activity.snapshot()["activity"]
        self.assertEqual(data["wait_remaining_seconds"], 0)
        self.assertEqual(data["wait_reason"], "")

    def test_query_budget_counts_and_incomplete_do_not_claim_success(self):
        activity, state = running_activity()
        state.update(stage="fetching_pages", query_index=1, query_page_limit=3, cursor_active=True, cursor_resuming=True)
        activity.update("query started", state)
        state.update(fetched_batch=20, stored_page=7, fetched=20, stored=7)
        activity.update("query page fetched", state)
        state.update(stage="storing", cursor_caught_up=False)
        activity.update("query stored", state)
        data = validate_activity(activity.snapshot())
        self.assertEqual(data["pages_done"], 1)
        self.assertEqual(data["page_budget"], 4)
        self.assertEqual(data["queries_done"], 1)
        self.assertEqual(data["incomplete_count"], 1)
        self.assertEqual(data["queries"][0]["state"], "partial")
        self.assertEqual(data["queries"][0]["stored"], 7)
        self.assertTrue(data["running"])

    def test_errors_are_counted_without_private_details_or_urls(self):
        activity, state = running_activity()
        marker = "fixture-private-token-cookie-url-path"
        state.update(stage="failed", error=marker, errors=[marker], parent_errors=[marker],
                     current_gallery_url=marker, current_parent_url=marker, next_url=marker)
        activity.update("fetch failed", state)
        serialized = json.dumps(activity.snapshot())
        self.assertNotIn(marker, serialized)
        self.assertEqual(activity.snapshot()["activity"]["error_count"], 1)
        self.assertEqual(activity.snapshot()["activity"]["parent_error_count"], 1)

    def test_snapshots_are_copies_and_events_are_bounded(self):
        activity, state = running_activity()
        for _ in range(150):
            activity.update("fetching query page", state)
        snapshot = activity.snapshot()
        self.assertEqual(len(snapshot["activity"]["events"]), 120)
        snapshot["activity"]["queries"][0]["label"] = "modified"
        self.assertEqual(activity.snapshot()["activity"]["queries"][0]["label"], "Recent galleries")

    def test_display_text_keeps_status_readable_with_invisible_title_characters(self):
        activity, state = running_activity()
        state.update(stage="enriching_details", current_gallery_title="Synthetic\u200b\x00 title\nnext")
        activity.update("detail fetch started", state)
        data = validate_activity(activity.snapshot())
        self.assertEqual(data["current_item"], "Synthetic title next")

    def test_abort_marks_active_query_failed_and_unstarted_queries_skipped(self):
        activity, state = running_activity()
        state.update(query_index=1, query_page_limit=3, stage="fetching_pages")
        activity.update("query started", state)
        state.update(running=False, stage="failed")
        activity.update("fetch failed", state)
        data = validate_activity(activity.snapshot())
        self.assertEqual([row["state"] for row in data["queries"]], ["failed", "skipped"])
        self.assertEqual(data["queries_done"], 1)

    def test_elapsed_freezes_after_finish_and_quiet_time_still_grows(self):
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=100):
            activity, state = running_activity()
        state.update(stage="finished", status="partial", running=False)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=110):
            activity.update("fetch finished", state)
        with patch("exh_rec.fetch_activity.time.monotonic", return_value=140):
            data = activity.snapshot()["activity"]
        self.assertEqual(data["elapsed_seconds"], 10)
        self.assertEqual(data["quiet_seconds"], 30)
        self.assertEqual(data["status"], "partial")

    def test_app_progress_resets_old_run_and_enrichment_state(self):
        from exh_rec import app
        with patch.object(app, "FETCH_ACTIVITY", FetchActivity()), patch.dict(app.FETCH_STATE, {}, clear=True), patch("builtins.print"):
            app.update_fetch_progress("fetch started", running=True, stage="starting", run_id=99,
                                      query_index=12, errors=["private"], cursor_caught_up=True)
            app.update_fetch_progress("enrichment started", running=True, stage="selecting_details",
                                      trigger="enrich", started_at="2026-10-28 12:00:00", detail_limit=2)
            self.assertNotIn("run_id", app.FETCH_STATE)
            self.assertNotIn("query_index", app.FETCH_STATE)
            data = validate_activity(app.FETCH_ACTIVITY.snapshot())
            self.assertEqual(data["query_total"], 0)
            self.assertEqual(data["error_count"], 0)
            self.assertEqual(data["queries"], [])

    def test_poll_endpoint_never_opens_database_or_computes_plan(self):
        from exh_rec import app
        import urllib.request
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(app.db, "connect", side_effect=AssertionError("Unexpected DB access")), patch.object(app, "plan_fetch", side_effect=AssertionError()), patch.object(app.Handler, "log_message"):
            thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/fetch-progress", timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["service"], "exh-rec")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_client_reads_only_fixed_endpoint_and_validates_response(self):
        activity, _ = running_activity()
        with telemetry_server(activity.snapshot()) as (url, requests):
            result = read_fetch_activity(url)
        self.assertEqual(result["availability"], "available")
        self.assertEqual(result["activity"]["run_id"], 42)
        self.assertEqual(requests, ["/api/fetch-progress"])

    def test_client_fails_closed_for_old_wrong_large_slow_and_redirected_services(self):
        activity, _ = running_activity()
        for mode in ("old", "other", "large", "slow", "redirect", "invalid"):
            with self.subTest(mode=mode), telemetry_server(activity.snapshot(), mode) as (url, requests):
                result = read_fetch_activity(url, timeout=.03 if mode == "slow" else .8)
                self.assertEqual(result, {"availability": "unsupported" if mode == "old" else "unavailable"})
                self.assertEqual(requests, ["/api/fetch-progress"])
        self.assertEqual(read_fetch_activity("http://user:private@127.0.0.1:1/"), {"availability": "unavailable"})

    def test_client_rejects_invalid_nested_fields(self):
        activity, _ = running_activity()
        payload = activity.snapshot()
        for field, value in (("stage", "private"), ("run_id", "private"), ("queries", None),
                             ("events", [{"message": "private"}]), ("current_item", "x\nprivate"),
                             ("wait_reason", "private"), ("metadata_batch", -1), ("min_posted_date", "private")):
            invalid = deepcopy(payload)
            invalid["activity"][field] = value
            with self.subTest(field=field), telemetry_server(invalid) as (url, _):
                self.assertEqual(read_fetch_activity(url), {"availability": "unavailable"})


@unittest.skipUnless(os.name == "nt", "Windows native launcher")
class FetchActivityViewTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        from scripts.launcher_fetch import FetchActivityView
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        self.view = FetchActivityView(self.notebook, tk, ttk)

    def tearDown(self):
        self.root.destroy()

    def test_renders_scope_completion_reason_and_metadata_wait(self):
        activity, state = running_activity()
        state.update(query_index=1, query_page_limit=25, cursor_caught_up=True,
                     query_completion_reason="page_limit", min_posted_date="2026-08-05")
        activity.update("query started", state)
        activity.update("query stored", state)
        state.update(stage="cover_metadata", metadata_batch=2, metadata_total_batches=10, metadata_batches_done=1,
                     wait_reason="metadata_pause", wait_seconds=5)
        activity.update("request activity", state)
        self.view.render({"availability": "available", "activity": validate_activity(activity.snapshot())})
        self.assertEqual(self.view.plan.item("1", "values")[-1], "Page cap")
        self.assertIn("Published >= 2026-08-05", self.view.plan_label.cget("text"))
        self.assertIn("cap completes", self.view.plan_label.cget("text"))
        self.assertIn("1/10 processed", self.view.detail_label.cget("text"))
        self.assertIn("Metadata batch pause", self.view.summary.cget("text"))
        self.assertEqual(float(self.view.detail_bar.cget("value")), 1)
        self.assertEqual(float(self.view.detail_bar.cget("maximum")), 10)
        self.assertNotIn("ETA", self.view.summary.cget("text"))

    def test_renders_plan_budget_and_completed_queries_while_details_still_running(self):
        activity, state = running_activity()
        for index in (1, 2):
            state.update(query_index=index, query_page_limit=1, cursor_caught_up=True)
            activity.update("query started", state)
            activity.update("query stored", state)
        state.update(stage="enriching_details", detail_total=3, detail_done=1, enriched=1,
                     current_gallery_title="Synthetic gallery")
        activity.update("detail fetch started", state)
        self.view.render({"availability": "available", "activity": validate_activity(activity.snapshot())})
        self.assertIn("Running", self.view.heading.cget("text"))
        self.assertIn("2/2", self.view.query_label.cget("text"))
        self.assertIn("1/3", self.view.detail_label.cget("text"))
        self.assertIn("Synthetic gallery", self.view.summary.cget("text"))
        self.assertEqual(float(self.view.detail_bar.cget("value")), 1)
        self.assertEqual(self.view.plan.item("1", "values")[2], "done")
        self.assertEqual(len(self.view.events.get_children()), 6)

    def test_partial_and_unavailable_are_explicit_and_never_display_stale_live_counts(self):
        activity, state = running_activity()
        state.update(stage="finished", status="partial", running=False)
        activity.update("fetch finished", state)
        self.view.render({"availability": "available", "activity": validate_activity(activity.snapshot())})
        self.assertIn("Partially completed", self.view.heading.cget("text"))
        self.view.render({"availability": "unavailable"})
        self.assertIn("unavailable", self.view.heading.cget("text"))
        self.assertEqual(self.view.plan.get_children(), ())
        self.assertEqual(self.view.metrics["Fetched"].get(), "Fetched: --")
        self.view.render({"availability": "unsupported"})
        self.assertIn("restart required", self.view.heading.cget("text"))


if __name__ == "__main__":
    unittest.main()
