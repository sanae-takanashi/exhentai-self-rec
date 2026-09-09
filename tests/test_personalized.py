import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from exh_rec import db
from exh_rec.exhentai import Gallery
from exh_rec.personalized import (
    FEATURE_SCHEMA,
    MIN_CLASS,
    MIN_LABELED,
    MODEL_SCHEMA,
    evaluation_acceptance,
    learn_low_interest_threshold,
    model_data_signature,
    normalize_reason_code,
    normalize_surface,
    sklearn_modules,
    train_personalized_model,
    training_examples,
    _numeric_features,
    _rolling_splits,
)
from exh_rec.recommender import (
    discovery_page,
    diversify_ranked_galleries,
    diversify_probability_ranked_galleries,
    export_preferences,
    import_preferences,
    mix_bootstrap_exploration,
    record_feedback,
    record_impressions,
    store_galleries,
    store_visual_embedding,
    store_visual_embedding_variant,
    store_visual_image_embeddings,
    set_classification_override,
)


class PersonalizedTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_strict_temporal_training_uses_last_snapshot_before_feedback(self):
        url = "https://exhentai.org/g/8999/a/"
        store_galleries(
            self.conn,
            [Gallery(url=url, gid="8999", token="a", title="Before", tags=["female:before"])],
        )
        db.snapshot_gallery_features(self.conn, url, "test", "2026-01-01 00:00:00")
        self.conn.execute(
            "UPDATE galleries SET title = 'After', tags_json = '[\"female:after\"]' WHERE url = ?",
            (url,),
        )
        db.snapshot_gallery_features(self.conn, url, "test", "2026-03-01 00:00:00")
        self.conn.execute(
            "INSERT INTO feedback(gallery_url, vote, created_at) VALUES (?, 1, '2026-02-01 00:00:00')",
            (url,),
        )

        examples = training_examples(self.conn, strict_temporal=True)

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["title"], "Before")
        self.assertEqual(examples[0]["tags"], ["female:before"])

    def test_feedback_reason_and_surface_round_trip_in_v2_export(self):
        url = "https://exhentai.org/g/9000/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9000", token="a", title="Reason")])
        record_feedback(self.conn, url, vote=-1, reason_code="visual_style", surface="discovery")

        payload = export_preferences(self.conn)
        target = sqlite3.connect(":memory:")
        target.row_factory = sqlite3.Row
        target.executescript(db.SCHEMA)
        import_preferences(target, payload)
        row = target.execute("SELECT reason_code, surface FROM feedback").fetchone()

        self.assertEqual(payload["schema"], "exh-rec-preferences-v2")
        self.assertEqual(dict(row), {"reason_code": "visual_style", "surface": "discovery"})
        target.close()

    def test_classification_override_round_trips_with_unrated_gallery(self):
        url = "https://exhentai.org/g/9009/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9009", token="a", title="Manual Class")])
        set_classification_override(self.conn, url, "updates")

        payload = export_preferences(self.conn)
        target = sqlite3.connect(":memory:")
        target.row_factory = sqlite3.Row
        target.executescript(db.SCHEMA)
        result = import_preferences(target, payload)
        row = target.execute("SELECT gallery_url, classification FROM gallery_classification_overrides").fetchone()

        self.assertEqual(result["classification_overrides"], 1)
        self.assertEqual(dict(row), {"gallery_url": url, "classification": "updates"})
        target.close()

    def test_feedback_reason_validation_and_surface_fallback(self):
        self.assertEqual(normalize_reason_code("content_tags"), "content_tags")
        self.assertEqual(normalize_reason_code("gallery_too_small"), "gallery_too_small")
        self.assertEqual(normalize_reason_code("too_few_relevant_images"), "too_few_relevant_images")
        self.assertIsNone(normalize_reason_code(""))
        self.assertEqual(normalize_surface("unknown"), "review")
        with self.assertRaises(ValueError):
            normalize_reason_code("made-up")

    def test_duplicate_update_feedback_is_excluded_from_training(self):
        url = "https://exhentai.org/g/9001/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9001", token="a", title="Duplicate")])
        record_feedback(self.conn, url, vote=-1, reason_code="duplicate_update")
        self.assertEqual(training_examples(self.conn), [])

    def test_negative_reason_does_not_change_serving_features(self):
        gallery = {"page_count": 8, "tags": [], "active_reason_code": "gallery_too_small"}
        normal = _numeric_features({"page_count": 8, "tags": []})
        reasoned = _numeric_features(gallery)

        self.assertEqual(reasoned, normal)

    def test_too_few_relevant_images_does_not_amplify_whole_gallery_visual(self):
        gallery = {
            "page_count": 500,
            "tags": [],
            "active_reason_code": "too_few_relevant_images",
            "visual_embedding": [1.0, 0.0],
        }
        normal = _numeric_features({**gallery, "active_reason_code": None})
        reasoned = _numeric_features(gallery)

        self.assertEqual(reasoned, normal)

    def test_per_image_vectors_produce_gallery_diversity_features(self):
        url = "https://exhentai.org/g/9010/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9010", token="a", title="Mixed Images")])
        store_visual_embedding(self.conn, url, [1, 1, 0, 0] * 16)
        store_visual_image_embeddings(
            self.conn,
            url,
            [
                {"image_url": "https://example.test/a.jpg", "embedding": [1, 0, 0, 0] * 16},
                {"image_url": "https://example.test/b.jpg", "embedding": [0, 1, 0, 0] * 16},
            ],
        )
        record_feedback(self.conn, url, vote=1)

        example = training_examples(self.conn)[0]
        features = _numeric_features(example)

        self.assertEqual(example["visual_image_count"], 2)
        self.assertEqual(features["visual_image_stats_ready"], 1.0)
        self.assertGreater(features["visual_image_diversity"], 0.0)
        self.assertGreater(features["visual_image_outlier"], 0.0)

    def test_insufficient_data_uses_safe_fallback(self):
        url = "https://exhentai.org/g/9002/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9002", token="a", title="Sparse")])
        record_feedback(self.conn, url, vote=1)
        status = train_personalized_model(self.conn, persist=False)
        self.assertFalse(status["ready"])
        self.assertEqual(status["status"], "insufficient-data")

    def test_unlabeled_gallery_does_not_invalidate_model_signature(self):
        labeled = Gallery(url="https://exhentai.org/g/90020/a/", gid="90020", token="a", title="Labeled")
        store_galleries(self.conn, [labeled])
        record_feedback(self.conn, labeled.url, vote=1)
        before = model_data_signature(self.conn)

        store_galleries(
            self.conn,
            [Gallery(url="https://exhentai.org/g/90021/a/", gid="90021", token="a", title="Unlabeled")],
        )

        self.assertEqual(model_data_signature(self.conn), before)

    def test_model_signature_detects_same_length_embedding_content_change(self):
        base = {
            "url": "https://exhentai.org/g/signature/a/",
            "label": 1,
            "sample_weight": 1.0,
            "visual_embedding_version": "visual-v1",
        }

        first = model_data_signature(self.conn, [{**base, "visual_embedding_json": "[1,2]"}])
        second = model_data_signature(self.conn, [{**base, "visual_embedding_json": "[2,1]"}])

        self.assertNotEqual(first, second)

    def test_model_signature_payload_excludes_raw_embedding_json(self):
        raw_embedding = "[0.123456,0.654321]"
        original_dumps = json.dumps
        captured = {}

        def capture_payload(value, *args, **kwargs):
            captured["value"] = value
            return original_dumps(value, *args, **kwargs)

        with patch("exh_rec.personalized.json.dumps", side_effect=capture_payload):
            model_data_signature(
                self.conn,
                [{
                    "url": "https://exhentai.org/g/signature/b/",
                    "label": 1,
                    "sample_weight": 1.0,
                    "visual_embedding_json": raw_embedding,
                    "visual_embedding_version": "visual-v1",
                }],
            )

        encoded_payload = original_dumps(captured["value"], sort_keys=True)
        self.assertNotIn(raw_embedding, encoded_payload)
        self.assertIn("visual_embedding_digest", encoded_payload)

    def test_evaluation_acceptance_rejects_stale_feature_schema(self):
        report = {
            "model_schema": MODEL_SCHEMA,
            "feature_schema": "content-features-old",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "sample_count": 100,
            "summary": {"acceptance": {"passed": True}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with patch("exh_rec.personalized.EVALUATION_PATH", path):
                accepted, status = evaluation_acceptance(100)

        self.assertFalse(accepted)
        self.assertIn("feature schema", status["reason"])

    def test_evaluation_acceptance_records_report_freshness(self):
        generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        report = {
            "model_schema": MODEL_SCHEMA,
            "feature_schema": FEATURE_SCHEMA,
            "generated_at": generated_at,
            "sample_count": 100,
            "summary": {"acceptance": {"passed": True, "checks": {"example": True}}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with patch("exh_rec.personalized.EVALUATION_PATH", path):
                accepted, status = evaluation_acceptance(100)

        self.assertTrue(accepted)
        self.assertEqual(status["generated_at"], generated_at)

    def test_rolling_splits_always_include_latest_calibration_window(self):
        splits = _rolling_splits(866)

        self.assertIn((766, 866), splits)
        self.assertTrue(all(start < end <= 866 for start, end in splits))

    def test_rolling_splits_cover_first_trainable_model(self):
        splits = _rolling_splits(MIN_LABELED)

        self.assertGreaterEqual(len(splits), 2)
        self.assertEqual(splits[0][0], MIN_CLASS * 2)
        self.assertEqual(max(end for _start, end in splits), MIN_LABELED)

    def test_low_interest_threshold_selects_largest_safe_temporal_cutoff(self):
        predictions = [
            {
                "index": index,
                "probability": round((index + 1) / 100, 6),
                "label": 0 if index < 40 else int(index % 3 == 0),
            }
            for index in range(100)
        ]
        with patch(
            "exh_rec.personalized._temporal_oof_probabilities",
            return_value=(predictions, [{"train_count": 50, "test_count": 50}], True),
        ):
            policy = learn_low_interest_threshold(
                [], 0.2, {}, model_version="model-v1", generated_at="2026-08-31 00:00:00"
            )

        self.assertTrue(policy["ready"])
        self.assertEqual(policy["threshold"], 0.42)
        self.assertEqual(policy["triaged_count"], 42)
        self.assertEqual(policy["triaged_positive_count"], 0)
        self.assertLessEqual(policy["positive_rate_upper_95"], 0.10)

    def test_low_interest_threshold_stays_disabled_when_false_omission_is_unsafe(self):
        predictions = [
            {
                "index": index,
                "probability": round((index + 1) / 100, 6),
                "label": int(index % 5 == 0),
            }
            for index in range(100)
        ]
        with patch(
            "exh_rec.personalized._temporal_oof_probabilities",
            return_value=(predictions, [{"train_count": 50, "test_count": 50}], True),
        ):
            policy = learn_low_interest_threshold(
                [], 0.2, {}, model_version="model-v1", generated_at="2026-08-31 00:00:00"
            )

        self.assertFalse(policy["ready"])
        self.assertEqual(policy["status"], "safety-target-not-met")
        self.assertIsNone(policy["threshold"])

    def test_probability_mmr_never_selects_outside_window(self):
        items = [
            {"url": "a", "like_probability": 0.9, "rank_score": 0.9, "confidence": 1.0, "model_version": "personalized-content-v2-x", "score_scale": "probability", "tags": ["artist:same"]},
            {"url": "b", "like_probability": 0.84, "rank_score": 0.84, "confidence": 1.0, "model_version": "personalized-content-v2-x", "score_scale": "probability", "tags": ["artist:same"]},
            {"url": "c", "like_probability": 0.79, "rank_score": 0.79, "confidence": 1.0, "model_version": "personalized-content-v2-x", "score_scale": "probability", "tags": ["artist:other"]},
        ]
        result = diversify_probability_ranked_galleries(items)
        self.assertEqual(result[0]["url"], "a")
        self.assertEqual(result[1]["url"], "b")

    def test_probability_dispatch_uses_probability_diversifier_for_v2(self):
        items = [
            {"url": "a", "score": 0.9, "rank_score": 0.9, "like_probability": 0.9, "score_scale": "probability", "tags": ["artist:same"]},
            {"url": "b", "score": 0.84, "rank_score": 0.84, "like_probability": 0.84, "score_scale": "probability", "tags": ["artist:same"]},
            {"url": "c", "score": 0.83, "rank_score": 0.83, "like_probability": 0.83, "score_scale": "probability", "tags": ["artist:other"]},
        ]

        result = diversify_ranked_galleries(items)

        self.assertEqual([item["url"] for item in result[:2]], ["a", "c"])
        self.assertTrue(all(float(item["score"]) >= 0 for item in result))

    def test_similarity_dispatch_preserves_cosine_scores(self):
        items = [
            {"url": "a", "score": 0.90, "score_scale": "similarity", "tags": ["artist:same"], "reasons": []},
            {"url": "b", "score": 0.84, "score_scale": "similarity", "tags": ["artist:same"], "reasons": []},
            {"url": "c", "score": 0.83, "score_scale": "similarity", "tags": ["artist:other"], "reasons": []},
        ]

        result = diversify_ranked_galleries(items)

        self.assertEqual([item["url"] for item in result[:2]], ["a", "c"])
        self.assertEqual({item["url"]: item["score"] for item in result}, {"a": 0.90, "b": 0.84, "c": 0.83})
        self.assertNotIn("diversity -0.45", " ".join(reason for item in result for reason in item.get("reasons", [])))

    def test_probability_exploration_uses_relative_score_floor(self):
        items = [
            {
                "url": str(index),
                "score": 0.9 - index * 0.05,
                "rank_score": 0.9 - index * 0.05,
                "score_scale": "probability",
                "source_query": "artist:seed",
                "reasons": ["bootstrap boost"],
            }
            for index in range(10)
        ]

        result = mix_bootstrap_exploration(items, limit=5, count=2, bootstrap_queries={"artist:seed"}, seed="fixed")

        self.assertEqual(len(result), len(items))
        self.assertTrue(any(item.get("reasons", [""])[0] == "bootstrap explore" for item in result[:5]))

    def test_impressions_are_idempotent_and_not_feedback(self):
        url = "https://exhentai.org/g/9003/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9003", token="a", title="Shown")])
        item = {"gallery_url": url, "position": 0, "model_version": "m1", "like_probability": 0.7}
        self.assertEqual(record_impressions(self.conn, "request-1", "review", [item]), 1)
        self.assertEqual(record_impressions(self.conn, "request-1", "review", [item]), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 0)

    def test_shadow_visual_embedding_is_stored_without_overwriting_production(self):
        url = "https://exhentai.org/g/9004/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9004", token="a", title="Shadow")])
        store_visual_embedding(self.conn, url, [1.0, *([0.0] * 15)])
        production = self.conn.execute(
            "SELECT visual_embedding_json, visual_embedding_version FROM galleries WHERE url = ?",
            (url,),
        ).fetchone()

        store_visual_embedding_variant(
            self.conn,
            url,
            [0.0, 3.0, *([0.0] * 14)],
            version="siglip2-test-v1",
            encoder="siglip2",
            image_count=2,
        )
        shadow = self.conn.execute(
            "SELECT * FROM gallery_visual_embeddings WHERE gallery_url = ?",
            (url,),
        ).fetchone()
        current = self.conn.execute(
            "SELECT visual_embedding_json, visual_embedding_version FROM galleries WHERE url = ?",
            (url,),
        ).fetchone()

        self.assertEqual(tuple(current), tuple(production))
        self.assertEqual(shadow["embedding_version"], "siglip2-test-v1")
        self.assertEqual(shadow["encoder"], "siglip2")
        self.assertEqual(shadow["dimensions"], 16)
        self.assertEqual(shadow["image_count"], 2)

    def test_sklearn_model_outputs_calibrated_fields_when_available(self):
        try:
            sklearn_modules()
        except Exception:
            self.skipTest("scikit-learn is not installed in this interpreter")
        galleries = []
        total = max(MIN_LABELED, MIN_CLASS * 2 + 20)
        for index in range(total + 2):
            liked = index % 2 == 0
            url = f"https://exhentai.org/g/{9100 + index}/a/"
            galleries.append(
                Gallery(url=url, gid=str(9100 + index), token="a", title=f"{'Liked' if liked else 'Rejected'} {index}", tags=[f"artist:{'good' if liked else 'bad'}"])
            )
        store_galleries(self.conn, galleries)
        for index, gallery in enumerate(galleries[:-2]):
            record_feedback(self.conn, gallery.url, vote=1 if index % 2 == 0 else -1)
        model = train_personalized_model(self.conn, persist=False)
        self.assertTrue(model["ready"])
        self.assertIsNotNone(model["calibrator"])
        page = discovery_page(self.conn, limit=2, candidate_limit=100, seed="fixed")
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(all(0 <= item["like_probability"] <= 1 for item in page["items"]))
        self.assertTrue(all(item.get("discovery_reason") for item in page["items"]))

    def test_discovery_can_page_beyond_first_hundred_candidates(self):
        galleries = [
            Gallery(
                url=f"https://exhentai.org/g/{10000 + index}/a/",
                gid=str(10000 + index),
                token="a",
                title=f"Candidate {index}",
                tags=[f"artist:artist-{index % 12}"],
            )
            for index in range(150)
        ]
        store_galleries(self.conn, galleries)

        def fake_scores(_conn, candidates):
            predictions = [
                {
                    "like_probability": 0.9 - index / 1000,
                    "rank_score": 0.9 - index / 1000,
                    "uncertainty": (index % 17) / 100,
                    "confidence": 0.8,
                    "model_version": "personalized-content-v2-test",
                    "score_scale": "probability",
                    "reason_details": {"positive": [], "negative": []},
                    "text_visual_disagreement": (index % 11) / 20,
                }
                for index, _candidate in enumerate(candidates)
            ]
            return predictions, {"ready": True, "accepted": True, "status": "ready", "score_scale": "probability"}

        with patch("exh_rec.recommender.score_personalized_galleries", side_effect=fake_scores):
            first = discovery_page(self.conn, limit=100, candidate_limit=150, seed="fixed")
            second = discovery_page(self.conn, limit=20, offset=100, candidate_limit=150, seed="fixed")

        self.assertEqual(first["total"], 150)
        self.assertEqual(len(second["items"]), 20)
        self.assertTrue(second["has_more"])
        self.assertFalse({item["url"] for item in first["items"]} & {item["url"] for item in second["items"]})

    def test_positive_feedback_rejects_negative_reason(self):
        url = "https://exhentai.org/g/10999/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="10999", token="a", title="Positive")])

        with self.assertRaisesRegex(ValueError, "only valid for negative"):
            record_feedback(self.conn, url, vote=1, reason_code="quality")

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
