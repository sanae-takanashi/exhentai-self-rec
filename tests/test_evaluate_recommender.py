import sqlite3
import unittest

from exh_rec import db
from scripts.evaluate_recommender import confidence_interval, non_overlapping_starts, restrict_temporal_training_data


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


if __name__ == "__main__":
    unittest.main()
