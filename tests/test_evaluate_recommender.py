import sqlite3
import unittest

from exh_rec import db
from scripts.evaluate_recommender import (
    confidence_interval, gallery_payload, materialize_history, non_overlapping_starts,
    restrict_temporal_training_data,
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
