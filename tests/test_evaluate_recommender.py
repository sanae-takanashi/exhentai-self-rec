import sqlite3
import unittest

from exh_rec import db
from scripts.evaluate_recommender import restrict_temporal_training_data


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


if __name__ == "__main__":
    unittest.main()
