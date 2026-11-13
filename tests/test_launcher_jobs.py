"""Fail-closed Jobs client, HTTP integration and native controls."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from exh_rec import app, db
from exh_rec.jobs import JobHalt, JobQueue, timestamp
from scripts import launcher_jobs as client
from scripts.launcher_core import LauncherError
from tests import test_jobs as domain
from tests.test_jobs import eventually


def job_payload(state="queued"):
    return {"id": "a" * 32, "name": "Fetch", "state": state, "source": "launcher", "stage": "starting",
            "error_code": "", "created_at": timestamp(), "updated_at": timestamp(), "started_at": None,
            "finished_at": None, "retry_of": None,
            "steps": [{"kind": "fetch", "params": {}, "state": "pending", "result": {}}]}


def snapshot():
    return {"service": "exh-rec", "schema": 1, "worker": True, "jobs": [job_payload()], "counts": {"queued": 1}, "workflows": []}


@contextmanager
def endpoint(mode="ready", payload=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Other/1" if mode == "other" else "ExhRec/fixture"
        def log_message(self, *args):
            pass
        def do_GET(self):
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/api/jobs")
                self.end_headers()
                return
            self.send_response(404 if mode == "unsupported" else 200)
            self.end_headers()
            self.wfile.write(b"x" * 1025 if mode == "oversized" else json.dumps(payload or snapshot()).encode())
        def do_POST(self):
            self.server.posts += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(400 if mode == "reject" else 202)
            self.end_headers()
            self.wfile.write(b"invalid FAKE_SECRET" if mode == "unknown" else json.dumps(
                {"service": "exh-rec", "schema": 1, "item": job_payload()}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.posts = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class JobsClientTest(unittest.TestCase):
    def test_strips_unrecognized_fields_and_result_payloads(self):
        payload = snapshot()
        payload["private"] = "FAKE_SECRET"
        payload["jobs"][0]["private"] = "FAKE_SECRET"
        payload["jobs"][0]["steps"][0]["result"] = {"stored": 3, "secret": "FAKE_SECRET", "verified": True}
        with endpoint(payload=payload) as (home, _):
            result = client.read_jobs(home)
        self.assertEqual(result["availability"], "available")
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        self.assertEqual(result["jobs"][0]["steps"][0]["result"], {"stored": 3, "verified": True})

    def test_legacy_other_redirect_oversized_and_unsafe_urls_fail_closed(self):
        with patch.object(client, "MAX_BYTES", 1024):
            for mode in ("unsupported", "other", "redirect", "oversized"):
                with self.subTest(mode=mode), endpoint(mode) as (home, server):
                    self.assertEqual(client.read_jobs(home)["availability"], "unsupported" if mode == "unsupported" else "unavailable")
                    with self.assertRaises(LauncherError):
                        client.write_jobs(home, "api/jobs", {"steps": [{"kind": "backup"}]})
                    self.assertEqual(server.posts, 0)
        with patch.object(client.urllib.request, "build_opener") as request:
            for home in ("http://user:fake@127.0.0.1:12/", "http://localhost:12/", "https://127.0.0.1:12/",
                         "http://127.0.0.1:12/?a=b", "http://127.0.0.1:12/path"):
                self.assertEqual(client.read_jobs(home)["availability"], "unavailable")
        request.assert_not_called()

    def test_uncertain_post_is_not_retried_and_never_displays_raw_error(self):
        for mode, expected in (("unknown", "jobs-outcome-unknown"), ("reject", "jobs-rejected")):
            with endpoint(mode) as (home, server):
                with self.assertRaises(LauncherError) as error:
                    client.write_jobs(home, "api/jobs", {"steps": [{"kind": "fetch"}]})
                self.assertEqual(str(error.exception), expected)
                self.assertEqual(server.posts, 1)

    def test_malformed_snapshot_shapes_never_escape_read(self):
        payloads = []
        for key, value in (("schema", True), ("worker", 1), ("jobs", {}), ("counts", []), ("workflows", [None])):
            payloads.append({**snapshot(), key: value})
        for key, value in (("steps", "FAKE_SECRET"), ("steps", [None]), ("state", []), ("id", "FAKE_SECRET"),
                           ("created_at", "2026-11-09T00:00:00")):
            payload = snapshot()
            payload["jobs"][0][key] = value
            payloads.append(payload)
        for payload in payloads:
            with patch.object(client, "_request", return_value=payload):
                self.assertEqual(client.read_jobs("unused"), {"availability": "unavailable"})


class JobsAPITest(unittest.TestCase):
    connect = domain.JobsTest.connect
    setUp = domain.JobsTest.setUp
    tearDown = domain.JobsTest.tearDown

    def test_real_http_submit_read_action_and_legacy_web_share_queue(self):
        entered, release = threading.Event(), threading.Event()
        def execute(kind, params, ctx, source):
            entered.set()
            release.wait(5)
            ctx.checkpoint("detail")
            return {"stored": 2, "status": "success"}
        self.jobs.start(execute)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(app, "JOBS", self.jobs), patch.object(app.Handler, "log_message"):
            thread.start()
            home = f"http://127.0.0.1:{server.server_port}/"
            try:
                result = client.write_jobs(home, "api/jobs", {"steps": [{"kind": "fetch"}]})
                self.assertTrue(entered.wait(3))
                client.write_jobs(home, "api/jobs/action", {"id": result["id"], "action": "pause"})
                release.set()
                eventually(lambda: self.jobs.get(result["id"])["state"] == "paused")
                self.assertEqual(client.read_jobs(home)["jobs"][0]["state"], "paused")
                client.write_jobs(home, "api/jobs/action", {"id": result["id"], "action": "resume"})
                eventually(lambda: self.jobs.get(result["id"])["state"] == "succeeded")
                request = urllib.request.Request(home + "api/fetch", data=b"{}", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["stored"], 2)
                self.assertEqual(self.jobs.snapshot()["counts"], {"succeeded": 2})
                self.assertTrue(any(job["source"] == "web" for job in self.jobs.snapshot()["jobs"]))
                for payload in ({"steps": [{"kind": []}]}, {"steps": [{"kind": "fetch"}], "next": 1}, []):
                    request = urllib.request.Request(home + "api/jobs", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_executor_reuses_verified_backup_and_defers_workflow_retrain(self):
        with patch.object(db, "DB_PATH", self.database):
            db.init_db()
            context = Mock(job_id="0" * 32)
            self.assertTrue(app.execute_job("backup", {}, context, "launcher")["verified"])
        receipts = [path for path in (self.directory / "backups").glob("*.json") if not path.name.endswith('.bundle.json')]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(json.loads(receipts[0].read_text())["verified"])
        context.has_later.return_value = True
        with patch.object(app, "fetch_and_store", return_value={}) as fetch:
            app.execute_job("fetch", {}, context, "background")
        self.assertTrue(fetch.call_args.kwargs["defer_retrain"])
        self.assertEqual(fetch.call_args.kwargs["trigger"], "background")

    def test_schedulers_enqueue_instead_of_executing_directly(self):
        stop = threading.Event()
        def wait(*args):
            stop.set()
        self.jobs.start(lambda *args: {})
        with patch.object(app, "JOBS", self.jobs), patch.object(app, "model_retrain_due", return_value=(True, "manual", 0)), patch.object(
                app, "wait_for_model_retrain_wake", side_effect=wait), patch.object(app, "run_model_retrain") as train:
            app.background_model_retrain(stop)
        train.assert_not_called()
        self.assertTrue(any(job["source"] == "model" for job in self.jobs.snapshot()["jobs"]))

    def test_unavailable_worker_does_not_spin_or_bypass_queue_for_training(self):
        self.jobs.start(lambda *args: {})
        self.jobs.shutdown()
        stop = threading.Event()
        with patch.object(app, "JOBS", self.jobs), patch.object(app, "model_retrain_due", return_value=(True, "manual", 0)), patch.object(
                app, "wait_for_model_retrain_wake", side_effect=lambda *args: stop.set()) as wait, patch.object(app, "run_model_retrain") as train:
            app.background_model_retrain(stop)
        wait.assert_called_once()
        train.assert_not_called()

    def test_real_enrichment_cancel_keeps_committed_detail_and_releases_lock(self):
        from exh_rec.exhentai import Gallery
        from exh_rec.recency import recent_gallery_cutoff
        gallery = Gallery(url="https://exhentai.org/g/1/a/", gid="1", token="a", title="Synthetic",
                          posted_at=recent_gallery_cutoff(), tags=["artist:synthetic"])
        with patch.object(db, "DATA_DIR", self.directory), patch.object(db, "DB_PATH", self.database):
            db.init_db()
            with db.connect() as conn:
                db.set_setting(conn, "cookie_header", "synthetic-test-cookie")
            def checkpoint(stage):
                if stage == "finalizing":
                    raise JobHalt("cancelled")
            with patch.object(app, "select_recommendation_detail_candidates", return_value=[gallery]), patch.object(
                    app, "fetch_gallery_detail", return_value=gallery), patch.object(app, "ensure_api_cover"), patch.object(
                    app, "collect_gallery_samples", return_value=[]), patch.object(app, "cache_sample_thumbnails"), patch.object(
                    app, "fetch_parent_chain_metadata", return_value=0), patch.object(app, "retrain_model") as train:
                with self.assertRaises(JobHalt):
                    app.enrich_recommendations(limit=1, checkpoint=checkpoint)
            train.assert_not_called()
            self.assertFalse(app.FETCH_LOCK.locked())
            with db.connect() as conn:
                row = conn.execute("SELECT detail_fetched_at FROM galleries WHERE url=?", (gallery.url,)).fetchone()
                self.assertIsNotNone(row["detail_fetched_at"])
                self.assertGreater(app.model_retrain_pending_count(conn), 0)
                self.assertEqual(conn.execute("SELECT status FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()[0], "failed")

    def test_real_fetch_cancel_keeps_committed_listing_and_cursor(self):
        from exh_rec.exhentai import Gallery, GalleryBatch
        from exh_rec.recency import recent_gallery_cutoff
        gallery = Gallery(url="https://exhentai.org/g/2/b/", gid="2", token="b", title="Synthetic",
                          posted_at=recent_gallery_cutoff())
        with patch.object(db, "DATA_DIR", self.directory), patch.object(db, "DB_PATH", self.database):
            db.init_db()
            with db.connect() as conn:
                db.set_setting(conn, "cookie_header", "synthetic-test-cookie")
            def checkpoint(stage):
                if stage == "query_metadata":
                    raise JobHalt("cancelled")
            with patch.object(app, "fetch_galleries", return_value=GalleryBatch([gallery], next_url=None)), patch.object(
                    app, "enrich_covers_via_api"), patch.object(app, "retrain_model") as train:
                with self.assertRaises(JobHalt):
                    app.fetch_and_store(force_query="artist:synthetic", checkpoint=checkpoint)
            train.assert_not_called()
            self.assertFalse(app.FETCH_LOCK.locked())
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries WHERE url=?", (gallery.url,)).fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT stored_count FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()[0], 1)


class JobsUITest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from scripts.launcher import LauncherApp
        self.root = tk.Tk()
        self.root.withdraw()
        self.directory = Path(tempfile.mkdtemp(prefix="jobs-ui-"))
        self.app = LauncherApp(self.root, self.directory / "synthetic.json")
        for timer in list(self.app.timers):
            self.root.after_cancel(timer)
        self.app.timers.clear()
        self.app.refresh = Mock()
        self.available = {"availability": "available", **client.validate_snapshot(snapshot())}
        self.app.latest = {"state": "running", "managed": True, "home_url": "http://127.0.0.1:12345/", "jobs": self.available}
        self.app.render_status(self.app.latest)

    def tearDown(self):
        self.app.close()

    def pump(self):
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.app.drain()

    def test_composition_actions_and_saved_presets(self):
        self.app.jobs.preset.set("Backup + Fetch + Retrain")
        self.app.jobs.load_preset()
        self.app.jobs.query.set("artist:synthetic")
        self.assertEqual([s["kind"] for s in self.app.jobs.definition()], ["backup", "fetch", "retrain"])
        with patch("scripts.launcher.write_jobs", return_value=job_payload()) as write, patch("tkinter.messagebox.askyesno", return_value=True):
            self.app.queue_job(next_job=True)
            self.pump()
        self.assertTrue(write.call_args.args[2]["next"])
        self.assertEqual(write.call_args.args[2]["steps"][1]["params"]["query"], "artist:synthetic")
        self.assertEqual(self.app.notebook.select(), str(self.app.jobs.frame))
        self.assertFalse(self.app.path.exists())

    def test_offline_clears_stale_jobs_and_disables_mutations(self):
        self.assertTrue(self.app.jobs.table.get_children())
        self.app.render_status({"state": "stopped"})
        self.assertFalse(self.app.jobs.table.get_children())
        self.assertEqual(str(self.app.jobs.queue_button.cget("state")), "disabled")
        with patch("scripts.launcher.write_jobs") as write:
            self.app.queue_job()
        write.assert_not_called()

    def test_partial_job_enables_retry_and_resume_only_for_paused(self):
        job = self.available["jobs"][0]
        for state in ("partial", "paused", "queued", "succeeded"):
            job["state"] = state
            self.app.render_status(self.app.latest)
            self.assertEqual(str(self.app.jobs.actions["retry"].cget("state")), "normal" if state == "partial" else "disabled")
            self.assertEqual(str(self.app.jobs.actions["resume"].cget("state")), "normal" if state == "paused" else "disabled")

    def test_tray_hide_restore_exit_never_stops_backend(self):
        with patch.object(type(self.app.tray), "available", new_callable=lambda: property(lambda _: True)), patch.object(self.app.controller, "stop") as stop:
            self.root.deiconify()
            self.app.request_close()
            self.assertEqual(self.root.state(), "withdrawn")
            self.app.tray.events.put("jobs")
            self.app.drain_tray()
            self.assertNotEqual(self.root.state(), "withdrawn")
            self.assertEqual(self.app.notebook.select(), str(self.app.jobs.frame))
            self.app.tray.events.put("exit")
            self.app.drain_tray()
        self.assertTrue(self.app.closed)
        stop.assert_not_called()

    def test_tray_start_uses_saved_profile_and_preferences_are_separate(self):
        self.app.variables["port"].set("invalid-unsaved")
        with patch.object(self.app.controller, "start", return_value="started") as start:
            self.app.start_saved()
            self.pump()
        start.assert_called_once_with(self.app.config)
        self.app.save_tray_preferences()
        self.assertTrue(self.app.ui_path.exists())
        self.assertFalse(self.app.path.exists())

    def test_observed_service_failure_notifies_once_but_probe_errors_do_not(self):
        self.app.tray.notify = Mock()
        def status(value):
            self.app.results.put(("status", value, None, None, self.app.config_generation))
            self.app.drain()
        status({**self.app.latest, "run_id": "synthetic-run"})
        self.app.results.put(("status", None, None, "Status unavailable", self.app.config_generation))
        self.app.drain()
        self.app.tray.notify.assert_not_called()
        status({"state": "failed", "managed": False})
        status({"state": "failed", "managed": False})
        self.app.tray.notify.assert_called_once_with("Managed service failed. Open Launcher for status.")
