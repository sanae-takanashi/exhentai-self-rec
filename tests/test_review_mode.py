"""Isolated undo contract tests. Synthetic databases are retained for inspection."""
import tempfile
import unittest
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from exh_rec import db
from exh_rec.app import ApiError, Handler
from exh_rec.exhentai import Gallery
from exh_rec.recommender import record_feedback, store_galleries


class ReviewUndoTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="exh-review-undo-"))
        self.data_patch = patch.object(db, "DATA_DIR", self.directory)
        self.path_patch = patch.object(db, "DB_PATH", self.directory / "test.sqlite3")
        self.data_patch.start()
        self.path_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.addCleanup(self.path_patch.stop)
        db.init_db()
        self.url = "https://example.test/g/1/a/"
        self.other = "https://example.test/g/2/b/"
        with db.connect() as conn:
            store_galleries(conn, [Gallery(url=url, gid=str(i), token="a", title="Synthetic")
                                   for i, url in enumerate([self.url, self.other])])

    def post(self, payload, path="/api/feedback/undo"):
        sent = []
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.read_json = lambda: payload
        handler.send_json = lambda body, status=HTTPStatus.OK: sent.append((body, status))
        handler.handle_error = lambda exc: (_ for _ in ()).throw(exc)
        with patch("exh_rec.app.retrain_model", side_effect=AssertionError("No synchronous retraining")), patch(
            "exh_rec.app.response_page_payload", side_effect=AssertionError("No reranking")
        ):
            handler.do_POST()
        return sent[0]

    def rate(self, score=5, url=None):
        with db.connect() as conn:
            record_feedback(conn, url or self.url, score=score, retrain=False)
            return conn.execute("SELECT MAX(id) FROM feedback").fetchone()[0]

    def rows(self):
        with db.connect() as conn:
            return [tuple(row) for row in conn.execute("SELECT id, gallery_url, score FROM feedback ORDER BY id")]

    def test_undo_preserves_older_events_and_other_galleries(self):
        first = self.rate(2)
        last = self.rate(5)
        other = self.rate(4, self.other)
        payload, status = self.post({"gallery_url": self.url, "feedback_id": last})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["ok"])
        self.assertNotIn("items", payload)
        self.assertEqual(self.rows(), [(first, self.url, 2), (other, self.other, 4)])

    def test_stale_wrong_gallery_missing_and_repeated_ids_fail_closed(self):
        first = self.rate(1)
        last = self.rate(4)
        expected = self.rows()
        for url, identifier in [(self.url, first), (self.other, last), (self.url, last + 50)]:
            with self.subTest(url=url, identifier=identifier), self.assertRaises(ApiError) as caught:
                self.post({"gallery_url": url, "feedback_id": identifier})
            self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(self.rows(), expected)
        self.post({"gallery_url": self.url, "feedback_id": last})
        with self.assertRaises(ApiError):
            self.post({"gallery_url": self.url, "feedback_id": last})

    def test_invalid_ids_are_not_coerced(self):
        last = self.rate()
        for identifier in [True, False, 0, -1, 2**63, float(last), str(last), None, [], {}]:
            with self.subTest(identifier=identifier), self.assertRaises(ApiError) as caught:
                self.post({"gallery_url": self.url, "feedback_id": identifier})
            self.assertEqual(caught.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(len(self.rows()), 1)

    def test_audit_outcome_reopens_without_changing_sampling_or_visibility(self):
        with db.connect() as conn:
            conn.execute("""INSERT INTO low_interest_audits
                (audit_date, gallery_url, selection_probability, frame_size, exposed_at, visible_at)
                VALUES ('2026-10-26', ?, 0.25, 40, 'exposed-fixture', 'visible-fixture')""", (self.url,))
            conn.execute("INSERT INTO gallery_marks(gallery_url, kind) VALUES (?, 'favorite')", (self.url,))
        last = self.rate()
        self.post({"gallery_url": self.url, "feedback_id": last})
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM low_interest_audits").fetchone()
            self.assertEqual(row["status"], "incomplete")
            for key in ["outcome_source", "outcome_positive", "completed_at", "feedback_id"]:
                self.assertIsNone(row[key])
            self.assertEqual(row["selection_probability"], 0.25)
            self.assertEqual(row["frame_size"], 40)
            self.assertEqual(row["visible_at"], "visible-fixture")
            self.assertEqual(row["exposed_at"], "exposed-fixture")
            self.assertEqual(conn.execute("SELECT kind FROM gallery_marks").fetchone()[0], "favorite")

    def test_neutral_rating_undo_and_capability_advertisement(self):
        payload, _ = self.post({"gallery_url": self.url, "score": 3, "enrich_feedback": False}, "/api/feedback")
        self.assertTrue(payload["feedback_undo_supported"])
        identifier = payload["feedback_update"]["latest_feedback_id"]
        self.post({"gallery_url": self.url, "feedback_id": identifier})
        self.assertEqual(self.rows(), [])

    def test_concurrent_http_undos_commit_once_with_independent_database_readback(self):
        identifier = self.rate()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def undo():
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/feedback/undo",
                data=json.dumps({"gallery_url": self.url, "feedback_id": identifier}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with opener.open(request, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                error.close()
                return error.code

        try:
            with ThreadPoolExecutor(max_workers=2) as clients:
                statuses = sorted(clients.map(lambda _: undo(), range(2)))
            self.assertEqual(statuses, [200, 409])
            self.assertEqual(self.rows(), [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
