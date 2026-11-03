"""An explicit Review trial never grants model acceptance or changes triage."""
from copy import deepcopy
from http import HTTPStatus
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from exh_rec import app, db
from exh_rec.exhentai import Gallery
from exh_rec.recommender import (
    apply_review_ranking, diversify_ranked_galleries, recommend_page, record_feedback, record_impressions,
    store_galleries, undo_feedback,
)


class RankingTrialTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(db.SCHEMA)
        self.addCleanup(self.conn.close)
        store_galleries(self.conn, [Gallery(url=f"https://exhentai.org/g/{index}/a/", gid=str(index),
                                          token="a", title=f"Fixture {index}") for index in range(1, 21)])
        self.artifact = {"ready": True, "accepted": False, "model_version": "personalized-content-v5-test",
                         "trained_at": "2026-11-03 00:00:00", "signature": "fixture-one"}
        self.predictor = patch("exh_rec.recommender.score_personalized_galleries", side_effect=self.predict)
        self.predictor.start()
        self.addCleanup(self.predictor.stop)
        classifier = patch("exh_rec.recommender.continuing_classifier_decisions", return_value=({}, {}))
        classifier.start()
        self.addCleanup(classifier.stop)

    def predict(self, conn, items, **kwargs):
        return [{"like_probability": int(item["gid"]) / 21, "rank_score": int(item["gid"]) / 21,
                 "model_version": self.artifact["model_version"], "score_scale": "probability"}
                for item in items], self.artifact

    def page(self, mode=None, **kwargs):
        return recommend_page(self.conn, limit=100, interest_band="primary", review_ranking_mode=mode,
                              personalized_artifact=self.artifact, **kwargs)

    def test_trial_reranks_same_candidates_without_acceptance_or_likelihood_claim(self):
        self.page("legacy")
        old = self.page("legacy")
        new = self.page("personalized_trial")
        self.assertEqual({item["url"] for item in old["items"]}, {item["url"] for item in new["items"]})
        self.assertNotEqual([item["url"] for item in old["items"]], [item["url"] for item in new["items"]])
        self.assertEqual(old["low_interest_policy"], new["low_interest_policy"])
        self.assertEqual(new["review_ranking"]["active"], "personalized_trial")
        self.assertFalse(new["personalized_model"]["accepted"])
        self.assertFalse(self.artifact["accepted"])
        for index, item in enumerate(new["items"]):
            self.assertTrue(item["ranking_trial"])
            self.assertIsNone(item["like_probability"])
            self.assertIsNone(item["confidence"])
            self.assertEqual(item["score_scale"], "trial-ranking")
            self.assertEqual(item["served_model"], "personalized-trial")
            self.assertEqual(item["served_rank"], index + 1)
            self.assertFalse(any("match " in reason for reason in item["reasons"]))

    def test_switch_back_matches_original_legacy_order_and_scores(self):
        baseline = self.page()
        self.page("personalized_trial")
        reverted = self.page("legacy")
        self.assertEqual([(item["url"], item["score"]) for item in baseline["items"]],
                         [(item["url"], item["score"]) for item in reverted["items"]])
        self.assertTrue(all(item["model_version"] == "legacy-linear-v1" for item in reverted["items"]))

    def test_trial_does_not_leak_to_low_interest_preview_or_default_callers(self):
        for band, rated, mode in [("low", False, "personalized_trial"), ("all", True, "personalized_trial"),
                                  ("primary", True, "personalized_trial"), ("primary", False, None)]:
            with self.subTest(band=band, rated=rated, mode=mode):
                page = recommend_page(self.conn, interest_band=band, include_rated=rated,
                                      review_ranking_mode=mode, personalized_artifact=self.artifact)
                self.assertIsNone(page["review_ranking"])
                self.assertFalse(any(item.get("ranking_trial") for item in page["items"]))

    def test_audit_selection_and_memberships_are_identical_across_modes(self):
        old = self.page("legacy", low_interest_percent=40, low_interest_audit_daily_count=5)
        memberships = [tuple(row) for row in self.conn.execute("SELECT * FROM low_interest_memberships ORDER BY gallery_url")]
        audits = [tuple(row) for row in self.conn.execute("SELECT * FROM low_interest_audits ORDER BY id")]
        new = self.page("personalized_trial", low_interest_percent=40, low_interest_audit_daily_count=5)
        self.assertEqual(old["low_interest_total"], new["low_interest_total"])
        self.assertEqual({item["url"] for item in old["items"]}, {item["url"] for item in new["items"]})
        self.assertEqual({item["audit_id"] for item in old["items"] if item.get("audit_id")},
                         {item["audit_id"] for item in new["items"] if item.get("audit_id")})
        self.assertEqual(memberships, [tuple(row) for row in self.conn.execute("SELECT * FROM low_interest_memberships ORDER BY gallery_url")])
        self.assertEqual(audits, [tuple(row) for row in self.conn.execute("SELECT * FROM low_interest_audits ORDER BY id")])
        self.assertTrue(all(item.get("audit_id") for item in new["items"][:5]))

    def test_missing_or_nonfinite_predictions_fall_back_whole_pool(self):
        status = {**self.artifact, "artifact_id": "fixture-id"}
        item = {"legacy_score": 3, "personalized_rank_score": 0.8, "reasons": []}
        for bad in (None, float("nan"), float("inf"), "invalid"):
            items = [deepcopy(item), {**item, "personalized_rank_score": bad}]
            returned = apply_review_ranking(items, "personalized_trial", "hybrid", status)
            self.assertEqual(returned["active"], "legacy")
            self.assertEqual(returned["fallback_reason"], "predictions-unavailable")
            self.assertTrue(all(not candidate["ranking_trial"] for candidate in items))
        returned = apply_review_ranking([deepcopy(item)], "personalized_trial", "hybrid", {"ready": False})
        self.assertEqual(returned["fallback_reason"], "model-not-ready")
        returned = apply_review_ranking([deepcopy(item)], "personalized_trial", "hybrid", {"ready": True})
        self.assertEqual(returned["fallback_reason"], "model-identity-unavailable")

    def test_visual_mode_is_not_silently_changed_by_trial(self):
        items = [{"score": 0.4, "score_scale": "similarity"}]
        original = deepcopy(items)
        status = apply_review_ranking(items, "personalized_trial", "visual", {})
        self.assertEqual(items, original)
        self.assertEqual(status["active"], "visual")

    def test_recorded_context_identifies_trial_and_training_revision(self):
        page = self.page("personalized_trial")
        item = {**page["items"][0], "position": 0, "visibility_protocol": "viewport-v1"}
        self.assertEqual(record_impressions(self.conn, "trial-fixture", "review", [item]), 1)
        row = self.conn.execute("SELECT * FROM recommendation_impressions").fetchone()
        context = json.loads(row["ranking_context_json"])
        self.assertEqual(context["ranking_policy"], "personalized_trial")
        self.assertEqual(context["ranking_artifact_id"], page["review_ranking"]["artifact_id"])
        self.assertEqual(context["ranking_trained_at"], self.artifact["trained_at"])
        self.assertIsNone(row["like_probability"])
        self.assertIsNone(row["visible_at"])
        first_id = page["review_ranking"]["artifact_id"]
        self.artifact["signature"] = "fixture-two"
        self.assertNotEqual(self.page("personalized_trial")["review_ranking"]["artifact_id"], first_id)

    def test_legacy_forced_even_after_formal_acceptance(self):
        self.artifact["accepted"] = True
        page = self.page("legacy")
        self.assertTrue(page["personalized_model"]["accepted"])
        self.assertTrue(all(item["model_version"] == "legacy-linear-v1" and item["like_probability"] is None
                            for item in page["items"]))
        self.assertFalse(any(any("match " in reason for reason in item["reasons"]) for item in page["items"]))

    def test_trial_keeps_normalized_score_diversity_behavior(self):
        items = [{"url": str(index), "score": score, "rank_score": score, "confidence": None,
                  "uploader": "fixture" if index < 2 else "other", "reasons": [], "tags": []}
                 for index, score in enumerate((0.9, 0.85, 0.6))]
        original = [{**item, "score_scale": "probability"} for item in deepcopy(items)]
        trial = [{**item, "score_scale": "trial-ranking"} for item in deepcopy(items)]
        self.assertEqual([item["url"] for item in diversify_ranked_galleries(original)],
                         [item["url"] for item in diversify_ranked_galleries(trial)])

    def test_feedback_links_exact_delivery_including_neutral_and_undo(self):
        item = {**self.page("personalized_trial")["items"][0], "position": 0}
        record_impressions(self.conn, "exact-request", "review", [item])
        for score in (5, 1, 3):
            record_feedback(self.conn, item["url"], score=score, surface="audit", retrain=False,
                            impression_request_id="exact-request", impression_surface="review")
        rows = self.conn.execute("SELECT f.id, f.vote, i.request_id FROM feedback f "
                                 "JOIN feedback_impression_links l ON l.feedback_id=f.id "
                                 "JOIN recommendation_impressions i ON i.id=l.impression_id ORDER BY f.id").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["request_id"] for row in rows], ["exact-request"] * 3)
        self.assertGreater(rows[0]["vote"], 0)
        self.assertLess(rows[1]["vote"], 0)
        self.assertEqual(rows[2]["vote"], 0)
        undo_feedback(self.conn, item["url"], rows[-1]["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feedback_impression_links").fetchone()[0], 2)

    def test_missing_mismatched_or_undelivered_context_stays_unknown(self):
        item = {**self.page("personalized_trial")["items"][0], "position": 0}
        record_impressions(self.conn, "exact-request", "review", [item])
        for request_id, surface in ((None, None), ("unknown-request", "review"),
                                    ("exact-request", "discovery"), ([], {})):
            record_feedback(self.conn, item["url"], score=5, retrain=False,
                            impression_request_id=request_id, impression_surface=surface)
        other = "https://exhentai.org/g/1/a/"
        self.assertNotEqual(item["url"], other)
        record_feedback(self.conn, other, score=5, retrain=False,
                        impression_request_id="exact-request", impression_surface="review")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feedback_impression_links").fetchone()[0], 0)

    def test_corrupt_scorer_output_falls_back_before_trial_sort(self):
        original = self.predict
        def corrupt(conn, items, **kwargs):
            predictions, artifact = original(conn, items, **kwargs)
            predictions[0]["rank_score"] = float("nan")
            return predictions, artifact
        with patch("exh_rec.recommender.score_personalized_galleries", side_effect=corrupt):
            page = self.page("personalized_trial")
        self.assertEqual(page["review_ranking"]["active"], "legacy")
        self.assertTrue(all(item["model_version"] == "legacy-linear-v1" for item in page["items"]))


class RankingTrialSettingsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ranking-trial-settings-"))
        for name, value in (("DATA_DIR", self.root), ("DB_PATH", self.root / "fixture.sqlite3")):
            patcher = patch.object(db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.init_db()

    def test_settings_default_off_and_narrow_toggle_does_not_train(self):
        with db.connect() as conn:
            before = dict(conn.execute("SELECT key,value FROM settings"))
        self.assertEqual(before["review_ranking_mode"], "legacy")
        with patch.object(app, "retrain_model", side_effect=AssertionError("No training")), patch.object(
            app, "wake_background_refresh", side_effect=AssertionError("No fetch")
        ):
            app.save_settings({"review_ranking_mode": "personalized_trial"})
        with db.connect() as conn:
            after = dict(conn.execute("SELECT key,value FROM settings"))
        self.assertEqual(after.pop("review_ranking_mode"), "personalized_trial")
        before.pop("review_ranking_mode")
        self.assertEqual(before, after)
        self.assertEqual(app.get_settings()["review_ranking_mode"], "personalized_trial")
        app.save_settings({"review_ranking_mode": "legacy"})
        self.assertEqual(app.get_settings()["review_ranking_mode"], "legacy")

    def test_invalid_setting_rejected_without_write(self):
        for value in (True, 1, None, "yes", {}, []):
            with self.subTest(value=value), self.assertRaises(app.ApiError) as caught:
                app.save_settings({"review_ranking_mode": value})
            self.assertEqual(caught.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(app.get_settings()["review_ranking_mode"], "legacy")

    def test_only_unrated_primary_endpoint_receives_opt_in(self):
        app.save_settings({"review_ranking_mode": "personalized_trial"})
        with db.connect() as conn, patch.object(app, "recommend_page", return_value={"items": []}) as recommend:
            for band, rated in (("primary", False), ("low", False), ("all", False), ("primary", True)):
                app.recommendation_payload(conn, interest_band=band, include_rated=rated)
                self.assertEqual(recommend.call_args.kwargs["review_ranking_mode"],
                                 "personalized_trial" if band == "primary" and not rated else None)


if __name__ == "__main__":
    unittest.main()
