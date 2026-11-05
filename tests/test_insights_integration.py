"""Exercise shared route and live-fetch wiring with isolated synthetic state."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from exh_rec import app, db
from exh_rec.exhentai import Gallery, GalleryBatch


class InsightsIntegrationTest(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="exh-insights-integration-"))
        self.addCleanup(patch.stopall)
        patch.object(db, "DATA_DIR", root).start()
        patch.object(db, "DB_PATH", root / "fixture.sqlite3").start()
        db.init_db()

    def get(self, path):
        handler = object.__new__(app.Handler)
        handler.path = path
        handler.send_json = lambda payload, **kw: payload
        with patch.object(handler, "send_json") as send, patch.object(handler, "handle_error") as error:
            handler.do_GET()
        if error.called:
            raise error.call_args.args[0]
        return send.call_args.args[0]

    def test_read_reports_have_no_model_side_effects_or_settings_disclosure(self):
        with db.connect() as conn:
            db.set_setting(conn, "cookie_header", "synthetic-never-return")
            before = list(conn.iterdump())
        with patch.object(app, "retrain_model", side_effect=AssertionError("no training")):
            for route, schema in [("/api/insights/fetch", "fetch-insights-v1"),
                                  ("/api/insights/ranking", "ranking-insights-v1")]:
                result = self.get(route)
                self.assertEqual(result["schema"], schema)
                self.assertNotIn("synthetic-never-return", json.dumps(result))
            self.assertEqual(self.get("/api/preferences")["preferences"], [])
        with db.connect() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_fetch_records_multiple_queries_atomically_and_failed_listing_separately(self):
        with db.connect() as conn:
            db.set_setting(conn, "cookie_header", "fixture=only")
            db.set_setting(conn, "detail_fetch_limit", "0")
        gallery = Gallery(url="https://exhentai.org/g/71/a/", title="Synthetic",
                          posted_at=app.recent_gallery_cutoff())
        plan = {"queries": ["artist:one", "artist:two", "artist:failed"],
                "entries": [{"query": query, "source": "manual"} for query in
                            ["artist:one", "artist:two", "artist:failed"]],
                "pages": 1, "stale_fetch_extra_pages": 0, "detail_fetch_limit": 0}
        with patch.object(app, "build_query_plan", return_value=plan["entries"]), \
             patch.object(app, "learned_query_tags", return_value=[]), \
             patch.object(app, "fetch_galleries", side_effect=[GalleryBatch([gallery]),
                         GalleryBatch([gallery]), RuntimeError("synthetic failure")]), \
             patch.object(app, "enrich_covers_via_api", return_value=0), \
             patch.object(app, "retrain_model", side_effect=AssertionError("no training")), \
             patch.object(app, "recommendation_payload", return_value={"items": []}), \
             patch("sys.stdout", io.StringIO()):
            app.fetch_and_store()
        with db.connect() as conn:
            rows = conn.execute("SELECT status, new_count, existing_count FROM fetch_listing_observations ORDER BY id").fetchall()
            self.assertEqual([tuple(row) for row in rows], [("completed", 1, 0), ("completed", 0, 1), ("failed", None, None)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM gallery_query_provenance").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
