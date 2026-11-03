import sqlite3
import unittest

from exh_rec import db
from scripts.evaluate_recommender import (
    confidence_interval, evaluation_windows, gallery_payload, latest_feedback, materialize_history,
    non_overlapping_starts, restrict_candidate_visual_history, restrict_temporal_training_data,
)


class TemporalEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.conn.execute("INSERT INTO hath_clients(client_id) VALUES ('client-a')")
        for gid in ("1", "2", "3"):
            self.conn.execute(
                "INSERT INTO galleries(url, gid, title) VALUES (?, ?, ?)",
                (f"https://exhentai.org/g/{gid}/token/", gid, f"Gallery {gid}"),
            )
        self.conn.execute(
            "INSERT INTO feedback(gallery_url, vote, created_at) VALUES (?, 1, ?)",
            ("https://exhentai.org/g/1/token/", "2026-01-01 00:00:00"),
        )
        self.training_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.conn.execute(
            "INSERT INTO feedback(gallery_url, vote, created_at) VALUES (?, -1, ?)",
            ("https://exhentai.org/g/2/token/", "2026-03-01 00:00:00"),
        )
        self.conn.execute(
            "INSERT INTO gallery_marks(gallery_url, kind) VALUES (?, 'favorite')",
            ("https://exhentai.org/g/3/token/",),
        )

    def tearDown(self):
        self.conn.close()

    def test_future_hath_downloads_are_excluded_from_training_fold(self):
        downloads = (
            ("1", "2026-01-15T00:00:00Z"),
            ("2", "2026-02-15T00:00:00Z"),
            ("3", None),
        )
        for gid, completed_at in downloads:
            self.conn.execute(
                """
                INSERT INTO hath_downloads(
                    client_id, gid, gallery_url, status, completed_at, updated_at
                ) VALUES ('client-a', ?, ?, 'completed', ?, '2026-03-01T00:00:00Z')
                """,
                (gid, f"https://exhentai.org/g/{gid}/token/", completed_at),
            )

        restrict_temporal_training_data(
            self.conn,
            [self.training_id],
            "2026-02-01 00:00:00",
        )

        feedback_ids = [row[0] for row in self.conn.execute("SELECT id FROM feedback")]
        download_gids = [row[0] for row in self.conn.execute("SELECT gid FROM hath_downloads")]
        mark_count = self.conn.execute("SELECT COUNT(*) FROM gallery_marks").fetchone()[0]
        self.assertEqual(feedback_ids, [self.training_id])
        self.assertEqual(download_gids, ["1"])
        self.assertEqual(mark_count, 0)

    def test_historical_marks_and_classification_overrides_are_retained(self):
        self.conn.execute("DELETE FROM gallery_marks")
        self.conn.execute(
            "INSERT INTO gallery_marks(gallery_url, kind, updated_at) VALUES (?, 'favorite', ?)",
            ("https://exhentai.org/g/1/token/", "2026-01-15 00:00:00"),
        )
        self.conn.execute(
            "INSERT INTO gallery_classification_overrides(gallery_url, classification, updated_at) VALUES (?, 'review', ?)",
            ("https://exhentai.org/g/1/token/", "2026-01-20 00:00:00"),
        )
        self.conn.execute(
            "INSERT INTO gallery_classification_overrides(gallery_url, classification, updated_at) VALUES (?, 'updates', ?)",
            ("https://exhentai.org/g/2/token/", "2026-03-01 00:00:00"),
        )

        restrict_temporal_training_data(self.conn, [self.training_id], "2026-02-01 00:00:00")

        marks = self.conn.execute("SELECT gallery_url FROM gallery_marks").fetchall()
        overrides = self.conn.execute(
            "SELECT gallery_url, classification FROM gallery_classification_overrides ORDER BY gallery_url"
        ).fetchall()
        self.assertEqual([row[0] for row in marks], ["https://exhentai.org/g/1/token/"])
        self.assertEqual(
            [tuple(row) for row in overrides],
            [("https://exhentai.org/g/1/token/", "review")],
        )

    def test_evaluation_windows_do_not_overlap(self):
        starts = non_overlapping_starts(973, 100)
        self.assertEqual(starts, [473, 573, 673, 773, 873])
        self.assertTrue(all(right - left >= 100 for left, right in zip(starts, starts[1:])))

    def test_duplicate_update_is_not_a_negative_preference_label(self):
        url = "https://exhentai.org/g/1/token/"
        self.conn.execute("INSERT INTO feedback(gallery_url,vote,reason_code,created_at) VALUES (?, -1, 'duplicate_update', '2026-02-01')", (url,))
        self.assertNotIn(url, [row["gallery_url"] for row in latest_feedback(self.conn)])

    def test_serving_visual_statistics_cannot_read_future_or_undated_images(self):
        url = "https://exhentai.org/g/1/token/"
        for index, at in enumerate(("2026-01-01", "2026-02-01", "2026-03-01", "unknown")):
            self.conn.execute("""INSERT INTO gallery_visual_images(
                gallery_url,image_key,embedding_json,embedding_version,created_at) VALUES (?, ?, '[1,0]', 'test', ?)""",
                (url, str(index), at))
        count = restrict_candidate_visual_history(self.conn, [{"url": url, "feature_snapshot_at": "2026-02-01"}])
        self.assertEqual(count, 2)
        self.assertEqual([row[0] for row in self.conn.execute("SELECT image_key FROM gallery_visual_images ORDER BY image_key")], ["0", "1"])

    def test_explicit_test_range_preserves_training_prefix_and_all_new_labels(self):
        feedback = [{"created_at": f"2026-10-{day:02d} 00:00:00"}
                    for day in range(1, 32) for _ in range(10)]
        windows = evaluation_windows(feedback, 75, "2026-10-09", "2026-10-31")
        self.assertEqual(windows, [(80, 155), (155, 230), (230, 300)])
        self.assertEqual(sum(end - start for start, end in windows), 220)

    def test_short_tail_is_merged_not_silently_dropped(self):
        feedback = [{"created_at": "2026-11-01"} for _ in range(160)]
        self.assertEqual(evaluation_windows(feedback, 75, "2026-11-01"), [(0, 75), (75, 160)])

    def test_range_can_be_empty_without_falling_back_to_old_labels(self):
        feedback = [{"created_at": "2026-10-01"} for _ in range(100)]
        self.assertEqual(evaluation_windows(feedback, 75, "2026-11-01"), [])

    def test_range_normalizes_timezone_and_rejects_reversed_bounds(self):
        feedback = [{"created_at": "2026-11-01 00:00:00"} for _ in range(30)]
        self.assertEqual(evaluation_windows(feedback, 75, "2026-11-01T08:00:00+08:00"), [(0, 30)])
        with self.assertRaises(ValueError):
            evaluation_windows(feedback, 75, "2026-11-02", "2026-11-01")
        with self.assertRaises(ValueError):
            evaluation_windows(feedback, 0)

    def test_confidence_interval_is_reported_for_multiple_folds(self):
        interval = confidence_interval([0.7, 0.8, 0.9])
        self.assertIsNotNone(interval)
        self.assertLess(interval[0], 0.8)
        self.assertGreater(interval[1], 0.8)

    def test_single_fold_cannot_claim_zero_uncertainty(self):
        self.assertIsNone(confidence_interval([0.8]))

    def test_prefix_preserves_old_votes_and_neutral_resets(self):
        url = "https://exhentai.org/g/1/token/"
        self.conn.execute("INSERT INTO feedback(gallery_url, vote, created_at) VALUES (?, 0, ?)",
                          (url, "2026-01-20 00:00:00"))
        restrict_temporal_training_data(self.conn, [], "2026-02-01 00:00:00")
        self.assertEqual([row[0] for row in self.conn.execute("SELECT vote FROM feedback ORDER BY id")], [1, 0])

    def test_future_classification_sample_labels_are_excluded(self):
        for labeled_at in ("2026-01-20 00:00:00", "2026-03-01 00:00:00", None):
            self.conn.execute("""INSERT INTO gallery_classification_samples(
                gallery_url, sampling_strategy, selection_probability, classification, sampled_at, labeled_at)
                VALUES (?, 'uncertainty', 1, 'updates', '2026-01-01 00:00:00', ?)""",
                ("https://exhentai.org/g/1/token/", labeled_at))
        restrict_temporal_training_data(self.conn, [], "2026-02-01 00:00:00")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM gallery_classification_samples").fetchone()[0], 1)

    def test_historical_serving_materializes_snapshot_not_current_gallery(self):
        url = "https://exhentai.org/g/1/token/"
        self.conn.execute("UPDATE galleries SET first_seen_at='2026-01-01', last_seen_at='2026-01-01' WHERE url=?", (url,))
        db.snapshot_gallery_features(self.conn, url, "test", "2026-01-01 00:00:00")
        self.conn.execute("UPDATE galleries SET title='Future', parent_url='future', rating=5, review_excluded=1 WHERE url=?", (url,))
        history = materialize_history(self.conn, "2026-02-01 00:00:00")
        row = self.conn.execute("SELECT title, parent_url, rating, review_excluded FROM galleries WHERE url=?", (url,)).fetchone()
        self.assertEqual(tuple(row), ("Gallery 1", None, None, 0))
        self.assertEqual(history["historical_galleries"], 1)
        self.assertEqual(history["complete_galleries"], 1)

    def test_v1_snapshot_does_not_inherit_future_structural_metadata(self):
        url = "https://exhentai.org/g/1/token/"
        self.conn.execute("INSERT INTO gallery_feature_snapshots(gallery_url, title, captured_at) VALUES (?, 'Past', '2026-01-01')", (url,))
        self.conn.execute("UPDATE galleries SET parent_url='future' WHERE url=?", (url,))
        payload = gallery_payload(self.conn, url, "2026-02-01", strict_snapshot=True)
        self.assertIsNone(payload["parent_url"])
        self.assertEqual(payload["snapshot_schema"], 1)


if __name__ == "__main__":
    unittest.main()
