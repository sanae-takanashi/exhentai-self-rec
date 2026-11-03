import unittest

from scripts.recommender_experiments import development_split, select_variant, variant_examples


class RecommenderExperimentsTest(unittest.TestCase):
    def rows(self):
        return [{"created_at": f"2026-11-01 00:{minute:02d}:00"}
                for minute in range(30) for _ in range(10)]

    def test_holdout_is_reserved_and_disjoint_before_selection(self):
        rows = self.rows()
        development, holdout = development_split(rows, "2026-11-01", 100)
        self.assertEqual((len(development), len(holdout)), (200, 100))
        self.assertLess(development[-1]["created_at"], holdout[0]["created_at"])
        self.assertEqual(development + holdout, rows)

    def test_equal_timestamp_never_straddles_development_and_holdout(self):
        development, holdout = development_split(self.rows(), "2026-11-01", 105)
        self.assertEqual((len(development), len(holdout)), (190, 110))
        self.assertLess(development[-1]["created_at"], holdout[0]["created_at"])

    def test_insufficient_new_data_never_falls_back_to_old_outcomes(self):
        with self.assertRaises(ValueError):
            development_split(self.rows(), "2026-11-02", 100)

    def test_candidate_must_improve_ranking_without_calibration_regression(self):
        baseline = {"ndcg_at_20": 0.7, "precision_at_10": 0.8, "ece": 0.1}
        self.assertEqual(select_variant({"v5": baseline, "candidate": dict(baseline, ndcg_at_20=0.8)}), "candidate")
        for changed in ({"ece": 0.2}, {"precision_at_10": 0.7}, {"ndcg_at_20": 0.71}, {"ece": None}):
            candidate = dict(baseline, ndcg_at_20=0.8)
            candidate.update(changed)
            self.assertEqual(select_variant({"v5": baseline, "candidate": candidate}), "v5")

    def test_explicit_only_does_not_filter_or_require_labels_in_test_features(self):
        gallery = {"title": "Unlabeled"}
        self.assertEqual(variant_examples([gallery], "explicit_only", "2026-11-01", training=False), [gallery])
        items = [{"label_source": "hath-download"}, {"label_source": "vote"}]
        self.assertEqual(variant_examples(items, "explicit_only", "2026-11-01"), [items[1]])

    def test_ablation_never_mutates_shared_training_or_test_features(self):
        item = {"title": "Original", "title_jpn": "Original", "visual_embedding": [1.0],
                "visual_embedding_json": "[1]", "visual_image_count": 3}
        self.assertEqual(variant_examples([item], "no_title", "2026-11-01")[0]["title"], "")
        changed = variant_examples([item], "no_visual", "2026-11-01")[0]
        self.assertNotIn("visual_embedding", changed)
        self.assertNotIn("visual_embedding_json", changed)
        self.assertNotIn("visual_image_count", changed)
        self.assertEqual(item["title"], "Original")
        self.assertEqual(item["visual_embedding"], [1.0])

    def test_decay_is_training_only_and_uses_cutoff_not_wall_clock(self):
        item = {"sample_weight": 2.0, "feedback_at": "2026-08-03"}
        self.assertEqual(variant_examples([item], "decay_90_days", "2026-11-01")[0]["sample_weight"], 1.0)
        self.assertEqual(variant_examples([{}], "decay_90_days", "2026-11-01", training=False), [{}])


if __name__ == "__main__":
    unittest.main()
