"""Prewarm tests use synthetic galleries, local images and retained fixtures only."""
from contextlib import ExitStack
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
import types
import sys
import unittest
from unittest.mock import Mock, patch

try:
    from PIL import Image
except ImportError:
    Image = None

from exh_rec import app, db, review_prewarm as warm, visual
from exh_rec.exhentai import Gallery
from exh_rec.jobs import JobHalt, JobQueue, result_summary, validate_steps
from exh_rec.recency import recent_gallery_cutoff
from exh_rec.recommender import store_galleries, store_gallery_samples
from scripts.launcher_jobs import validate_job


@unittest.skipIf(Image is None, "Pillow is required for prewarm image fixtures")
class PrewarmTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="review-prewarm-test-"))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(db, "DATA_DIR", self.root))
        self.stack.enter_context(patch.object(db, "DB_PATH", self.root / "synthetic.sqlite3"))
        db.init_db()
        self.image = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 100, 50)).save(self.image, format="PNG")
        self.blob = self.image.getvalue()
        self.download = self.stack.enter_context(patch.object(app, "fetch_thumbnail_bytes", side_effect=self.fetch))
        with db.connect() as conn:
            db.set_setting(conn, "cookie_header", "FAKE_COOKIE")
            db.set_setting(conn, "visual_encoder", "simple")
            db.set_setting(conn, "review_require_bootstrap_match", "0")
            db.set_setting(conn, "review_low_interest_percent", "0")
        self.jobs = JobQueue(lambda: db.connect())
        self.jobs.initialize()
        self.stack.enter_context(patch.object(app, "JOBS", self.jobs))
        self.stack.enter_context(patch.object(warm, "_SERVICE", None))

    def fetch(self, *_args, **_kwargs):
        # Every external stage must leave SQLite's writer slot free.
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            db.set_setting(conn, "synthetic_network_writer", "ok")
        return self.blob, "image/png"

    def gallery(self, index=1, *, sprite=False, detail=True, samples=True):
        gallery = Gallery(url=f"https://exhentai.org/g/{index}/abc/", gid=str(index), token="abc",
                          title=f"Synthetic {index}", thumb_url=f"https://ehgt.org/cover-{index}.png",
                          posted_at=recent_gallery_cutoff(), tags=["language:japanese"], page_count=20)
        entry = ({"url": f"https://ehgt.org/sheet-{index}.png", "x": 2, "y": 1, "w": 10, "h": 15}
                 if sprite else f"https://ehgt.org/sample-{index}.png")
        with db.connect() as conn:
            store_galleries(conn, [gallery], detail_fetched=detail)
            if samples:
                store_gallery_samples(conn, gallery.url, 20, [entry])
        return warm.load_item(gallery.url)

    def state(self, url):
        with db.connect() as conn:
            return dict(conn.execute("SELECT * FROM review_media_prewarm WHERE gallery_url=?", (url,)).fetchone())

    def test_warms_cover_sprite_crop_and_visual_without_impressions(self):
        item = self.gallery(sprite=True)
        result = warm.run_batch()
        self.assertEqual((result["ready"], result["images"], result["analyzed"]), (1, 2, 1))
        crop = app.read_cached_thumbnail(*app.thumbnail_cache_paths(warm.cache_key(item["samples"][0])),
                                         warm.cache_key(item["samples"][0]))
        self.assertEqual(Image.open(io.BytesIO(crop[0])).size, (10, 15))
        self.assertEqual(self.state(item["url"])["state"], "ready")
        self.assertEqual(warm.candidates(), [])
        self.assertEqual(warm.run_batch()["checked"], 0)
        self.assertEqual(self.download.call_count, 2)
        with db.connect() as conn:
            for table in ("feedback", "recommendation_impressions", "low_interest_audit_days", "low_interest_memberships"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM gallery_visual_images").fetchone()[0], 2)

    def test_selection_read_only_matches_serving_policy_without_freezing_audit_day(self):
        for index in range(1, 15):
            self.gallery(index)
        with db.connect() as conn:
            db.set_setting(conn, "review_low_interest_percent", "20")
            db.set_setting(conn, "review_low_interest_audit_enabled", "1")
        due = warm.candidates()
        self.assertGreater(len(due), 0)
        self.assertLess(len(due), 14)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM low_interest_memberships").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM low_interest_audit_days").fetchone()[0], 0)
        with patch.object(app, "recommendation_payload", wraps=app.recommendation_payload) as call:
            warm.candidates()
        self.assertEqual(call.call_args.kwargs["interest_band"], "primary")
        self.assertTrue(call.call_args.kwargs["selection_read_only"])

    def test_metadata_fetch_is_outside_transaction_and_fetch_lock_released(self):
        item = self.gallery(detail=False, samples=False)
        detailed = app.gallery_from_item(item)
        detailed.sample_thumbs = ["https://ehgt.org/extra.png"]
        def fetch_detail(*_args, **_kwargs):
            self.fetch()
            return detailed
        with patch.object(app, "fetch_gallery_detail", side_effect=fetch_detail), patch.object(app, "ensure_api_cover"):
            result = warm.run_batch()
        self.assertEqual(result["ready"], 1)
        self.assertFalse(app.FETCH_LOCK.locked())

    def test_cover_fallback_and_duplicate_samples_are_analyzed_once(self):
        item = self.gallery()
        with db.connect() as conn:
            conn.execute("UPDATE galleries SET thumb_url=NULL WHERE url=?", (item["url"],))
            store_gallery_samples(conn, item["url"], 20, [item["samples"][0], item["samples"][0]])
        self.assertEqual(warm.run_batch()["ready"], 1)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM gallery_visual_images").fetchone()[0], 1)

    def test_protocol_relative_sprite_url_uses_shared_crop_key(self):
        item = self.gallery(sprite=True)
        sample = dict(item["samples"][0], url="//ehgt.org/protocol-relative.png")
        with db.connect() as conn:
            store_gallery_samples(conn, item["url"], 20, [sample])
        self.assertEqual(warm.run_batch()["ready"], 1)
        self.assertEqual(warm.candidates(), [])

    def test_failures_cool_down_but_do_not_block_other_galleries(self):
        first = self.gallery(1)
        self.gallery(2)
        def download(_cookie, url, *_args, **_kwargs):
            if "-1." in url:
                raise RuntimeError("FAKE_SECRET_must_not_be_reported")
            return self.fetch()
        self.download.side_effect = download
        result = warm.run_batch()
        self.assertEqual((result["partial"], result["ready"]), (1, 1))
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        self.assertGreater(self.state(first["url"])["retry_after"], time.time())
        self.assertEqual(warm.candidates(), [])
        with db.connect() as conn:
            conn.execute("UPDATE review_media_prewarm SET retry_after=0 WHERE gallery_url=?", (first["url"],))
        self.assertEqual([row["url"] for row in warm.candidates()], [first["url"]])

    def test_sprite_fallback_is_not_mislabeled_ready(self):
        item = self.gallery(sprite=True)
        with patch.object(app, "crop_sprite_bytes", side_effect=app.SpriteCropUnavailable("unavailable")):
            self.assertEqual(warm.run_batch()["partial"], 1)
        self.assertNotEqual(self.state(item["url"])["state"], "ready")

    def test_changed_samples_or_encoder_invalidate_readiness(self):
        item = self.gallery()
        warm.run_batch()
        with db.connect() as conn:
            store_gallery_samples(conn, item["url"], 20, ["https://ehgt.org/replacement.png"])
        self.assertEqual(len(warm.candidates()), 1)
        warm.run_batch()
        with db.connect() as conn:
            db.set_setting(conn, "visual_encoder", "dinov2")
        self.assertEqual(len(warm.candidates()), 1)

    def test_cache_repair_does_not_recompute_current_analysis(self):
        item = self.gallery()
        warm.run_batch()
        cache, _ = app.thumbnail_cache_paths(item["thumb_url"])
        cache.write_bytes(b"incomplete")
        result = warm.run_batch()
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["analyzed"], 0)
        self.assertEqual(self.download.call_count, 3)

    def test_stale_media_refresh_is_bounded_and_cooldown_uses_new_manifest(self):
        item = self.gallery()
        self.download.side_effect = app.ApiError(app.HTTPStatus.BAD_GATEWAY, "Thumbnail fetch failed with HTTP 403")
        def refresh(current, _checkpoint):
            with db.connect() as conn:
                conn.execute("UPDATE galleries SET thumb_url=? WHERE url=?", ("https://ehgt.org/new.png", current["url"]))
            return warm.load_item(current["url"])
        with patch.object(warm, "refresh_detail", side_effect=refresh) as refresh_call:
            self.assertEqual(warm.run_batch()["partial"], 1)
        self.assertEqual(refresh_call.call_count, 1)
        self.assertEqual(warm.candidates(), [])

    def test_metadata_change_during_inference_never_publishes_stale_result(self):
        item = self.gallery()
        original = warm.simple_embeddings
        def encode(blobs):
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                store_gallery_samples(conn, item["url"], 20, ["https://ehgt.org/raced.png"])
            return original(blobs)
        with patch.object(warm, "simple_embeddings", side_effect=encode):
            self.assertEqual(warm.run_batch()["partial"], 1)
        self.assertFalse(warm.load_item(item["url"])["visual_embedding_at"])

    def test_old_ready_digest_is_not_rebound_to_changed_media_after_failure(self):
        item = self.gallery()
        warm.run_batch()
        with db.connect() as conn:
            store_gallery_samples(conn, item["url"], 20, ["https://ehgt.org/changed-before.png"])
        original = warm.simple_embeddings
        def encode(blobs):
            with db.connect() as conn:
                store_gallery_samples(conn, item["url"], 20, ["https://ehgt.org/changed-during.png"])
            return original(blobs)
        with patch.object(warm, "simple_embeddings", side_effect=encode):
            self.assertEqual(warm.run_batch()["partial"], 1)
        self.assertEqual(self.state(item["url"])["embedding_digest"], "")
        with db.connect() as conn:
            conn.execute("UPDATE review_media_prewarm SET retry_after=0")
        self.assertEqual(warm.run_batch()["analyzed"], 1)

    def test_database_errors_fail_closed_without_replaying_batch(self):
        self.gallery()
        with patch.object(warm, "prepare_gallery", side_effect=sqlite3.OperationalError("synthetic busy")) as prepare:
            with self.assertRaises(sqlite3.OperationalError):
                warm.run_batch()
        self.assertEqual(prepare.call_count, 1)

    def test_batch_bound_and_disable(self):
        for index in range(1, 7):
            self.gallery(index)
        self.assertEqual(warm.run_batch()["checked"], warm.BATCH_SIZE)
        with db.connect() as conn:
            db.set_setting(conn, "review_prewarm_enabled", "0")
        self.assertEqual(warm.run_batch(automatic=True)["checked"], 0)
        self.assertEqual(warm.run_batch()["checked"], warm.BATCH_SIZE)

    def test_all_samples_warm_but_inference_keeps_a_bounded_image_batch(self):
        item = self.gallery()
        with db.connect() as conn:
            store_gallery_samples(conn, item["url"], 20, [f"https://ehgt.org/page-{index}.png" for index in range(15)])
        with patch.object(warm, "simple_embeddings", wraps=warm.simple_embeddings) as encode:
            result = warm.run_batch()
        self.assertEqual(result["images"], 16)
        self.assertEqual(len(encode.call_args.args[0]), 11)
        self.assertEqual(warm.candidates(), [])

    def test_cancel_does_not_get_swallowed_as_media_failure(self):
        self.gallery()
        def checkpoint(stage):
            if stage == "media":
                raise JobHalt("cancelled")
        with self.assertRaises(JobHalt):
            warm.run_batch(checkpoint)

    def test_dino_prewarm_is_local_only_and_does_not_fallback_to_simple(self):
        item = self.gallery()
        with db.connect() as conn:
            db.set_setting(conn, "visual_encoder", "dinov2")
        with patch.object(warm, "dinov2_image_embeddings", side_effect=visual.VisualEncoderUnavailable("missing")) as encode:
            self.assertEqual(warm.run_batch()["partial"], 1)
        self.assertTrue(encode.call_args.kwargs["local_files_only"])
        self.assertFalse(warm.load_item(item["url"])["visual_embedding_at"])

    def test_settings_readback_and_window_bounds(self):
        app.save_settings({"review_prewarm_enabled": False, "review_prewarm_window": 999})
        observed = app.get_settings()["review_prewarm"]
        self.assertFalse(observed["enabled"])
        self.assertEqual(observed["window"], 400)
        with db.connect() as conn:
            db.set_setting(conn, "review_prewarm_window", "bad")
            self.assertEqual(warm.settings(conn)["window"], 120)

    def test_thumbnail_singleflight_and_atomic_cache_readback(self):
        item = self.gallery()
        entered, release = threading.Event(), threading.Event()
        def fetch(*_args, **_kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("synthetic timeout")
            return self.blob, "image/png"
        self.download.side_effect = fetch
        values, errors = [], []
        def read():
            try:
                values.append(app.cached_thumbnail(item["thumb_url"], item["url"]))
            except Exception as exc:
                errors.append(type(exc).__name__)
        threads = [threading.Thread(target=read) for _ in range(3)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(2))
        release.set()
        for thread in threads:
            thread.join(4)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(values), 3)
        self.assertEqual(self.download.call_count, 1)
        data, meta = app.thumbnail_cache_paths(item["thumb_url"])
        self.assertEqual(json.loads(meta.read_text())["size"], len(data.read_bytes()))

    def test_job_priority_and_launcher_result_validation(self):
        prewarm = self.jobs.submit([{"kind": "review_prewarm"}], source="background")
        normal = self.jobs.submit([{"kind": "backup"}])
        self.assertEqual(self.jobs._claim()["id"], normal["id"])
        public = self.jobs.get(prewarm["id"])
        public["steps"][0]["result"] = result_summary({"ready": 2, "images": 12, "errors": ["redacted"]})
        self.assertEqual(validate_job(public)["steps"][0]["result"], {"ready": 2, "images": 12, "errors": 1})
        with self.assertRaises(ValueError):
            validate_steps([{"kind": "review_prewarm", "params": {"limit": 999}}])

    def test_scheduler_coalesces_and_honors_disable(self):
        self.gallery()
        with patch.object(JobQueue, "running", new_callable=unittest.mock.PropertyMock, return_value=True):
            self.assertTrue(warm.schedule_once())
            self.assertTrue(warm.schedule_once())
            self.assertEqual(len(self.jobs.snapshot()["jobs"]), 1)
            with db.connect() as conn:
                db.set_setting(conn, "review_prewarm_enabled", "0")
            self.assertFalse(warm.schedule_once())

    def test_module_entrypoint_uses_bound_live_queue_not_a_second_import(self):
        queue = types.SimpleNamespace(running=True, submit=Mock())
        warm.bind_service({"JOBS": queue})
        self.assertIs(warm.service().JOBS, queue)
        self.assertFalse(app.JOBS.running)
        with patch.object(warm, "candidates", return_value=[{"url": "synthetic"}]):
            self.assertTrue(warm.schedule_once())
        queue.submit.assert_called_once_with([{"kind": "review_prewarm"}], source="background", coalesce=True)

    def test_scheduler_failure_has_fixed_status_and_bounded_retry(self):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        stop.is_set.return_value = False
        with patch.object(warm, "schedule_once", side_effect=RuntimeError("FAKE_SECRET")):
            warm.background(stop)
        self.assertEqual(warm.STATUS["last_error_code"], "scheduler_failed")
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [30, 30])
        warm.STATUS["last_error_code"] = ""


class PrewarmEncoderTest(unittest.TestCase):
    def test_local_only_load_does_not_download_missing_model(self):
        processor = Mock()
        processor.from_pretrained.side_effect = OSError("synthetic cache miss")
        fake_transformers = types.SimpleNamespace(AutoImageProcessor=processor, AutoModel=Mock())
        with patch.dict(visual._DINO_STATE, {}, clear=True), patch.dict(sys.modules, {
                "torch": types.SimpleNamespace(), "transformers": fake_transformers}), \
                patch.object(visual, "_snapshot_download_dinov2") as download:
            with self.assertRaises(visual.VisualEncoderUnavailable):
                visual.load_dinov2("cpu", local_files_only=True)
            download.assert_not_called()
            processor.from_pretrained.assert_called_once_with(visual.DINOV2_MODEL_NAME, local_files_only=True)

    def test_foreground_and_background_inference_are_serialized(self):
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []
        def encode(blobs, _device, **_kwargs):
            calls.append(blobs)
            entered.set()
            if not release.wait(3):
                raise RuntimeError("synthetic timeout")
            return [[1.0]]
        def run():
            try:
                visual.dinov2_image_embeddings([b"synthetic"])
            except Exception as exc:
                errors.append(type(exc).__name__)
        with patch.object(visual, "_dinov2_image_embeddings", side_effect=encode):
            first, second = threading.Thread(target=run), threading.Thread(target=run)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            time.sleep(0.05)
            self.assertEqual(len(calls), 1)
            release.set()
            first.join(4)
            second.join(4)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
