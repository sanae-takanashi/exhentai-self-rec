"""Durable queue tests against retained synthetic SQLite databases."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from exh_rec.jobs import JobContext, JobError, JobQueue, TERMINAL, validate_steps


def eventually(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic Jobs condition timed out")
        time.sleep(0.01)


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="jobs-checks-"))
        self.database = self.directory / "synthetic.sqlite3"
        self.jobs = JobQueue(self.connect)
        self.jobs.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.jobs.shutdown()

    def submit(self, *kinds, **kwargs):
        return self.jobs.submit([{"kind": kind} for kind in (kinds or ("fetch",))], **kwargs)

    def finish(self, job):
        eventually(lambda: self.jobs.get(job["id"])["state"] in TERMINAL)
        return self.jobs.get(job["id"])

    def test_validation_is_strict_and_sanitized(self):
        for values in (None, [], {}, [{"kind": []}], [{"kind": "other"}], [{"kind": "fetch", "secret": "FAKE_SECRET"}],
                       [{"kind": "backup", "params": {"path": "FAKE_SECRET"}}],
                       [{"kind": "enrich", "params": {"limit": True}}], [{"kind": "enrich", "params": {"limit": 51}}],
                       [{"kind": "fetch", "params": {"include_rated": 1}}], [{"kind": "fetch", "params": {"query": "\nFAKE_SECRET"}}]):
            with self.subTest(values=values), self.assertRaises(JobError) as error:
                validate_steps(values)
            self.assertNotIn("FAKE_SECRET", str(error.exception))
        self.assertEqual(validate_steps([{"kind": "fetch"}])[0]["state"], "pending")

    def test_queue_order_hold_resume_and_independent_readback(self):
        first = self.submit()
        second = self.submit("enrich", next_job=True)
        self.jobs.action(first["id"], "pause")
        order = []
        self.jobs.start(lambda kind, params, ctx, source: order.append(kind) or {"fetched": 3})
        self.finish(second)
        self.assertEqual(self.jobs.get(first["id"])["state"], "held")
        self.jobs.action(first["id"], "resume")
        self.finish(first)
        self.assertEqual(order, ["enrich", "fetch"])
        with self.connect() as conn:
            rows = conn.execute("SELECT state, steps_json FROM jobs ORDER BY sequence").fetchall()
        self.assertTrue(all(row["state"] == "succeeded" for row in rows))
        self.assertEqual(json.loads(rows[0]["steps_json"])[0]["result"], {"fetched": 3})

    def test_pause_holds_single_worker_and_cancel_retains_committed_work(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def execute(kind, params, ctx, source):
            calls.append(kind)
            entered.set()
            release.wait(5)
            with self.connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS committed (n INTEGER)")
                conn.execute("INSERT INTO committed VALUES (1)")
            ctx.checkpoint("detail")
            return {"enriched": 1}
        first, second = self.submit(), self.submit("backup")
        self.jobs.start(execute)
        self.assertTrue(entered.wait(3))
        self.assertEqual(self.jobs.action(first["id"], "pause")["state"], "pause_requested")
        release.set()
        eventually(lambda: self.jobs.get(first["id"])["state"] == "paused")
        self.assertEqual(calls, ["fetch"])
        self.assertEqual(self.jobs.get(second["id"])["state"], "queued")
        self.jobs.action(first["id"], "cancel")
        self.assertEqual(self.finish(first)["state"], "cancelled")
        self.finish(second)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM committed").fetchone()[0], 2)

    def test_running_pause_resume_finishes_same_execution(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def execute(kind, params, ctx, source):
            calls.append(kind)
            entered.set()
            release.wait(5)
            ctx.checkpoint("detail")
            return {}
        first = self.submit()
        self.jobs.start(execute)
        self.assertTrue(entered.wait(3))
        self.jobs.action(first["id"], "pause")
        release.set()
        eventually(lambda: self.jobs.get(first["id"])["state"] == "paused")
        self.jobs.action(first["id"], "resume")
        self.assertEqual(self.finish(first)["state"], "succeeded")
        self.assertEqual(calls, ["fetch"])

    def test_retry_preserves_successful_steps_and_sanitizes_errors(self):
        calls = []
        def execute(kind, params, ctx, source):
            calls.append(kind)
            if kind == "fetch":
                raise RuntimeError("FAKE_SECRET")
            return {"bytes": 30, "secret": "FAKE_SECRET"}
        job = self.submit("backup", "fetch", "retrain")
        self.jobs.start(execute)
        failed = self.finish(job)
        self.assertEqual(failed["state"], "failed")
        self.assertEqual([s["state"] for s in failed["steps"]], ["succeeded", "failed", "pending"])
        self.assertNotIn("FAKE_SECRET", json.dumps(self.jobs.snapshot()))
        self.jobs.executor = lambda kind, params, ctx, source: calls.append(kind) or {"status": "ok"}
        retry = self.jobs.action(job["id"], "retry")
        self.assertEqual(retry["retry_of"], job["id"])
        self.assertEqual(self.finish(retry)["state"], "succeeded")
        self.assertEqual(calls, ["backup", "fetch", "fetch", "retrain"])

    def test_partial_stops_workflow_and_is_retryable(self):
        job = self.submit("fetch", "retrain")
        self.jobs.start(lambda *args: {"status": "partial", "stored": 5, "errors": ["FAKE_SECRET"]})
        result = self.finish(job)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["steps"][0]["result"], {"stored": 5, "errors": 1})
        self.assertEqual(result["steps"][1]["state"], "pending")

    def test_recovery_interrupts_inflight_preserves_pending_and_history(self):
        inflight, queued, held = self.submit(), self.submit(), self.submit()
        self.jobs.action(held["id"], "pause")
        with self.connect() as conn:
            steps = inflight["steps"]
            steps[0]["state"] = "running"
            conn.execute("UPDATE jobs SET state='paused',steps_json=? WHERE id=?", (json.dumps(steps), inflight["id"]))
        recovered = JobQueue(self.connect)
        recovered.recover()
        self.assertEqual(recovered.get(inflight["id"])["state"], "interrupted")
        self.assertEqual(recovered.get(inflight["id"])["steps"][0]["state"], "interrupted")
        self.assertEqual(recovered.get(queued["id"])["state"], "queued")
        self.assertEqual(recovered.get(held["id"])["state"], "held")

    def test_coalescing_workflows_and_queue_cap(self):
        job = self.submit(source="background", coalesce=True)
        self.assertEqual(self.submit(source="background", coalesce=True)["id"], job["id"])
        workflow = self.jobs.save_workflow("Daily", [{"kind": "backup"}, {"kind": "fetch"}])
        self.jobs.save_workflow("Daily updated", [{"kind": "enrich"}], workflow["id"])
        self.assertEqual(self.jobs.snapshot()["workflows"][0]["name"], "Daily updated")
        for _ in range(99):
            self.submit()
        with self.assertRaises(JobError):
            self.submit()
        self.assertEqual(len(self.jobs.snapshot()["jobs"]), 100)

    def test_sync_web_result_is_transient_and_exception_is_fixed(self):
        self.jobs.start(lambda *args: {"stored": 2, "gallery": "FAKE_SECRET"})
        result = self.jobs.run_sync([{"kind": "fetch"}])
        self.assertEqual(result["gallery"], "FAKE_SECRET")
        self.assertNotIn("FAKE_SECRET", json.dumps(self.jobs.snapshot()))
        self.jobs.executor = Mock(side_effect=RuntimeError("FAKE_SECRET"))
        with self.assertRaises(JobError) as error:
            self.jobs.run_sync([{"kind": "fetch"}])
        self.assertNotIn("FAKE_SECRET", str(error.exception))

    def test_sync_waiter_wakes_for_queued_cancel(self):
        entered, release = threading.Event(), threading.Event()
        def execute(*args):
            entered.set()
            release.wait(5)
            return {}
        self.submit()
        self.jobs.start(execute)
        self.assertTrue(entered.wait(3))
        errors = []
        def request():
            try:
                self.jobs.run_sync([{"kind": "backup"}])
            except JobError as error:
                errors.append(str(error))
        thread = threading.Thread(target=request)
        thread.start()
        try:
            eventually(lambda: len(self.jobs.snapshot()["jobs"]) == 2)
            queued = next(job for job in self.jobs.snapshot()["jobs"] if job["state"] == "queued")
            self.jobs.action(queued["id"], "cancel")
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
        finally:
            release.set()
            thread.join(3)

    def test_shutdown_unblocks_pause_and_refuses_second_worker(self):
        job = self.submit()
        def execute(kind, params, ctx, source):
            self.jobs.action(ctx.job_id, "pause")
            ctx.checkpoint("detail")
        self.jobs.start(execute)
        eventually(lambda: self.jobs.get(job["id"])["state"] == "paused")
        with self.assertRaises(JobError):
            self.jobs.start(execute)
        self.jobs.shutdown()
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")
        self.assertFalse(self.jobs.worker.is_alive())

    def test_run_next_is_priority_not_preemption(self):
        first, second = self.submit(), self.submit("backup")
        self.jobs.action(second["id"], "pause")
        self.jobs.action(second["id"], "next")
        order = []
        self.jobs.start(lambda kind, *args: order.append(kind) or {})
        self.finish(first)
        self.assertEqual(order, ["backup", "fetch"])

    def test_bookkeeping_failure_disables_worker_instead_of_running_untracked_jobs(self):
        with patch.object(self.jobs, "_claim", side_effect=RuntimeError("FAKE_SECRET")):
            self.jobs.start(lambda *args: {})
            eventually(lambda: not self.jobs.running)
            self.jobs.worker.join(3)
        self.assertFalse(self.jobs.snapshot()["worker"])

    def test_saved_workflow_names_are_unique_and_limits_hold(self):
        first = self.jobs.save_workflow("Daily", [{"kind": "fetch"}])
        with self.assertRaises(JobError):
            self.jobs.save_workflow("daily", [{"kind": "backup"}])
        for index in range(49):
            self.jobs.save_workflow("Preset " + str(index), [{"kind": "backup"}])
        with self.assertRaises(JobError):
            self.jobs.save_workflow("Extra", [{"kind": "fetch"}])
        self.jobs.save_workflow("Daily", [{"kind": "enrich"}], first["id"])
        self.assertEqual(len(self.jobs.snapshot()["workflows"]), 50)

    def test_process_death_recovers_durable_job_without_replaying_completed_steps(self):
        job = self.submit("backup", "fetch")
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("jobs_fixture.py")), str(self.database)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            eventually(lambda: self.jobs.get(job["id"])["state"] == "paused")
            process.terminate()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        recovered = JobQueue(self.connect)
        recovered.recover()
        interrupted = recovered.get(job["id"])
        self.assertEqual(interrupted["state"], "interrupted")
        self.assertEqual([step["state"] for step in interrupted["steps"]], ["succeeded", "interrupted"])
        retry = self.jobs.action(job["id"], "retry")
        order = []
        self.jobs.start(lambda kind, *args: order.append(kind) or {})
        self.assertEqual(self.finish(retry)["state"], "succeeded")
        self.assertEqual(order, ["fetch"])
