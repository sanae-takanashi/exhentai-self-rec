"""Lock ordering and publication checks with retained, synthetic databases."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from exh_rec import app, classification, db, jobs, personalized as p, recommender as r
from exh_rec.exhentai import Gallery


class LockSafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="exh-lock-safety-"))
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for owner, name, value in (
            (db, "DATA_DIR", self.directory), (db, "DB_PATH", self.directory / "test.sqlite3"),
            (p, "MODEL_PATH", self.directory / "model.joblib"), (p, "_MODEL_CACHE", {}),
            (p, "_MODEL_LOCK", threading.RLock()), (p, "_SNAPSHOT_LOCK", threading.Lock()),
            (classification, "MODEL_PATH", self.directory / "classifier.joblib"),
            (classification, "_CACHE", {}), (r, "SCAN_CACHE", {}),
            (app, "FETCH_LOCK", threading.Lock()), (app, "FETCH_STATE", {}),
            (app, "MODEL_RETRAIN_STATE", {}), (app, "MODEL_RETRAIN_WAKE", threading.Event()),
            (app, "MODEL_RETRAIN_RUN_LOCK", threading.Lock()),
        ):
            self.patches.enter_context(patch.object(owner, name, value))
        db.init_db()
        self.old = "https://exhentai.org/g/8210/a/"
        self.latest = "https://exhentai.org/g/8211/a/"
        with db.connect() as conn:
            r.store_galleries(conn, [
                Gallery(url=self.old, gid="8210", token="a", title="[Fanbox] Copy Parent"),
                Gallery(url=self.latest, gid="8211", token="a", title="[Fanbox] Copy Parent", parent_url=self.old),
            ])
            r.record_feedback(conn, self.old, score=5, retrain=False)

    def handler(self, route, payload):
        handler = app.Handler.__new__(app.Handler)
        handler.path = route
        handler.read_json = Mock(return_value=payload)
        handler.send_json = Mock()
        handler.handle_error = Mock()
        return handler

    def status(self):
        with db.connect() as conn:
            return app.model_retrain_status(conn)

    def artifact(self, signature, ready=False):
        return {"schema": p.MODEL_SCHEMA, "signature": signature, "ready": ready,
                "status": "ready" if ready else "insufficient-data"}

    def activate(self, signature):
        with db.connect() as conn:
            db.set_setting(conn, "model_retrain_active_signature", signature)

    def test_copy_commits_feedback_and_pending_before_wake_without_training(self):
        handler = self.handler("/api/feedback/copy-continuing", {"gallery_url": self.latest})

        def wake():
            with db.connect() as conn:
                self.assertEqual(r.feedback_history(conn, self.latest)[0]["score"], 5)
                self.assertEqual(app.model_retrain_pending_count(conn), 1)
                db.set_setting(conn, "wake_writer", "available")

        with patch.object(app.MODEL_RETRAIN_WAKE, "set", side_effect=wake) as signal, \
                patch.object(app, "continuing_update_payload", return_value={}), \
                patch.object(r, "retrain_model") as train:
            handler.do_POST()
        handler.handle_error.assert_not_called()
        handler.send_json.assert_called_once()
        self.assertEqual(handler.send_json.call_args.args[0]["copied_from"]["url"], self.old)
        signal.assert_called_once()
        train.assert_not_called()

    def test_copy_rolls_back_feedback_when_pending_write_fails(self):
        handler = self.handler("/api/feedback/copy-continuing", {"gallery_url": self.latest})
        with patch.object(app, "mark_model_retrain_pending", side_effect=sqlite3.OperationalError("fixture")):
            handler.do_POST()
        handler.handle_error.assert_called_once()
        handler.send_json.assert_not_called()
        self.assertFalse(app.MODEL_RETRAIN_WAKE.is_set())
        with db.connect() as conn:
            self.assertEqual(r.feedback_history(conn, self.latest), [])

    def test_copy_pending_survives_lost_wake(self):
        handler = self.handler("/api/feedback/copy-continuing", {"gallery_url": self.latest})
        with patch.object(app.MODEL_RETRAIN_WAKE, "set"), \
                patch.object(app, "continuing_update_payload", return_value={}):
            handler.do_POST()
        app.MODEL_RETRAIN_STATE.clear()
        self.assertEqual(self.status()["pending_count"], 1)
        with db.connect() as conn:
            db.set_setting(conn, "model_retrain_mode", "immediate")
            self.assertTrue(app.model_retrain_due(conn)[0])

    def test_feedback_and_undo_during_training_remain_for_next_run(self):
        with db.connect() as conn:
            app.mark_model_retrain_pending(conn)
            db.set_setting(conn, "model_retrain_mode", "immediate")
        entered, release = threading.Event(), threading.Event()
        errors = []

        def compute(conn):
            self.assertFalse(conn.in_transaction)
            with p._MODEL_LOCK:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture timeout")
            return self.artifact("first")

        def train():
            try:
                app.run_model_retrain("feedback")
            except Exception as exc:
                errors.append(exc)

        with patch.object(r, "train_personalized_model", side_effect=compute), \
                patch.object(app, "continuing_update_payload", return_value={}):
            worker = threading.Thread(target=train)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                copied = self.handler("/api/feedback/copy-continuing", {"gallery_url": self.latest})
                copied.do_POST()
                copied.handle_error.assert_not_called()
                with db.connect() as conn:
                    feedback_id = r.feedback_history(conn, self.latest)[0]["id"]
                undone = self.handler("/api/feedback/undo", {"gallery_url": self.latest, "feedback_id": feedback_id})
                undone.do_POST()
                undone.handle_error.assert_not_called()
                self.assertEqual(self.status()["pending_count"], 2)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.status()["pending_count"], 2)
        with db.connect() as conn:
            self.assertEqual(r.feedback_history(conn, self.latest), [])
            self.assertTrue(app.model_retrain_due(conn)[0])
        with patch.object(r, "train_personalized_model", return_value=self.artifact("second")) as train_again:
            self.assertEqual(app.run_model_retrain("feedback")["pending_count"], 0)
        train_again.assert_called_once()

    def test_failed_training_does_not_publish_or_clear_pending(self):
        self.activate("old")
        with db.connect() as conn:
            app.mark_model_retrain_pending(conn)
        with patch.object(r, "train_personalized_model", return_value={"signature": "new", "status": "failed"}):
            with self.assertRaises(p.PersonalizedModelUnavailable):
                app.run_model_retrain("feedback")
        self.assertEqual(self.status()["pending_count"], 1)
        with db.connect() as conn:
            self.assertEqual(db.get_setting(conn, "model_retrain_active_signature"), "old")

    def test_serving_snapshot_does_not_wait_for_training_lock(self):
        class NoTraining:
            def __enter__(self):
                raise AssertionError("serving acquired training lock")

        with patch.object(p, "_MODEL_LOCK", NoTraining()), \
                patch.object(p, "_train_personalized_model") as train, \
                patch.object(classification, "train_continuing_classifier") as classify:
            with db.connect() as conn:
                status = r.model_snapshot(conn)
        self.assertEqual(status["personalized"]["status"], "missing")
        self.assertEqual(status["continuing_classifier"]["status"], "missing")
        train.assert_not_called()
        classify.assert_not_called()

    def test_writer_and_separate_recommendation_reader_finish_while_training(self):
        self.activate("old")
        p._cache_model(self.artifact("old"))
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def train():
            with p._MODEL_LOCK:
                entered.set()
                release.wait(5)

        def read():
            try:
                with db.connect() as conn:
                    r.recommend_page(conn, low_interest_percent=0, low_interest_audit_daily_count=0)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        trainer = threading.Thread(target=train)
        trainer.start()
        reader = threading.Thread(target=read)
        try:
            self.assertTrue(entered.wait(3))
            # Settle membership writes before holding the writer slot.
            with db.connect() as conn:
                r.recommend_page(conn, low_interest_percent=0, low_interest_audit_daily_count=0)
            with db.connect() as writer:
                db.set_setting(writer, "writer", "held")
                r.recommend_page(writer, low_interest_percent=0, low_interest_audit_daily_count=0)
                reader.start()
                self.assertTrue(done.wait(2))
        finally:
            release.set()
            trainer.join(5)
            if reader.ident:
                reader.join(5)
        self.assertFalse(trainer.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(errors, [])

    def test_training_run_write_happens_after_training_lock_release(self):
        acquired = threading.Event()

        def record(conn, artifact):
            def acquire():
                with p._MODEL_LOCK:
                    acquired.set()
            reader = threading.Thread(target=acquire)
            reader.start()
            try:
                self.assertTrue(acquired.wait(2))
            finally:
                reader.join(3)
            conn.execute("INSERT INTO settings(key,value) VALUES ('audit_writer','ok')")

        for explicit in (True, False):
            with self.subTest(explicit=explicit), \
                    patch.object(p, "_train_personalized_model", return_value=self.artifact("fixture")), \
                    patch.object(p, "_record_training_run", side_effect=record):
                acquired.clear()
                with db.connect() as conn:
                    conn.execute("DELETE FROM settings WHERE key='audit_writer'")
                    if explicit:
                        p.train_personalized_model(conn)
                    else:
                        p.load_personalized_model(conn)

    def test_training_run_write_error_is_not_swallowed(self):
        conn = Mock()
        conn.execute.side_effect = sqlite3.OperationalError("fixture audit failed")
        with self.assertRaises(sqlite3.OperationalError):
            p._record_training_run(conn, self.artifact("fixture"))

    def test_failed_training_does_not_replace_cached_active_model(self):
        artifact = self.artifact("active", ready=True)
        p._cache_model(artifact)
        examples = [{"label": index % 2} for index in range(max(p.MIN_LABELED, 2 * p.MIN_CLASS))]
        with db.connect() as conn, patch.object(p, "sklearn_modules", side_effect=RuntimeError("fixture")):
            failed = p._train_personalized_model(conn, True, examples, "active")
        self.assertEqual(failed["status"], "failed")
        self.assertIs(p._MODEL_CACHE["active"], artifact)

    def test_read_only_classifier_status_keeps_existing_model_visible(self):
        with db.connect() as conn:
            signature = classification._signature(conn)
            classification._CACHE[signature] = {"ready": True, "accepted": False, "status": "shadow"}
            with patch.object(classification, "train_continuing_classifier") as train:
                status = r.model_snapshot(conn)
        self.assertEqual(status["continuing_classifier"]["status"], "shadow")
        train.assert_not_called()

    def test_versioned_artifact_recovery_uses_database_active_signature(self):
        modules = p.sklearn_modules()
        old, new = self.artifact("old"), self.artifact("new")
        modules["joblib"].dump(old, p.MODEL_PATH)
        self.activate("old")
        p._atomic_dump(new, modules)
        for signature in ("old", "new"):
            with self.subTest(signature=signature):
                self.activate(signature)
                p._MODEL_CACHE.clear()
                with db.connect() as conn:
                    loaded = p.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
                self.assertEqual(loaded["signature"], signature)
                self.assertEqual(loaded["status"], "insufficient-data")

    def test_missing_or_mismatched_active_artifact_never_selects_new_model(self):
        modules = p.sklearn_modules()
        p._atomic_dump(self.artifact("new"), modules)
        self.activate("old")
        with db.connect() as conn:
            loaded = p.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
        self.assertEqual(loaded, {"ready": False, "status": "missing", "signature": "old"})

    def test_artifact_publication_and_audit_failure_keep_old_active_after_restart(self):
        modules = p.sklearn_modules()
        p._atomic_dump(self.artifact("old"), modules)
        self.activate("old")
        with db.connect() as conn:
            app.mark_model_retrain_pending(conn)

        def compute(conn, **kwargs):
            artifact = self.artifact("new")
            p._atomic_dump(artifact, modules)
            p._cache_model(artifact)
            return artifact

        with patch.object(p, "_train_personalized_model", side_effect=compute), \
                patch.object(p, "_record_training_run", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertRaises(sqlite3.OperationalError):
                app.run_model_retrain("feedback")
        p._MODEL_CACHE.clear()
        with db.connect() as conn:
            loaded = p.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
        self.assertEqual(loaded["signature"], "old")
        self.assertEqual(self.status()["pending_count"], 1)

    def test_compatibility_file_failure_retains_old_active_version(self):
        modules = p.sklearn_modules()
        modules["joblib"].dump(self.artifact("old"), p.MODEL_PATH)
        self.activate("old")
        dump = p._dump_model_file

        def fail_compatibility(artifact, modules, path):
            if path == p.MODEL_PATH:
                raise OSError("fixture disk failure")
            dump(artifact, modules, path)

        with patch.object(p, "_dump_model_file", side_effect=fail_compatibility):
            with self.assertRaises(OSError):
                p._atomic_dump(self.artifact("new"), modules)
        p._MODEL_CACHE.clear()
        with db.connect() as conn:
            loaded = p.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
        self.assertEqual(loaded["signature"], "old")

    def test_failed_atomic_replace_never_loads_partial_candidate(self):
        modules = p.sklearn_modules()
        p._atomic_dump(self.artifact("old"), modules)
        self.activate("old")
        with patch.object(p.os, "replace", side_effect=OSError("fixture replace failure")):
            with self.assertRaises(OSError):
                p._atomic_dump(self.artifact("new"), modules)
        self.assertFalse(p._versioned_model_path("new").exists())
        self.assertTrue(list(p._versioned_model_path("new").parent.glob("personalized-*.joblib")))
        p._MODEL_CACHE.clear()
        with db.connect() as conn:
            loaded = p.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
        self.assertEqual(loaded["signature"], "old")

    def test_acceptance_refresh_does_not_mutate_published_cache(self):
        artifact = {**self.artifact("active", ready=True), "accepted": False}
        p._cache_model(artifact)

        def refresh(snapshot):
            snapshot["accepted"] = True
            return snapshot

        with patch.object(p, "_refresh_acceptance", side_effect=refresh):
            self.assertTrue(p._matching_model("active", True)["accepted"])
        self.assertFalse(p._MODEL_CACHE["active"]["accepted"])

    def test_fetch_setup_and_progress_failures_release_lock(self):
        for operation in (app.fetch_and_store, app.enrich_recommendations):
            for fault in ("connect", "missing-cookie", "progress"):
                with self.subTest(operation=operation.__name__, fault=fault), ExitStack() as stack:
                    with db.connect() as conn:
                        db.set_setting(conn, "cookie_header", "" if fault == "missing-cookie" else "synthetic")
                    if fault == "connect":
                        stack.enter_context(patch.object(db, "connect", side_effect=RuntimeError("fixture")))
                    stack.enter_context(patch.object(app, "update_fetch_progress", side_effect=RuntimeError("fixture")))
                    stack.enter_context(patch.object(app, "build_query_plan", return_value=[]))
                    with self.assertRaises((RuntimeError, app.ApiError)):
                        operation()
                    self.assertFalse(app.FETCH_LOCK.locked())
                    self.assertFalse(app.FETCH_STATE["running"])

    def test_fetch_query_plan_failure_releases_lock(self):
        with db.connect() as conn:
            db.set_setting(conn, "cookie_header", "synthetic")
        with patch.object(app, "build_query_plan", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                app.fetch_and_store()
        self.assertFalse(app.FETCH_LOCK.locked())

    def test_fetch_finalizer_failure_releases_lock_and_allows_next_run(self):
        with db.connect() as conn:
            db.set_setting(conn, "cookie_header", "synthetic")
        for operation in (app.fetch_and_store, app.enrich_recommendations):
            with self.subTest(operation=operation.__name__), \
                    patch.object(app, "build_query_plan", return_value=[]), \
                    patch.object(app, "select_recommendation_detail_candidates", return_value=[]), \
                    patch.object(app, "recommend_page", return_value={}), \
                    patch.object(app, "update_fetch_progress"), \
                    patch.object(app, "last_fetch_run", side_effect=RuntimeError("fixture cleanup")):
                with self.assertRaisesRegex(RuntimeError, "fixture cleanup"):
                    operation()
            self.assertFalse(app.FETCH_LOCK.locked())
            self.assertFalse(app.FETCH_STATE["running"])
            private = "_fetch_and_store" if operation == app.fetch_and_store else "_enrich_recommendations"
            with patch.object(app, private, return_value={"ok": True}):
                self.assertTrue(operation()["ok"])

    def test_failed_fetch_acquisition_does_not_release_another_owner(self):
        app.FETCH_LOCK.acquire()
        try:
            for operation in (app.fetch_and_store, app.enrich_recommendations):
                with self.assertRaises(app.ApiError):
                    operation()
                self.assertTrue(app.FETCH_LOCK.locked())
        finally:
            app.FETCH_LOCK.release()


class ClaimRetryTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="exh-claim-retry-"))
        self.path = self.directory / "jobs.sqlite3"
        self.queue = jobs.JobQueue(self.connect)
        self.queue.initialize()
        self.addCleanup(self.queue.shutdown)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def test_real_preclaim_busy_recovers_and_executes_once(self):
        job = self.queue.submit([{"kind": "fetch"}])
        busy, done = threading.Event(), threading.Event()
        executed = []
        claim = self.queue._claim

        def tracked_claim():
            try:
                return claim()
            except jobs._ClaimBusy:
                busy.set()
                raise

        with patch.object(self.queue, "_claim", side_effect=tracked_claim), \
                self.connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            # Avoid startup recovery writes so this specifically tests the claim loop.
            self.queue.executor = lambda *args: executed.append(args[0]) or done.set() or {}
            self.queue.worker = threading.Thread(target=self.queue._loop)
            self.queue.worker.start()
            self.assertTrue(busy.wait(3))
            self.assertFalse(self.queue.stop.is_set())
        self.assertTrue(done.wait(3))
        self.queue.shutdown()
        self.assertFalse(self.queue.worker.is_alive())
        self.assertEqual(executed, ["fetch"])
        self.assertEqual(self.queue.get(job["id"])["state"], "succeeded")

    def test_stop_interrupts_busy_backoff(self):
        busy = threading.Event()

        def claim():
            busy.set()
            raise jobs._ClaimBusy

        with patch.object(self.queue, "_claim", side_effect=claim) as calls:
            worker = threading.Thread(target=self.queue._claim_with_retry)
            worker.start()
            self.assertTrue(busy.wait(2))
            self.queue.stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls.call_count, 1)

    def test_exhausted_busy_retries_fail_closed_and_notify_waiters(self):
        waiter = {"event": threading.Event()}
        self.queue.waiters["synthetic"] = waiter
        with patch.object(self.queue, "_claim", side_effect=jobs._ClaimBusy) as claim, \
                patch.object(self.queue.stop, "wait", return_value=False), \
                patch.object(self.queue, "_execute") as execute:
            self.queue._loop()
        self.assertEqual(claim.call_count, jobs.CLAIM_BUSY_RETRIES + 1)
        self.assertTrue(self.queue.stop.is_set())
        self.assertTrue(waiter["event"].is_set())
        self.assertIsInstance(waiter["error"], jobs.JobError)
        execute.assert_not_called()

    def test_unclassified_operational_error_is_not_retried(self):
        with patch.object(self.queue, "_claim", side_effect=sqlite3.OperationalError("fixture")) as claim:
            with self.assertRaises(sqlite3.OperationalError):
                self.queue._claim_with_retry()
        claim.assert_called_once()

    def test_postclaim_busy_is_not_retried(self):
        error = sqlite3.OperationalError("fixture")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        conn = Mock()
        conn.execute.side_effect = [None, None, error]

        @contextmanager
        def connect():
            yield conn

        with patch.object(self.queue, "connect", connect), \
                patch.object(self.queue.stop, "wait") as wait:
            with self.assertRaises(sqlite3.OperationalError):
                self.queue._claim_with_retry()
        self.assertEqual(conn.execute.call_count, 3)
        wait.assert_not_called()

    def test_claim_commit_failure_is_not_retried(self):
        self.queue.submit([{"kind": "fetch"}])
        original = self.connect

        @contextmanager
        def failed_commit():
            with original() as conn:
                yield conn
                raise sqlite3.OperationalError("fixture commit uncertainty")

        with patch.object(self.queue, "connect", failed_commit), \
                patch.object(self.queue.stop, "wait") as wait:
            with self.assertRaises(sqlite3.OperationalError):
                self.queue._claim_with_retry()
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
