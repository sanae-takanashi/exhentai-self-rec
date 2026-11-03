import http.client
import json
import sqlite3
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

from exh_rec import app, db, exhentai
from exh_rec.exhentai import Gallery, GalleryBatch
from exh_rec.recency import recent_gallery_cutoff


def gallery(gid):
    return Gallery(url=f"https://exhentai.org/g/{gid}/abcdef/", gid=str(gid), token="abcdef", title=f"Gallery {gid}",
                   posted_at=recent_gallery_cutoff())


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

    def test_inflight_fetch_is_readable_over_http_while_remote_page_is_pending(self):
        from scripts.launcher_fetch import read_fetch_activity
        entered, release = threading.Event(), threading.Event()
        failures = []

        def remote_page(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic remote page wait timed out")
            return GalleryBatch([gallery(10)])

        def fetch():
            try:
                app.fetch_and_store()
            except Exception as error:
                failures.append(type(error).__name__)

        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        worker = threading.Thread(target=fetch, daemon=True)
        with patch.object(app, "fetch_galleries", side_effect=remote_page), patch.object(app.Handler, "log_message"), patch("builtins.print"):
            serving.start()
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                payload = read_fetch_activity(f"http://127.0.0.1:{server.server_port}/")
                self.assertEqual(payload["availability"], "available")
                data = payload["activity"]
                self.assertTrue(data["running"])
                self.assertEqual(data["stage"], "fetching_pages")
                self.assertEqual(data["queries"][0]["state"], "running")
                self.assertEqual(data["pages_done"], 0)
                self.assertEqual(data["page_budget"], 2)
            finally:
                release.set()
                worker.join(10)
                server.shutdown()
                server.server_close()
                serving.join()
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(app.FETCH_ACTIVITY.snapshot()["activity"]["running"])

    def test_partial_run_resumes_and_promotes_frozen_head_only_after_catch_up(self):
        with db.connect() as conn:
            conn.execute("UPDATE fetch_query_state SET last_success_at='2000-01-01 00:00:00'")
        pages = [GalleryBatch([gallery(30)], next_url="https://exhentai.org/?next=29"),
                 GalleryBatch([gallery(29)], next_url="https://exhentai.org/?next=28")]
        with patch.object(app, "fetch_galleries", side_effect=pages):
            first = app.fetch_and_store()
        self.assertEqual(first["status"], "partial")
        self.assertFalse(first["ok"])
        activity = app.FETCH_ACTIVITY.snapshot()["activity"]
        self.assertFalse(activity["running"])
        self.assertEqual(activity["status"], "partial")
        self.assertEqual(activity["queries"][0]["state"], "partial")
        self.assertEqual(activity["pages_done"], 2)
        self.assertEqual(activity["page_budget"], 2)
        self.assertEqual(activity["incomplete_count"], 1)
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
        activity = app.FETCH_ACTIVITY.snapshot()["activity"]
        self.assertEqual(activity["status"], "success")
        self.assertTrue(activity["queries"][0]["resuming"])
        self.assertEqual(activity["queries"][0]["state"], "done")
        self.assertEqual(activity["pages_done"], 1)
        self.assertEqual(activity["incomplete_count"], 0)
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

    def test_age_boundary_stops_without_three_anchor_matches_and_preserves_history(self):
        fresh, boundary, old = gallery(40), gallery(30), gallery(20)
        fresh.posted_at, boundary.posted_at, old.posted_at = "2026-11-03", "2026-08-03", "2026-08-02"
        with db.connect() as conn:
            app.store_galleries(conn, [old])
            app.save_fetch_query_state(conn, {"query": None, "source": "recent"},
                                       [gallery(gid).url for gid in (12, 11, 10)], True, 1)
        with patch.object(app, "recent_gallery_cutoff", return_value="2026-08-03"), patch.object(
            app, "fetch_galleries", return_value=GalleryBatch([fresh, boundary, old], next_url="https://exhentai.org/?next=19")
        ) as fetch:
            result = app.fetch_and_store()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stored"], 2)
        self.assertTrue(self.state()["caught_up"])
        self.assertIsNone(self.state()["resume_url"])
        self.assertEqual(self.state()["anchors"], [fresh.url, boundary.url])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 3)
        activity = app.FETCH_ACTIVITY.snapshot()["activity"]
        self.assertEqual(activity["min_posted_date"], "2026-08-03")
        self.assertTrue(activity["queries"][0]["age_limit_reached"])

    def test_all_old_resume_finishes_and_next_run_starts_at_head(self):
        old = gallery(20)
        old.posted_at = "2026-07-15"
        with db.connect() as conn:
            app.store_galleries(conn, [old])
            app.save_fetch_query_state(conn, {"query": None, "source": "recent"}, [old.url], False, 25,
                                       resume_url="https://exhentai.org/?next=20", pending_anchors=[old.url])
        with patch.object(app, "recent_gallery_cutoff", return_value="2026-08-03"), patch.object(
            app, "fetch_galleries", return_value=GalleryBatch([old], next_url="https://exhentai.org/?next=19")
        ) as fetch:
            result = app.fetch_and_store()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stored"], 0)
        self.assertEqual(self.state()["anchors"], [])
        self.assertIsNone(self.state()["resume_url"])
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch([gallery(50)])) as fetch:
            app.fetch_and_store()
        self.assertIsNone(fetch.call_args.kwargs["next_url"])

    def test_missing_publication_date_does_not_advance_or_store(self):
        unknown = gallery(30)
        unknown.posted_at = None
        before = self.state()
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch([unknown], next_url="https://exhentai.org/?next=29")):
            result = app.fetch_and_store()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.state(), before)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 0)

    def test_missing_publication_date_can_be_resolved_before_filtering(self):
        unknown = gallery(30)
        unknown.posted_at = None

        def metadata(_cookie, galleries, **_kwargs):
            for item in galleries:
                item.posted_at = "2026-07-01"
            return len(galleries)

        with patch.object(app, "recent_gallery_cutoff", return_value="2026-08-03"), patch.object(
            app, "fetch_galleries", return_value=GalleryBatch([unknown], next_url="https://exhentai.org/?next=29")
        ), patch.object(app, "enrich_covers_via_api", side_effect=metadata):
            result = app.fetch_and_store()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stored"], 0)
        self.assertIsNone(self.state()["resume_url"])

    def test_manual_search_also_stops_at_age_limit_without_touching_auto_state(self):
        before = self.state()
        old = gallery(20)
        old.posted_at = "2026-07-01"
        with patch.object(app, "recent_gallery_cutoff", return_value="2026-08-03"), patch.object(
            app, "fetch_galleries", return_value=GalleryBatch([old], next_url="https://exhentai.org/?next=19&f_search=artist%3Atest")
        ) as fetch:
            result = app.fetch_and_store(force_query="artist:test")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stored"], 0)
        self.assertEqual(self.state(), before)

    def test_missing_listing_date_uses_known_database_date_without_metadata_request(self):
        stored, undated = gallery(10), gallery(10)
        undated.posted_at = None
        with db.connect() as conn:
            app.store_galleries(conn, [stored])
        with patch.object(app, "fetch_galleries", return_value=GalleryBatch([undated])), patch.object(
            app, "enrich_covers_via_api", return_value=0
        ) as metadata:
            result = app.fetch_and_store()
        self.assertEqual(result["status"], "success")
        self.assertEqual(undated.posted_at, stored.posted_at)
        self.assertEqual(metadata.call_count, 1)


if __name__ == "__main__":
    unittest.main()
