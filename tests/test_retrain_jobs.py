import tempfile
import threading
import unittest
from contextlib import ExitStack
from http import HTTPStatus
from pathlib import Path
from unittest.mock import Mock, patch

from exh_rec import app, db, recommender
from exh_rec.audit import prepare_daily_low_interest_audits
from exh_rec.exhentai import Gallery
from test_hath import completed_event


class RetrainJobsTest(unittest.TestCase):
    def setUp(self):
        # Retain fixtures rather than deleting them under the workspace policy.
        self.directory = Path(tempfile.mkdtemp(prefix="exh-rec-retrain-jobs-"))
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(db, "DATA_DIR", self.directory))
        self.patches.enter_context(patch.object(db, "DB_PATH", self.directory / "test.sqlite3"))
        self.patches.enter_context(patch.object(app, "MODEL_RETRAIN_STATE", {}))
        self.patches.enter_context(patch.object(app, "MODEL_RETRAIN_WAKE", threading.Event()))
        self.patches.enter_context(patch.object(app, "MODEL_RETRAIN_RUN_LOCK", threading.Lock()))
        db.init_db()

    def settings(self, **values):
        with db.connect() as conn:
            for key, value in values.items():
                db.set_setting(conn, "model_retrain_" + key, str(value))

    def status(self):
        with db.connect() as conn:
            return app.model_retrain_status(conn)

    def assert_claim_cleared(self):
        with db.connect() as conn:
            self.assertEqual(db.get_setting(conn, "model_retrain_claimed_count", "0"), "0")
            self.assertEqual(db.get_setting(conn, "model_retrain_claimed_since", ""), "")
            self.assertEqual(db.get_setting(conn, "model_retrain_claimed_manual", "0"), "0")

    def test_manual_requests_are_durable_and_coalesced(self):
        self.settings(pending_count=4, pending_since="2026-01-01 00:00:00")

        with patch.object(app, "retrain_model") as train:
            first = app.request_model_retrain()
            second = app.request_model_retrain()

        train.assert_not_called()
        self.assertEqual(first["pending_count"], 4)
        self.assertEqual(second["pending_count"], 4)
        self.assertTrue(second["manual_requested"])
        self.assertTrue(app.MODEL_RETRAIN_WAKE.is_set())
        # A fresh connection and fresh process state retain the request.
        app.MODEL_RETRAIN_STATE.clear()
        persisted = self.status()
        self.assertTrue(persisted["manual_requested"])
        self.assertEqual(persisted["pending_since"], "2026-01-01 00:00:00")

    def test_manual_request_with_no_feedback_creates_one_pending_unit(self):
        app.request_model_retrain()
        app.request_model_retrain()

        self.assertEqual(self.status()["pending_count"], 1)
        self.assertTrue(self.status()["manual_requested"])

    def test_explicit_manual_request_bypasses_manual_schedule(self):
        self.settings(mode="manual", pending_count=2)
        with db.connect() as conn:
            self.assertFalse(app.model_retrain_due(conn)[0])

        app.request_model_retrain()

        with db.connect() as conn:
            self.assertEqual(app.model_retrain_due(conn), (True, "manual", 0.0))
        with patch.object(app, "retrain_model") as train:
            result = app.run_model_retrain("manual")
        train.assert_called_once()
        self.assertEqual(train.call_args.kwargs, {"release_write_lock": True})
        self.assertFalse(result["manual_requested"])
        self.assertEqual(result["pending_count"], 0)
        self.assert_claim_cleared()

    def test_crash_recovery_merges_claim_once_and_keeps_oldest_pending_time(self):
        self.settings(
            pending_count=2,
            pending_since="2026-02-01 00:00:00",
            claimed_count=5,
            claimed_since="2026-01-01 00:00:00",
            claimed_manual=1,
        )

        with db.connect() as conn:
            app.recover_model_retrain_claim(conn)
        with db.connect() as conn:
            app.recover_model_retrain_claim(conn)

        recovered = self.status()
        self.assertEqual(recovered["pending_count"], 7)
        self.assertEqual(recovered["pending_since"], "2026-01-01 00:00:00")
        self.assertTrue(recovered["manual_requested"])
        self.assert_claim_cleared()

    def test_crash_recovery_preserves_manual_claim_without_feedback_count(self):
        self.settings(mode="manual", claimed_count=0, claimed_manual=1)

        with db.connect() as conn:
            app.recover_model_retrain_claim(conn)
        with db.connect() as conn:
            self.assertEqual(app.model_retrain_due(conn), (True, "manual", 0.0))
        self.assert_claim_cleared()

    def test_success_preserves_feedback_committed_during_training(self):
        self.settings(pending_count=5, pending_since="2026-01-01 00:00:00")

        def train(_conn, *, release_write_lock):
            self.assertTrue(release_write_lock)
            claimed = self.status()
            self.assertTrue(claimed["running"])
            self.assertEqual(claimed["claimed_count"], 5)
            self.assertEqual(claimed["pending_count"], 0)
            # Another request writes through an independent DB connection.
            with db.connect() as conn:
                app.mark_model_retrain_pending(conn)
                app.mark_model_retrain_pending(conn)

        with patch.object(app, "retrain_model", side_effect=train):
            result = app.run_model_retrain("threshold")

        self.assertEqual(result["pending_count"], 2)
        self.assertFalse(result["running"])
        self.assertIsNotNone(result["last_completed_at"])
        self.assertIsNone(result["last_error"])
        self.assert_claim_cleared()

    def test_success_preserves_new_manual_request_for_next_run(self):
        app.request_model_retrain()

        def train(_conn, *, release_write_lock):
            app.request_model_retrain()
            app.request_model_retrain()

        with patch.object(app, "retrain_model", side_effect=train):
            result = app.run_model_retrain("manual")

        self.assertTrue(result["manual_requested"])
        self.assertEqual(result["pending_count"], 1)
        with db.connect() as conn:
            self.assertEqual(app.model_retrain_due(conn), (True, "manual", 0.0))
        self.assert_claim_cleared()

    def test_failure_restores_claim_and_retains_new_pending_feedback(self):
        self.settings(pending_count=5, pending_since="2026-01-01 00:00:00")
        app.request_model_retrain()

        def train(_conn, *, release_write_lock):
            with db.connect() as conn:
                app.mark_model_retrain_pending(conn)
                app.mark_model_retrain_pending(conn)
            raise RuntimeError("training fixture failed")

        with patch.object(app, "retrain_model", side_effect=train):
            with self.assertRaisesRegex(RuntimeError, "training fixture failed"):
                app.run_model_retrain("manual")

        result = self.status()
        self.assertEqual(result["pending_count"], 7)
        self.assertEqual(result["pending_since"], "2026-01-01 00:00:00")
        self.assertTrue(result["manual_requested"])
        self.assertFalse(result["running"])
        self.assertIn("pending work retained", result["last_error"])
        self.assertNotIn("training fixture failed", result["last_error"])
        self.assert_claim_cleared()
        with db.connect() as conn:
            app.recover_model_retrain_claim(conn)
        self.assertEqual(self.status()["pending_count"], 7)
        # A failed run must release its lock so an explicit retry can succeed.
        with patch.object(app, "retrain_model"):
            retried = app.run_model_retrain("manual")
        self.assertEqual(retried["pending_count"], 0)
        self.assertIsNone(retried["last_error"])

    def test_empty_queue_does_not_train(self):
        with patch.object(app, "retrain_model") as train:
            result = app.run_model_retrain("threshold")
        train.assert_not_called()
        self.assertEqual(result["pending_count"], 0)
        self.assert_claim_cleared()

    def test_busy_worker_does_not_consume_queued_request(self):
        app.request_model_retrain()
        app.MODEL_RETRAIN_RUN_LOCK.acquire()
        try:
            with patch.object(app, "retrain_model") as train:
                with self.assertRaises(app.ApiError) as caught:
                    app.run_model_retrain("manual")
            train.assert_not_called()
            self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
            self.assertTrue(self.status()["manual_requested"])
            self.assertEqual(self.status()["pending_count"], 1)
        finally:
            app.MODEL_RETRAIN_RUN_LOCK.release()

    def test_http_retrain_queues_and_returns_202_without_inline_training(self):
        self.settings(mode="manual")
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/retrain"
        handler.read_json = Mock(return_value={})
        handler.send_json = Mock()

        with patch.object(app, "retrain_model") as train, patch.object(app, "run_model_retrain") as run:
            handler.do_POST()

        train.assert_not_called()
        run.assert_not_called()
        handler.send_json.assert_called_once()
        payload = handler.send_json.call_args.args[0]
        self.assertEqual(handler.send_json.call_args.kwargs["status"], HTTPStatus.ACCEPTED)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["queued"])
        self.assertTrue(payload["model_retrain"]["manual_requested"])
        self.assertTrue(self.status()["manual_requested"])

    def hath_handler(self, payload=None):
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/integrations/hath/events"
        handler.headers = {}
        handler.read_json = Mock(return_value=payload or completed_event())
        handler.send_json = Mock()
        handler.handle_error = Mock()
        return handler

    def seed_galleries(self):
        with db.connect() as conn:
            recommender.store_galleries(conn, [
                Gallery(url=f"https://exhentai.org/g/{123 + i}/abc/", gid=str(123 + i),
                        token="abc", title=f"Fixture {i}", tags=["artist:fixture", f"tag:{i}"])
                for i in range(10)
            ])

    def test_hath_completion_queues_atomically_and_wakes_only_after_commit(self):
        self.settings(mode="manual")
        handler = self.hath_handler()

        def verify_committed():
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM hath_events").fetchone()[0], 1)
                self.assertEqual(app.model_retrain_pending_count(conn), 1)

        with patch.object(app, "require_hath_authorization"), patch.object(app, "retrain_model") as train, \
                patch.object(app.MODEL_RETRAIN_WAKE, "set", side_effect=verify_committed) as wake:
            handler.do_POST()
            handler.do_POST()

        handler.handle_error.assert_not_called()
        train.assert_not_called()
        wake.assert_called_once()
        self.assertEqual(self.status()["pending_count"], 1)
        self.assertFalse(self.status()["manual_requested"])
        with db.connect() as conn:
            self.assertFalse(app.model_retrain_due(conn)[0])
        self.assertEqual(handler.send_json.call_args.args[0]["duplicates"], 1)

    def test_hath_queue_failure_rolls_back_event_and_does_not_wake(self):
        handler = self.hath_handler()
        with patch.object(app, "require_hath_authorization"), \
                patch.object(app, "mark_model_retrain_pending", side_effect=RuntimeError("fixture")):
            handler.do_POST()
        handler.handle_error.assert_called_once()
        handler.send_json.assert_not_called()
        self.assertFalse(app.MODEL_RETRAIN_WAKE.is_set())
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM hath_events").fetchone()[0], 0)

    def test_hath_completion_respects_immediate_and_batched_schedules(self):
        for mode, pending in [("immediate", 0), ("batched", 9)]:
            with self.subTest(mode=mode):
                self.settings(mode=mode, pending_count=pending)
                handler = self.hath_handler(completed_event(event_id=mode, gid="123" if mode == "immediate" else "124"))
                with patch.object(app, "require_hath_authorization"), patch.object(app, "retrain_model") as train:
                    handler.do_POST()
                handler.handle_error.assert_not_called()
                train.assert_not_called()
                with db.connect() as conn:
                    self.assertTrue(app.model_retrain_due(conn)[0])

    def test_hath_progress_does_not_queue_training(self):
        payload = completed_event()
        payload["events"][0]["type"] = "download.progress"
        handler = self.hath_handler(payload)
        with patch.object(app, "require_hath_authorization"):
            handler.do_POST()
        handler.handle_error.assert_not_called()
        self.assertEqual(self.status()["pending_count"], 0)
        self.assertFalse(app.MODEL_RETRAIN_WAKE.is_set())

    def test_hath_and_feedback_commit_while_real_retrain_is_running(self):
        self.seed_galleries()
        self.settings(pending_count=2)
        training, release = threading.Event(), threading.Event()
        errors = []

        def slow_train(conn):
            self.assertFalse(conn.in_transaction)
            training.set()
            if not release.wait(5):
                raise RuntimeError("fixture timeout")
            return {"signature": "fixture"}

        def run():
            try:
                app.run_model_retrain("threshold")
            except Exception as exc:
                errors.append(exc)

        with patch.object(recommender, "train_personalized_model", side_effect=slow_train), \
                patch.object(app, "require_hath_authorization"):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(training.wait(5))
                handler = self.hath_handler()
                handler.do_POST()
                handler.handle_error.assert_not_called()
                handler.send_json.assert_called_once()
                with db.connect() as conn:
                    recommender.record_feedback(conn, "https://exhentai.org/g/124/abc/", vote=1, retrain=False)
                    app.mark_model_retrain_pending(conn)
                self.assertEqual(self.status()["pending_count"], 2)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.status()["pending_count"], 2)
        self.assert_claim_cleared()

    def test_runtime_retrain_commits_before_waiting_for_model_lock(self):
        waiting, release = threading.Event(), threading.Event()
        errors = []

        class HeldModelLock:
            def __enter__(self):
                waiting.set()
                if not release.wait(5):
                    raise RuntimeError("fixture timeout")

            def __exit__(self, *_):
                pass

        def run():
            try:
                with db.connect() as conn:
                    db.set_setting(conn, "fixture_caller", "committed")
                    recommender.retrain_model(conn, release_write_lock=True)
            except Exception as exc:
                errors.append(exc)

        with patch.object(recommender, "RETRAIN_LOCK", HeldModelLock()), \
                patch.object(recommender, "train_personalized_model", return_value={"signature": "fixture"}):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(waiting.wait(5))
                with db.connect() as conn:
                    conn.execute("PRAGMA busy_timeout=250")
                    db.set_setting(conn, "fixture_other_writer", "ok")
                    self.assertEqual(db.get_setting(conn, "fixture_caller"), "committed")
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_runtime_rebuild_matches_transactional_weights_and_counts(self):
        self.seed_galleries()
        with db.connect() as conn:
            for vote in [1, 1, -1]:
                recommender.record_feedback(conn, "https://exhentai.org/g/124/abc/", vote=vote, retrain=False)
            recommender.record_feedback(conn, "https://exhentai.org/g/125/abc/", score=2, retrain=False)
            recommender.record_gallery_mark(conn, "https://exhentai.org/g/126/abc/", "favorite", retrain=False)
            recommender.record_gallery_mark(conn, "https://exhentai.org/g/127/abc/", "ban", retrain=False)
            app.ingest_event_batch(conn, completed_event())
        with patch.object(recommender, "train_personalized_model", return_value={"signature": "fixture"}):
            with db.connect() as conn:
                recommender.retrain_model(conn)
                expected = [tuple(row) for row in conn.execute(
                    "SELECT feature,weight,positive_count,negative_count FROM feature_weights ORDER BY feature")]
            with db.connect() as conn:
                recommender.retrain_model(conn, release_write_lock=True)
                actual = [tuple(row) for row in conn.execute(
                    "SELECT feature,weight,positive_count,negative_count FROM feature_weights ORDER BY feature")]
        self.assertTrue(expected)
        self.assertEqual(actual, expected)

    def test_runtime_weight_computation_does_not_own_writer_slot(self):
        self.seed_galleries()
        with db.connect() as conn:
            recommender.record_feedback(conn, "https://exhentai.org/g/124/abc/", vote=1, retrain=False)
        original = recommender.feedback_feature_updates

        def compute(*args, **kwargs):
            with db.connect() as other:
                other.execute("PRAGMA busy_timeout=250")
                db.set_setting(other, "fixture_during_compute", "ok")
            yield from original(*args, **kwargs)

        with patch.object(recommender, "feedback_feature_updates", side_effect=compute), \
                patch.object(recommender, "train_personalized_model", return_value={"signature": "fixture"}):
            with db.connect() as conn:
                recommender.retrain_model(conn, release_write_lock=True)
        with db.connect() as conn:
            self.assertEqual(db.get_setting(conn, "fixture_during_compute"), "ok")

    def test_settled_review_reads_do_not_need_writer_slot(self):
        self.seed_galleries()
        options = dict(model_mode="legacy", low_interest_percent=20, low_interest_audit_daily_count=1)
        with db.connect() as conn:
            first = recommender.recommend_page(conn, **options)
        with db.connect() as writer:
            db.set_setting(writer, "fixture_held_writer", "ok")
            with db.connect() as reader:
                reader.execute("PRAGMA busy_timeout=250")
                second = recommender.recommend_page(reader, **options)
                self.assertFalse(reader.in_transaction)
                self.assertEqual(reader.total_changes, 0)
        self.assertEqual([item["url"] for item in first["items"]], [item["url"] for item in second["items"]])

    def test_concurrent_first_daily_audit_freezes_one_sample(self):
        self.seed_galleries()
        barrier = threading.Barrier(2)
        results, errors = [], []

        def run():
            try:
                scored = [{"url": f"https://exhentai.org/g/{123 + i}/abc/",
                           "low_interest_fallback": True, "score": i} for i in range(10)]
                with db.connect() as conn:
                    barrier.wait(timeout=5)
                    prepare_daily_low_interest_audits(conn, scored, 2, audit_date="2026-11-12")
                results.append({item["audit_id"] for item in scored if item.get("audit_id")})
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]), 2)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM low_interest_audit_days").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM low_interest_audits").fetchone()[0], 2)

    def test_review_http_commits_before_sending_response(self):
        for route, target in [("/api/queue-counts", "queue_counts_payload"),
                              ("/api/recommendations", "recommendation_payload")]:
            with self.subTest(route=route):
                handler = app.Handler.__new__(app.Handler)
                handler.path = route
                handler.headers = {}
                handler.handle_error = Mock()

                def payload(conn, **_):
                    db.set_setting(conn, "fixture_before_response", route)
                    return {"ok": True}

                def send(_):
                    with db.connect() as conn:
                        conn.execute("PRAGMA busy_timeout=250")
                        self.assertEqual(db.get_setting(conn, "fixture_before_response"), route)
                        db.set_setting(conn, "fixture_during_response", route)

                handler.send_json = Mock(side_effect=send)
                with patch.object(app, target, side_effect=payload):
                    handler.do_GET()
                handler.handle_error.assert_not_called()
                handler.send_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
