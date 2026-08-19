import sqlite3
import unittest

from exh_rec import db
from exh_rec.classification import continuing_classifier_decisions, train_continuing_classifier
from exh_rec.exhentai import Gallery
from exh_rec.recommender import set_classification_override, store_galleries


class ContinuingClassifierTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_classifier_waits_for_enough_manual_examples(self):
        gallery = Gallery(url="https://exhentai.org/g/9500/a/", gid="9500", token="a", title="[Pixiv] Small")
        store_galleries(self.conn, [gallery])
        set_classification_override(self.conn, gallery.url, "updates")
        status = train_continuing_classifier(self.conn, persist=False)
        self.assertFalse(status["ready"])
        self.assertEqual(status["status"], "insufficient-data")

    def test_classifier_learns_distinct_manual_title_patterns(self):
        galleries = []
        for index in range(24):
            updates = index < 12
            gallery = Gallery(
                url=f"https://exhentai.org/g/{9600 + index}/a/",
                gid=str(9600 + index),
                token="a",
                title=f"{'[Pixiv] Monthly Archive' if updates else 'One Shot Doujin'} Pattern {index}",
                page_count=300 if updates else 30,
            )
            galleries.append(gallery)
        candidate = Gallery(
            url="https://exhentai.org/g/9700/a/", gid="9700", token="a", title="[Pixiv] Monthly Archive New", page_count=320
        )
        store_galleries(self.conn, [*galleries, candidate])
        for index, gallery in enumerate(galleries):
            set_classification_override(self.conn, gallery.url, "updates" if index < 12 else "review")

        model = train_continuing_classifier(self.conn, persist=False)
        decisions, status = continuing_classifier_decisions(self.conn, [{"url": candidate.url, "title": candidate.title, "page_count": 320}])

        self.assertTrue(model["accepted"])
        self.assertTrue(status["accepted"])
        self.assertEqual(decisions[candidate.url]["classification"], "updates")


if __name__ == "__main__":
    unittest.main()
