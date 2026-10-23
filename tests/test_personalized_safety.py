import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from exh_rec import db, personalized as p
from exh_rec.recommender import recommend_page
from scripts.evaluate_recommender import metric_set, paired_serving_metrics


class PersonalizedSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.modules = p.sklearn_modules()
        except p.PersonalizedModelUnavailable:
            raise unittest.SkipTest("scikit-learn unavailable")

    def examples(self, count=100):
        return [{"url": str(index), "title": "liked" if index % 2 else "rejected",
                 "tags": ["tag:prefix" if index < 80 else "tag:holdout"],
                 "label": index % 2, "sample_weight": 1.0} for index in range(count)]

    def test_invalid_calibration_never_reverses_ranking(self):
        for slope in (-1.0, 0.0, float("nan"), float("inf")):
            calibrator = SimpleNamespace(coef_=[[slope]], intercept_=[0.0])
            self.assertFalse(p._valid_calibrator(calibrator))
            scores = p._calibrated([-2.0, 0.0, 2.0], calibrator, self.modules)
            self.assertTrue(all(left < right for left, right in zip(scores, scores[1:])))
        self.assertFalse(p._valid_calibrator(SimpleNamespace(coef_=[[1]], intercept_=[float("nan")])))

    def test_classifier_and_vocabulary_are_frozen_before_holdout(self):
        items = self.examples()
        fitted, classifier, calibrator, start, fit_count = p._fit_temporal_classifier(items, 0.2, self.modules)
        self.assertEqual(start, 80)
        self.assertEqual(fit_count, 80)
        self.assertNotIn("tag=tag:holdout", fitted["dict_vectorizer"].get_feature_names_out())
        independent = p._classifier(self.modules, 0.2)
        independent.fit(fitted["matrix"][:80], [item["label"] for item in items[:80]], sample_weight=[1] * 80)
        self.modules["np"].testing.assert_allclose(classifier.coef_, independent.coef_)
        self.assertTrue(p._valid_calibrator(calibrator))

    def test_hyperparameter_selection_fits_preprocessing_on_prefixes_only(self):
        items = self.examples()
        labels = self.modules["np"].asarray([item["label"] for item in items])
        with patch.object(p, "_fit_vectorizers", wraps=p._fit_vectorizers) as fitted:
            p._select_c(None, labels, self.modules["np"].ones(100), self.modules, examples=items)
        self.assertTrue(fitted.call_args_list)
        self.assertTrue(all(len(call.args[0]) < len(items) for call in fitted.call_args_list))

    def test_invalid_calibration_exposes_rank_only_not_likelihood(self):
        items = self.examples()
        fitted, classifier, _, start, _ = p._fit_temporal_classifier(items, 0.2, self.modules)
        artifact = {**fitted, "ready": True, "accepted": True, "classifier": classifier,
                    "calibrator": SimpleNamespace(coef_=[[-1.0]], intercept_=[0.0]),
                    "model_version": "test", "bootstrap_models": [], "calibration": {"ready": False}}
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        try:
            predictions, returned = p.score_personalized_galleries(conn, items[:2], artifact=artifact)
        finally:
            conn.close()
        self.assertTrue(all(item["like_probability"] is None for item in predictions))
        self.assertTrue(all(item["score_scale"] == "uncalibrated" for item in predictions))
        self.assertFalse(returned["accepted"])

    def test_report_requires_known_protocol_and_all_named_checks(self):
        report = {"model_schema": p.MODEL_SCHEMA, "feature_schema": p.FEATURE_SCHEMA,
                  "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "sample_count": 100,
                  "summary": {"acceptance": {"passed": True}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with patch.object(p, "EVALUATION_PATH", path):
                for protocol in (None, p.EVALUATION_PROTOCOL):
                    report["evaluation_protocol"] = protocol
                    path.write_text(json.dumps(report), encoding="utf-8")
                    self.assertFalse(p.evaluation_acceptance(100)[0])
                report["summary"]["acceptance"]["checks"] = dict.fromkeys(p.EVALUATION_CHECKS, True)
                report["diagnostic_only"] = False
                report["summary"].update(strict_snapshots=True, snapshot_coverage=1.0, fold_count=3)
                path.write_text(json.dumps(report), encoding="utf-8")
                self.assertTrue(p.evaluation_acceptance(100)[0])
                report["diagnostic_only"] = True
                path.write_text(json.dumps(report), encoding="utf-8")
                self.assertFalse(p.evaluation_acceptance(100)[0])
                report["diagnostic_only"] = False
                report["model_schema"] = "personalized-content-v4"
                path.write_text(json.dumps(report), encoding="utf-8")
                self.assertFalse(p.evaluation_acceptance(100)[0])

    def test_fast_stale_load_refreshes_expired_acceptance(self):
        artifact = {"schema": p.MODEL_SCHEMA, "ready": True, "accepted": True, "signature": "active",
                    "calibrator": SimpleNamespace(coef_=[[1.0]], intercept_=[0.0]),
                    "calibration": {"ready": True}, "sample_count": 100}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.joblib"
            self.modules["joblib"].dump(artifact, path)
            conn = sqlite3.connect(Path(directory) / "test.sqlite3")
            conn.row_factory = sqlite3.Row
            conn.executescript(db.SCHEMA)
            conn.execute("INSERT INTO settings(key, value) VALUES ('model_retrain_active_signature', 'active')")
            with patch.object(p, "MODEL_PATH", path), patch.object(p, "evaluation_acceptance", return_value=(False, {"passed": False, "reason": "expired"})) as check:
                loaded = p.load_personalized_model(conn, allow_stale=True)
            conn.close()
            self.assertFalse(loaded["accepted"])
            check.assert_called_once_with(100)

    def test_rank_only_metrics_do_not_claim_probability_calibration(self):
        metrics = metric_set([0, 1, 0, 1], None, self.modules, ranking_scores=[0, 1, 2, 3])
        self.assertIsNone(metrics["ece"])
        self.assertIsNone(metrics["brier"])
        self.assertIsNotNone(metrics["roc_auc"])

    def test_filtered_candidates_are_excluded_in_paired_metrics(self):
        galleries = [{"url": str(index), "title": "test", "tags": [], "tag_weights": {}} for index in range(3)]
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        for item in galleries:
            conn.execute("INSERT INTO galleries(url, title) VALUES (?, ?)", (item["url"], item["title"]))
        returned = [{"url": "1", "like_probability": 0.8}, {"url": "0", "like_probability": 0.2}]
        with patch("scripts.evaluate_recommender.recommend_page", return_value={"items": returned}):
            result = paired_serving_metrics(conn, galleries, [0, 1, 1], {}, self.modules)
        conn.close()
        self.assertEqual(result["filtered"], 1)
        self.assertEqual(result["personalized"]["count"], 2)
        self.assertEqual(result["legacy"]["count"], 2)

    def test_replay_keeps_learned_components_on_frozen_training_connection(self):
        serving = sqlite3.connect(":memory:")
        training = sqlite3.connect(":memory:")
        for conn in (serving, training):
            conn.row_factory = sqlite3.Row
            conn.executescript(db.SCHEMA)
        serving.execute("INSERT INTO galleries(url, title) VALUES ('candidate', 'Later features')")
        with patch("exh_rec.recommender.continuing_classifier_decisions", return_value=({}, {})) as classifier:
            recommend_page(serving, training_conn=training, personalized_artifact={"ready": False},
                           low_interest_percent=0)
        self.assertIs(classifier.call_args.args[0], training)
        serving.close()
        training.close()

    def test_v2_migration_captures_now_without_relabeling_old_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            with patch.object(db, "DB_PATH", path), patch.object(db, "DATA_DIR", Path(directory)):
                db.init_db()
                with db.connect() as conn:
                    conn.execute("INSERT INTO galleries(url, title, parent_url) VALUES ('test', 'Current', 'parent')")
                    conn.execute("INSERT INTO gallery_feature_snapshots(gallery_url, title, captured_at) VALUES ('test', 'Old', '2026-01-01')")
                db.init_db()
                db.init_db()
                with db.connect() as conn:
                    rows = conn.execute("SELECT title, snapshot_schema, parent_url, captured_at FROM gallery_feature_snapshots ORDER BY id").fetchall()
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(tuple(rows[0]), ("Old", 1, None, "2026-01-01"))
                    self.assertEqual(tuple(rows[1])[:3], ("Current", 2, "parent"))
                    self.assertNotEqual(rows[1]["captured_at"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()
