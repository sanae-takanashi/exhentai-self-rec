import sqlite3
import unittest

from exh_rec import db
from exh_rec.exhentai import Gallery
from exh_rec.personalized import (
    MIN_CLASS,
    MIN_LABELED,
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
    diversify_probability_ranked_galleries,
    export_preferences,
    import_preferences,
    record_feedback,
    record_impressions,
    store_galleries,
    store_visual_embedding,
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

    def test_gallery_too_small_reason_strengthens_short_gallery_feature(self):
        gallery = {"page_count": 8, "tags": [], "active_reason_code": "gallery_too_small"}
        normal = _numeric_features({"page_count": 8, "tags": []})
        reasoned = _numeric_features(gallery)

        self.assertGreater(reasoned["short_gallery"], normal["short_gallery"])

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

    def test_rolling_splits_always_include_latest_calibration_window(self):
        splits = _rolling_splits(866)

        self.assertIn((766, 866), splits)
        self.assertTrue(all(start < end <= 866 for start, end in splits))

    def test_probability_mmr_never_selects_outside_window(self):
        items = [
            {"url": "a", "like_probability": 0.9, "confidence": 1.0, "model_version": "personalized-content-v1-x", "tags": ["artist:same"]},
            {"url": "b", "like_probability": 0.84, "confidence": 1.0, "model_version": "personalized-content-v1-x", "tags": ["artist:same"]},
            {"url": "c", "like_probability": 0.79, "confidence": 1.0, "model_version": "personalized-content-v1-x", "tags": ["artist:other"]},
        ]
        result = diversify_probability_ranked_galleries(items)
        self.assertEqual(result[0]["url"], "a")
        self.assertEqual(result[1]["url"], "b")

    def test_impressions_are_idempotent_and_not_feedback(self):
        url = "https://exhentai.org/g/9003/a/"
        store_galleries(self.conn, [Gallery(url=url, gid="9003", token="a", title="Shown")])
        item = {"gallery_url": url, "position": 0, "model_version": "m1", "like_probability": 0.7}
        self.assertEqual(record_impressions(self.conn, "request-1", "review", [item]), 1)
        self.assertEqual(record_impressions(self.conn, "request-1", "review", [item]), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 0)

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
        page = discovery_page(self.conn, limit=2, candidate_limit=100, seed="fixed")
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(all(0 <= item["like_probability"] <= 1 for item in page["items"]))
        self.assertTrue(all(item.get("discovery_reason") for item in page["items"]))


if __name__ == "__main__":
    unittest.main()
