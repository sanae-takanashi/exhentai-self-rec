import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from exh_rec import db, personalized, recommender
from exh_rec.exhentai import Gallery


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="exh-rec-cache-test-")) / "fixture.sqlite3"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        recommender.store_galleries(self.conn, [
            Gallery(url=f"https://example.test/g/{i}/a/", gid=str(i), token="a",
                    title=f"Gallery {i}", tags=["artist:one", "language:japanese"])
            for i in range(12)
        ])
        self.conn.commit()
        self.classifier = patch("exh_rec.recommender.continuing_classifier_decisions", return_value=({}, {"ready": False}))
        self.predict = patch("exh_rec.recommender.score_personalized_galleries", return_value=([], {"ready": False}))
        self.classifier.start()
        self.predict.start()
        self.addCleanup(self.classifier.stop)
        self.addCleanup(self.predict.stop)

    def tearDown(self):
        self.conn.close()

    def page(self, **kwargs):
        return recommender.recommend_page(self.conn, limit=2, **kwargs)

    def test_revision_detects_same_length_update_and_rollback(self):
        before = db.cache_revision(self.conn, ("galleries",))
        self.conn.execute("UPDATE galleries SET title='Changed 0' WHERE gid='0'")
        self.assertIsNone(db.cache_revision(self.conn, ("galleries",)))
        self.conn.rollback()
        self.assertEqual(before, db.cache_revision(self.conn, ("galleries",)))
        self.conn.execute("UPDATE galleries SET title='Changed 0' WHERE gid='0'")
        self.conn.commit()
        self.assertNotEqual(before, db.cache_revision(self.conn, ("galleries",)))

    def test_pages_and_count_share_scores_without_mutating_snapshot(self):
        with patch.object(recommender, "_score_recommendation_candidates", wraps=recommender._score_recommendation_candidates) as score:
            first = self.page()
            original_title = first["items"][0]["title"]
            first["items"][0]["title"] = "caller mutation"
            first["items"][0]["tags"].append("artist:mutated")
            self.conn.commit()
            again = self.page()
            self.conn.commit()
            self.page(offset=2)
            self.conn.commit()
            counts = self.page(count_only=True)
            self.assertEqual(score.call_count, 1)
            self.assertEqual(again["items"][0]["title"], original_title)
            self.assertNotIn("artist:mutated", again["items"][0]["tags"])
            self.assertEqual(counts["overall_total"], first["overall_total"])
            self.assertEqual(counts["primary_total"], first["primary_total"])

    def test_committed_feedback_immediately_excludes_cached_gallery(self):
        first = self.page()
        self.conn.commit()
        url = first["items"][0]["url"]
        self.conn.execute("INSERT INTO feedback(gallery_url,vote) VALUES (?,1)", (url,))
        self.conn.commit()
        result = self.page()
        self.assertNotIn(url, [item["url"] for item in result["items"]])
        self.assertEqual(result["overall_total"], first["overall_total"] - 1)

    def test_rolled_back_candidate_scores_never_poison_shared_cache(self):
        first = self.page()
        self.conn.commit()
        self.conn.execute("UPDATE galleries SET review_excluded=1 WHERE gid='0'")
        uncommitted = self.page()
        self.assertEqual(uncommitted["overall_total"], first["overall_total"] - 1)
        self.conn.rollback()
        self.conn.execute("UPDATE galleries SET title='Different' WHERE gid='1'")
        self.conn.commit()
        self.assertEqual(self.page()["overall_total"], first["overall_total"])

    def test_cross_connection_and_delete_invalidation(self):
        with patch.object(recommender, "_score_recommendation_candidates", wraps=recommender._score_recommendation_candidates) as score:
            self.page()
            self.conn.commit()
            other = sqlite3.connect(self.path)
            try:
                other.execute("UPDATE galleries SET title='Changed 0' WHERE gid='0'")
                other.commit()
                self.page()
                self.conn.commit()
                other.execute("DELETE FROM galleries WHERE gid='0'")
                other.commit()
                self.assertEqual(self.page()["overall_total"], 11)
                self.assertEqual(score.call_count, 3)
            finally:
                other.close()

    def test_settings_bootstrap_classification_and_marks_invalidate(self):
        mutations = [
            ("INSERT INTO settings(key,value) VALUES ('recommend_model_mode','legacy')", ()),
            ("INSERT INTO bootstrap_tags(tag,weight) VALUES ('artist:one',2)", ()),
            ("INSERT INTO gallery_classification_overrides(gallery_url,classification) VALUES (?,'review')", ("https://example.test/g/0/a/",)),
            ("INSERT INTO gallery_marks(gallery_url,kind) VALUES (?,'favorite')", ("https://example.test/g/1/a/",)),
            ("UPDATE galleries SET tags_json=? WHERE gid='2'", (json.dumps(["artist:two"]),)),
        ]
        with patch.object(recommender, "_score_recommendation_candidates", wraps=recommender._score_recommendation_candidates) as score:
            self.page()
            self.conn.commit()
            for sql, args in mutations:
                self.conn.execute(sql, args)
                self.conn.commit()
                self.page()
                self.conn.commit()
            self.assertEqual(score.call_count, len(mutations) + 1)

    def test_count_only_skips_diversification_and_preserves_totals(self):
        full = self.page(low_interest_audit_daily_count=6)
        self.conn.commit()
        with patch.object(recommender, "diversify_ranked_galleries", side_effect=AssertionError("count ranked")):
            counts = self.page(count_only=True, low_interest_audit_daily_count=6)
        for field in ("total", "overall_total", "primary_total", "low_interest_total"):
            self.assertEqual(full[field], counts[field])
        self.assertEqual(counts["items"], [])
        self.assertEqual(full["low_interest_audit"], counts["low_interest_audit"])

    def test_filters_have_separate_candidate_cache_keys(self):
        with patch.object(recommender, "_score_recommendation_candidates", wraps=recommender._score_recommendation_candidates) as score:
            self.page(language_filter="japanese")
            self.conn.commit()
            self.assertEqual(self.page(language_filter="chinese")["total"], 0)
            self.conn.commit()
            self.page(model_mode="legacy")
            self.assertEqual(score.call_count, 3)

    def test_model_and_evaluation_file_replacement_invalidates_scores(self):
        model_path = self.path.parent / "model.joblib"
        evaluation_path = self.path.parent / "evaluation.json"
        with patch.object(personalized, "MODEL_PATH", model_path), patch.object(personalized, "EVALUATION_PATH", evaluation_path), patch.object(
            recommender, "_score_recommendation_candidates", wraps=recommender._score_recommendation_candidates
        ) as score:
            self.page()
            self.conn.commit()
            for path in (model_path, evaluation_path):
                path.write_bytes(b"fixture")
                self.page()
                self.conn.commit()
                version = path.stat().st_mtime_ns
                os.utime(path, ns=(version, version + 1_000_000_000))
                self.page()
                self.conn.commit()
            self.assertEqual(score.call_count, 5)

    def test_completed_download_immediately_excludes_cached_gallery(self):
        first = self.page()
        self.conn.commit()
        url = first["items"][0]["url"]
        self.conn.execute(
            "INSERT INTO hath_downloads(client_id,gid,gallery_url,status,updated_at) "
            "VALUES ('fixture','1',?,'completed',CURRENT_TIMESTAMP)",
            (url,),
        )
        self.conn.commit()
        result = self.page()
        self.assertNotIn(url, [item["url"] for item in result["items"]])
        self.assertEqual(result["overall_total"], first["overall_total"] - 1)

    def test_simultaneous_list_and_count_share_one_score_build(self):
        original = recommender._score_recommendation_candidates
        start = threading.Barrier(2)

        def build(*args, **kwargs):
            time.sleep(0.05)
            return original(*args, **kwargs)

        def request(count_only):
            conn = sqlite3.connect(self.path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                start.wait(timeout=5)
                result = recommender.recommend_page(conn, limit=2, count_only=count_only, low_interest_percent=0)
                conn.commit()
                return result
            finally:
                conn.close()

        with patch.object(recommender, "_score_recommendation_candidates", side_effect=build) as score:
            with ThreadPoolExecutor(max_workers=2) as pool:
                listing, count = list(pool.map(request, (False, True)))
        self.assertEqual(score.call_count, 1)
        self.assertEqual(listing["overall_total"], count["overall_total"])
        self.assertEqual(len(listing["items"]), 2)
        self.assertEqual(count["items"], [])

    def test_legacy_database_without_revisions_bypasses_cache(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.assertIsNone(db.cache_revision(conn, ("galleries",)))
        finally:
            conn.close()

    def test_read_only_queue_counts_cache_and_feedback_invalidation(self):
        with patch.object(recommender, "short_repeat_page", return_value={"total": 3}) as repeats, patch.object(
            recommender, "continuing_update_page", return_value={"total": 4}
        ) as updates:
            first = recommender.related_queue_counts(self.conn)
            first["short_repeats"] = -1
            self.assertEqual(recommender.related_queue_counts(self.conn)["short_repeats"], 3)
            self.assertEqual(repeats.call_count, 1)
            self.assertEqual(updates.call_count, 1)
            self.conn.execute("INSERT INTO feedback(gallery_url,vote) VALUES (?,1)", ("https://example.test/g/0/a/",))
            self.conn.commit()
            recommender.related_queue_counts(self.conn)
            self.assertEqual(repeats.call_count, 2)
            self.assertEqual(updates.call_count, 2)
            recommender.related_queue_counts(self.conn, min_new_pages=100)
            self.assertEqual(updates.call_count, 3)

    def test_caller_cannot_mutate_cached_threshold_validation(self):
        artifact = {"ready": False, "low_interest_threshold": {"threshold": 0.1}}
        with patch.object(recommender, "score_personalized_galleries", return_value=([], artifact)):
            first = self.page()
            self.conn.commit()
            first["low_interest_policy"]["validation"]["threshold"] = 0.9
            self.assertEqual(self.page()["low_interest_policy"]["validation"]["threshold"], 0.1)
            self.assertEqual(artifact["low_interest_threshold"]["threshold"], 0.1)

    def test_prediction_batch_builds_names_once_with_identical_explanations(self):
        try:
            modules = personalized.sklearn_modules()
        except personalized.PersonalizedModelUnavailable:
            self.skipTest("optional sklearn stack unavailable")
        np = modules["np"]
        matrix = modules["sparse"].csr_matrix([[1, 1, 1, 0.5], [1, 0, 2, 0.2]])
        vectorizer = Mock()
        vectorizer.get_feature_names_out.return_value = ["artist:one", "category:manga"]
        classifier = Mock()
        classifier.coef_ = np.array([[0.25, -0.5, 0.2, 0.3]])
        classifier.decision_function.return_value = np.array([0.2, 0.4])
        artifact = {"ready": True, "dict_vectorizer": vectorizer, "title_vectorizer": None,
                    "numeric_names": ["pages"], "visual_dims": 1,
                    "classifier": classifier, "model_version": "fixture"}
        with patch.object(personalized, "_transform", return_value=matrix), patch.object(
            personalized, "_attach_visual_image_features"
        ), patch.object(personalized, "_feature_names", wraps=personalized._feature_names) as names:
            predictions, _ = personalized.score_personalized_galleries(self.conn, [{}, {}], artifact=artifact)
            self.assertEqual(names.call_count, 1)
            self.assertEqual(vectorizer.get_feature_names_out.call_count, 1)
        for index, prediction in enumerate(predictions):
            positive, negative = personalized._explain_row(matrix[index], artifact, modules)
            self.assertEqual(prediction["reason_details"], {"positive": positive, "negative": negative})
            self.assertEqual(prediction["text_visual_disagreement"], round(
                personalized._modality_disagreement(matrix[index], artifact, modules), 6
            ))

    def test_history_scores_only_the_requested_page(self):
        self.conn.executemany("INSERT INTO feedback(gallery_url,vote) VALUES (?,1)", [
            (f"https://example.test/g/{i}/a/",) for i in range(12)
        ])
        self.conn.commit()
        with patch.object(recommender, "score_gallery", wraps=recommender.score_gallery) as score:
            page = recommender.reaction_history_page(self.conn, limit=2, offset=2)
        self.assertEqual(page["total"], 12)
        self.assertEqual(score.call_count, 2)
        self.assertEqual([item["gid"] for item in page["items"]], ["9", "8"])
        empty = recommender.reaction_history_page(self.conn, limit=2, offset=10001)
        self.assertEqual(empty["items"], [])
        self.assertFalse(empty["has_more"])

    def test_legacy_and_probability_diversity_parse_keys_once(self):
        for scale in ("legacy", "probability"):
            items = [{"url": str(i), "tags": ["artist:one"], "score": 1-i*0.01,
                      "rank_score": 1-i*0.01, "score_scale": scale} for i in range(20)]
            with patch.object(recommender, "diversity_keys", wraps=recommender.diversity_keys) as keys:
                result = recommender.diversify_ranked_galleries(items)
            self.assertEqual(len(result), len(items))
            self.assertEqual(keys.call_count, len(items))

    def test_related_feedback_index_does_not_decode_unused_visual_data(self):
        url = "https://exhentai.org/g/1000/a/"
        self.conn.execute("UPDATE galleries SET url=?, parent_url=?, visual_embedding_json=? WHERE gid='0'",
                          (url, "https://exhentai.org/g/999/a/", json.dumps([0.5] * 4096)))
        self.conn.execute("INSERT INTO feedback(gallery_url,vote,score) VALUES (?,1,5)", (url,))
        self.conn.execute("INSERT INTO gallery_marks(gallery_url,kind) VALUES (?,'favorite')", (url,))
        self.conn.commit()
        with patch.object(recommender, "parse_visual_embedding", side_effect=AssertionError("unused vector decoded")):
            index = recommender._build_related_feedback_reference_index(self.conn)
        reference = index[recommender.parent_repeat_key(url)][0]
        self.assertEqual(reference["url"], url)
        self.assertEqual(reference["title"], "Gallery 0")
        self.assertEqual(reference["user_score"], 5)
        self.assertEqual(reference["user_mark_kind"], "favorite")
        self.assertTrue(any(key.startswith("title-identity:") for key in index))


if __name__ == "__main__":
    unittest.main()
