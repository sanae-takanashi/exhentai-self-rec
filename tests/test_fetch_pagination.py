import http.client
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from exh_rec import app, db, exhentai
from exh_rec.exhentai import Gallery, GalleryBatch


def gallery(gid):
    return Gallery(url=f"https://exhentai.org/g/{gid}/abcdef/", gid=str(gid), token="abcdef", title=f"Gallery {gid}")


def listing(gid, following=None, query=""):
    link = f'<a id="unext" href="/?next={following}&amp;f_search={query}">Next</a>' if following else ""
    return f'<a class="glink" href="https://exhentai.org/g/{gid}/abcdef/">Gallery</a>{link}'


class PaginationTest(unittest.TestCase):
    def test_follows_next_links_and_returns_continuation(self):
        with patch.object(exhentai, "fetch_page", side_effect=[listing(30, 29), listing(29, 28)]) as fetch:
            batch = exhentai.fetch_galleries("fixture=only", None, pages=2, delay=0)
        self.assertEqual([g.gid for g in batch], ["30", "29"])
        self.assertEqual(batch.next_url, "https://exhentai.org/?next=28&f_search=")
        self.assertEqual(fetch.call_args_list[1].args[1], "https://exhentai.org/?next=29&f_search=")

    def test_resumes_without_refetching_head(self):
        with patch.object(exhentai, "fetch_page", return_value=listing(20)) as fetch:
            batch = exhentai.fetch_galleries("fixture=only", None, next_url="https://exhentai.org/?next=20")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1], "https://exhentai.org/?next=20")
        self.assertIsNone(batch.next_url)

    def test_next_link_keeps_quoted_search(self):
        html = listing(30, 29, "artist%3A%22some+name%22")
        url = exhentai.parse_search_next_url(html, exhentai.build_search_url('artist:"some name"'), 'artist:"some name"')
        self.assertIn("artist%3A%22some+name%22", url)

    def test_next_navigation_takes_priority_over_pagination_jump_links(self):
        html = listing(30, 29) + '<a href="/?next=30&amp;jump=100">Jump</a>'
        self.assertEqual(exhentai.parse_search_next_url(html, "https://exhentai.org/"),
                         "https://exhentai.org/?next=29&f_search=")

    def test_rejects_untrusted_or_changed_query_continuations(self):
        for url in (
            "https://evil.test/?next=20",
            "http://exhentai.org/?next=20",
            "https://user:password@exhentai.org/?next=20",
            "https://exhentai.org/g/20/abcdef/?next=20",
            "https://exhentai.org/?next=20&f_search=other",
            "https://exhentai.org/?next=20&next=10",
            "https://exhentai.org/?next=20&page=1",
        ):
            with self.subTest(url=url), patch.object(exhentai, "fetch_page") as fetch:
                with self.assertRaises(RuntimeError):
                    exhentai.fetch_galleries("fixture=only", None, next_url=url)
                fetch.assert_not_called()

    def test_rejects_repeated_results_even_when_next_id_changes(self):
        with patch.object(exhentai, "fetch_page", side_effect=[listing(30, 29), listing(30, 28)]):
            with self.assertRaisesRegex(RuntimeError, "repeated"):
                exhentai.fetch_galleries("fixture=only", None, pages=2, delay=0)

    def test_rejects_nonadvancing_next_cursor(self):
        with self.assertRaisesRegex(RuntimeError, "advance"):
            exhentai.parse_search_next_url(listing(30, 30), "https://exhentai.org/?next=30")

    def test_access_failure_is_not_treated_as_end_of_search(self):
        with patch.object(exhentai, "fetch_page", return_value="<html>login required</html>"):
            with self.assertRaisesRegex(RuntimeError, "access"):
                exhentai.fetch_galleries("fixture=only", None)
        with patch.object(exhentai, "fetch_page", return_value="<p>No hits found</p>"):
            self.assertEqual(exhentai.fetch_galleries("fixture=only", None), [])

    def test_incomplete_body_is_retried_boundedly(self):
        with patch.object(exhentai, "_fetch_page_once", side_effect=[http.client.IncompleteRead(b""), "complete"]) as fetch, patch.object(exhentai.time, "sleep"):
            self.assertEqual(exhentai.fetch_page("fixture=only", "https://exhentai.org/"), "complete")
        self.assertEqual(fetch.call_count, 2)
        with patch.object(exhentai, "_fetch_page_once", side_effect=http.client.IncompleteRead(b"")) as fetch, patch.object(exhentai.time, "sleep"):
            with self.assertRaises(http.client.IncompleteRead):
                exhentai.fetch_page("fixture=only", "https://exhentai.org/")
        self.assertEqual(fetch.call_count, 3)


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="exh-fetch-test-"))
        self.patches = [
            patch.object(db, "DATA_DIR", self.directory),
            patch.object(db, "DB_PATH", self.directory / "test.sqlite3"),
            patch.object(app, "enrich_covers_via_api", return_value=0),
            patch.object(app, "recommend_page", return_value={"items": []}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        db.init_db()
        with db.connect() as conn:
            for key, value in {
                "cookie_header": "fixture=only", "fetch_pages": "1",
                "stale_fetch_extra_pages": "1", "detail_fetch_limit": "0", "learned_query_limit": "0",
            }.items():
                db.set_setting(conn, key, value)
            app.save_fetch_query_state(conn, {"query": None, "source": "recent"}, [gallery(10).url], True, 1)

    def state(self):
        with db.connect() as conn:
            return app.get_fetch_query_state(conn, {"query": None, "source": "recent"})

    def test_partial_run_resumes_and_promotes_frozen_head_only_after_catch_up(self):
        with db.connect() as conn:
            conn.execute("UPDATE fetch_query_state SET last_success_at='2000-01-01 00:00:00'")
        pages = [GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29"),
                 GalleryBatch([gallery(29)], next_url="https://exhentai.org/?next=28")]
        with patch.object(app, "fetch_galleries", side_effect=pages):
            first = app.fetch_and_store()
        self.assertEqual(first["status"], "partial")
        self.assertFalse(first["ok"])
        self.assertEqual(self.state()["anchors"], [gallery(10).url])
        self.assertEqual(self.state()["pending_anchors"], [gallery(30).url])
        self.assertEqual(self.state()["resume_url"], "https://exhentai.org/?next=28")
        with db.connect() as conn:
            self.assertEqual(app.fetch_runs(conn, 1)[0]["cursor_incomplete_queries"], ["recent"])
        self.assertEqual(self.state()["last_success_at"], "2000-01-01 00:00:00")
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch([gallery(10)], next_url="https://exhentai.org/?next=9")) as fetch:
            second = app.fetch_and_store()
        self.assertEqual(fetch.call_args.kwargs["next_url"], "https://exhentai.org/?next=28")
        self.assertEqual(second["status"], "success")
        self.assertEqual(self.state()["anchors"], [gallery(30).url])
        self.assertIsNone(self.state()["resume_url"])
        self.assertEqual(self.state()["pending_anchors"], [])
        self.assertNotEqual(self.state()["last_success_at"], "2000-01-01 00:00:00")
        with patch.object(app, "fetch_galleries", side_effect=[
            GalleryBatch([gallery(35)], next_url="https://exhentai.org/?next=34"),
            GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29"),
        ]) as fetch:
            third = app.fetch_and_store()
        self.assertIsNone(fetch.call_args_list[0].kwargs["next_url"])
        self.assertEqual(third["status"], "success")
        self.assertEqual(self.state()["anchors"], [gallery(35).url])

    def test_manual_query_does_not_modify_automatic_continuation(self):
        before = self.state()
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch(
            [gallery(30)], next_url="https://exhentai.org/?next=29&f_search=artist%3Atest"
        )) as fetch:
            result = app.fetch_and_store(force_query="artist:test")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.state(), before)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fetch_query_state").fetchone()[0], 1)

    def test_initial_query_keeps_bounded_baseline_without_claiming_full_history(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM fetch_query_state")
            db.set_setting(conn, "fetch_pages", "2")
        with patch.object(app, "fetch_galleries", side_effect=[
            GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29"),
            GalleryBatch([gallery(29)], next_url="https://exhentai.org/?next=28"),
        ]) as fetch:
            result = app.fetch_and_store()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.state()["anchors"], [gallery(30).url])
        self.assertIsNone(self.state()["resume_url"])

    def test_page_failure_keeps_committed_gallery_and_retries_same_next_page(self):
        with patch.object(app, "fetch_galleries", side_effect=[
            GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29"),
            http.client.IncompleteRead(b""),
        ]):
            result = app.fetch_and_store()
        self.assertEqual(result["stored"], 1)
        self.assertEqual(result["status"], "partial")
        with db.connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM galleries WHERE url=?", (gallery(30).url,)).fetchone())
        self.assertEqual(self.state()["resume_url"], "https://exhentai.org/?next=29")
        self.assertEqual(self.state()["anchors"], [gallery(10).url])

    def test_duplicate_page_is_detected_across_run_boundary(self):
        with db.connect() as conn:
            db.set_setting(conn, "stale_fetch_extra_pages", "0")
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29")):
            app.fetch_and_store()
            second = app.fetch_and_store()
        self.assertEqual(second["status"], "failed")
        self.assertIn("repeated", second["errors"][0])
        self.assertEqual(self.state()["resume_url"], "https://exhentai.org/?next=29")
        self.assertEqual(self.state()["anchors"], [gallery(10).url])

    def test_anchor_matches_accumulate_across_batches(self):
        with db.connect() as conn:
            db.set_setting(conn, "stale_fetch_extra_pages", "0")
            app.save_fetch_query_state(conn, {"query": None, "source": "recent"},
                                       [gallery(gid).url for gid in (12, 11, 10)], True, 1)
        for gid in (12, 11, 10):
            with patch.object(app, "fetch_galleries", return_value=GalleryBatch([gallery(gid)], next_url=f"https://exhentai.org/?next={gid-1}")):
                result = app.fetch_and_store()
            self.assertEqual(result["status"], "success" if gid == 10 else "partial")
        self.assertEqual(self.state()["anchors"], [gallery(12).url])

    def test_legacy_database_migration_preserves_existing_anchors(self):
        path = self.directory / "legacy.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE fetch_query_state(
                query_key TEXT PRIMARY KEY,query_text TEXT,source TEXT,anchor_urls_json TEXT,
                caught_up INTEGER,last_page_count INTEGER,last_success_at TEXT,updated_at TEXT)""")
            conn.execute("INSERT INTO fetch_query_state VALUES('recent',NULL,'recent',?,0,25,NULL,'2026-08-01')",
                         (json.dumps([gallery(10).url]),))
        with patch.object(db, "DB_PATH", path):
            db.init_db()
            state = self.state()
        self.assertEqual(state["anchors"], [gallery(10).url])
        self.assertIsNone(state["resume_url"])
        self.assertFalse(state["caught_up"])


if __name__ == "__main__":
    unittest.main()
