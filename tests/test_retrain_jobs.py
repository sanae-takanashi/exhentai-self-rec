import tempfile
import threading
import unittest
from contextlib import ExitStack
from http import HTTPStatus
from pathlib import Path
from unittest.mock import Mock, patch

from exh_rec import app, db


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


if __name__ == "__main__":
    unittest.main()
