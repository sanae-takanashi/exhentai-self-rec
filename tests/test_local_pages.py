import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from exh_rec.local_pages import LocalPageLibrary, SCHEMA, mean_vector, normalized, read_regular


class LocalPagesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="exh-local-pages-"))
        (self.root / "images").mkdir()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE galleries (gid TEXT, title TEXT, url TEXT)")
        self.conn.execute("INSERT INTO galleries VALUES ('1', 'Known gallery', 'https://exhentai.org/g/1/abc/')")
        self.conn.commit()
        self.blob = b"synthetic-local-image"
        self.digest = hashlib.sha256(self.blob).hexdigest()
        self.asset = {"sha256": self.digest, "suffix": "png", "bytes": len(self.blob), "width": 64, "height": 96}
        (self.root / "images" / f"{self.digest}.png").write_bytes(self.blob)
        self.document = {"schema": SCHEMA, "collected_at": "2026-10-31", "galleries": [
            self.row("1", [1, 0], [0, 1]), self.row("2", [1, 0], [1, 0]),
            self.row("3", [0, 1], [0, 1]), self.row("4", None, [1, 0]),
        ]}
        self.publish()
        self.library = LocalPageLibrary(self.root)

    def tearDown(self):
        self.conn.close()

    def row(self, gid, cover, body):
        return {"key": f"{gid}-1280", "gid": gid, "resolution": "1280", "ordering": "filename_lexical", "image_count": 20,
                "pages": [{**self.asset, "position": 1}, {**self.asset, "position": 11}, {**self.asset, "position": 20}],
                "cover": self.asset if cover else None, "cover_vector": cover, "body_vector": body}

    def publish(self):
        (self.root / "index.json").write_text(json.dumps(self.document), encoding="utf-8")

    def test_catalog_has_missing_metadata_without_invented_urls(self):
        result = self.library.catalog(self.conn)
        self.assertTrue(result["available"])
        self.assertEqual(result["comparable_count"], 3)
        known, missing = result["items"][:2]
        self.assertEqual(known["title"], "Known gallery")
        self.assertEqual(known["url"], "https://exhentai.org/g/1/abc/")
        self.assertEqual(missing["metadata_status"], "missing")
        self.assertIsNone(missing["url"])
        self.assertNotIn("vector", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_ambiguous_metadata_does_not_select_arbitrary_gallery_url(self):
        self.conn.execute("INSERT INTO galleries VALUES ('1', 'Other', 'https://e-hentai.org/g/1/def/')")
        item = self.library.catalog(self.conn)["items"][0]
        self.assertEqual(item["metadata_status"], "ambiguous")
        self.assertIsNone(item["url"])

    def test_confirmed_metadata_is_visible_without_rebuilding_local_samples(self):
        before_index = (self.root / "index.json").read_bytes()
        before = self.library.compare("2-1280", self.conn)["item"]
        self.assertEqual(before["metadata_status"], "missing")
        self.conn.execute("INSERT INTO galleries VALUES ('2', 'Fetched metadata', 'https://exhentai.org/g/2/abc/')")
        self.conn.commit()
        changes = self.conn.total_changes
        after = self.library.compare("2-1280", self.conn)["item"]
        catalog_item = next(item for item in self.library.catalog(self.conn)["items"] if item["gid"] == "2")
        self.assertEqual(after["metadata_status"], "matched")
        self.assertEqual(after["title"], "Fetched metadata")
        self.assertEqual(after["url"], "https://exhentai.org/g/2/abc/")
        self.assertEqual(catalog_item, after)
        self.assertEqual(after["pages"], before["pages"])
        self.assertEqual(after["cover"], before["cover"])
        self.assertEqual(self.library.image(self.digest)[0], self.blob)
        self.assertEqual((self.root / "index.json").read_bytes(), before_index)
        self.assertEqual(self.conn.total_changes, changes)

    def test_invalid_site_url_never_becomes_link(self):
        self.conn.execute("UPDATE galleries SET url='javascript:alert(1)'")
        self.assertIsNone(self.library.catalog(self.conn)["items"][0]["url"])

    def test_cover_and_body_have_distinct_neighbors_and_identical_candidates(self):
        result = self.library.compare("1-1280", self.conn)
        self.assertEqual(result["cover"][0]["gid"], "2")
        self.assertEqual(result["body"][0]["gid"], "3")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual({row["gid"] for row in result["cover"]}, {row["gid"] for row in result["body"]})
        self.assertNotIn("1", {row["gid"] for row in result["body"]})

    def test_excludes_all_other_resolutions_of_query(self):
        row = self.row("1", [1, 0], [0, 1])
        row.update(key="1-org", resolution="org")
        self.document["galleries"].append(row)
        self.publish()
        self.assertEqual(self.library.compare("1-1280", self.conn)["candidate_count"], 2)

    def test_candidate_resolutions_are_deduplicated(self):
        row = self.row("2", [1, 0], [1, 0])
        row.update(key="2-org", resolution="org")
        self.document["galleries"].append(row)
        self.publish()
        result = self.library.compare("1-1280", self.conn)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(len({row["gid"] for row in result["cover"]}), 2)

    def test_without_cover_still_has_pages_but_not_fabricated_comparison(self):
        result = self.library.compare("4-1280", self.conn)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["cover"], [])
        self.assertEqual(len(result["item"]["pages"]), 3)

    def test_missing_library_and_invalid_index_are_distinct(self):
        self.assertEqual(LocalPageLibrary(self.root / "absent").catalog(self.conn)["reason"], "not_configured")
        self.document["schema"] = "other"
        self.publish()
        self.assertEqual(self.library.catalog(self.conn)["reason"], "invalid_library")
        (self.root / "index.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self.library.catalog(self.conn)["reason"], "invalid_library")

    def test_single_file_without_vectors_remains_previewable(self):
        self.document["galleries"] = [self.row("5", None, None)]
        self.document["galleries"][0]["pages"] = [{**self.asset, "position": 1}]
        self.publish()
        self.assertTrue(self.library.catalog(self.conn)["available"])
        self.assertFalse(self.library.compare("5-1280", self.conn)["comparable"])

    def test_image_is_digest_checked_and_cannot_request_arbitrary_path(self):
        self.assertEqual(self.library.image(self.digest), (self.blob, "image/png"))
        for name in ["../index.json", self.digest + ".png", "f" * 64, "", "C:\\private"]:
            with self.subTest(name=name), self.assertRaises(KeyError):
                self.library.image(name)
        (self.root / "images" / f"{self.digest}.png").write_bytes(b"replaced")
        with self.assertRaises(ValueError):
            self.library.image(self.digest)

    def test_changed_index_reloads_and_corruption_does_not_use_stale_state(self):
        self.assertTrue(self.library.catalog(self.conn)["available"])
        self.document["schema"] = "invalid-replacement"
        self.publish()
        self.assertFalse(self.library.catalog(self.conn)["available"])
        with self.assertRaises(ValueError):
            self.library.image(self.digest)

    def test_index_rejects_duplicate_keys_invalid_paths_and_vectors(self):
        original = copy.deepcopy(self.document)
        for mutation in [lambda rows: rows.append(copy.deepcopy(rows[0])),
                         lambda rows: rows[0].update(key="../bad"),
                         lambda rows: rows[0]["pages"][0].update(sha256="../private"),
                         lambda rows: rows[0]["pages"][0].update(suffix="svg"),
                         lambda rows: rows[0].update(body_vector=[1, 0, 0]),
                         lambda rows: rows[0].update(cover_vector=[float("nan"), 0]),
                         lambda rows: rows[0].update(cover_vector=[True, 0]),
                         lambda rows: rows[0].update(cover_vector=[0, 0]),
                         lambda rows: rows[0].update(pages=[])]:
            self.document = copy.deepcopy(original)
            mutation(self.document["galleries"])
            self.publish()
            self.assertFalse(LocalPageLibrary(self.root).catalog(self.conn)["available"])

    def test_bounded_reader_and_reparse_rejection(self):
        image = self.root / "images" / f"{self.digest}.png"
        with self.assertRaises(ValueError):
            read_regular(image, 2)
        info = image.stat()
        class Reparse:
            st_mode = info.st_mode
            st_file_attributes = 0x400
        with patch.object(Path, "lstat", return_value=Reparse()), self.assertRaises(ValueError):
            read_regular(image, 1024)

    def test_vector_math_rejects_incompatible_or_nonfinite_input(self):
        self.assertEqual(mean_vector([]), None)
        self.assertEqual(normalized([3, 4]), [.6, .8])
        with self.assertRaises(ValueError):
            mean_vector([[1, 0], [1]])
        with self.assertRaises(ValueError):
            normalized([float("inf"), 0])

    def test_requests_do_not_modify_database(self):
        before = self.conn.total_changes
        self.library.catalog(self.conn)
        self.library.compare("1-1280", self.conn)
        self.library.image(self.digest)
        self.assertEqual(self.conn.total_changes, before)

    def test_http_routes_serve_only_allowlisted_verified_images(self):
        from contextlib import contextmanager
        from http.server import ThreadingHTTPServer
        from exh_rec import app

        class QuietHandler(app.Handler):
            def log_message(self, *args):
                pass

        @contextmanager
        def connect():
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE galleries (gid TEXT, title TEXT, url TEXT)")
            try:
                yield conn
            finally:
                conn.close()

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(app, "LOCAL_PAGES", self.library), patch.object(app.db, "connect", connect), patch.object(app, "fetch_thumbnail_bytes", side_effect=AssertionError("no network")):
                thread.start()
                with opener.open(base + "/api/local-pages", timeout=5) as response:
                    self.assertTrue(json.load(response)["available"])
                with opener.open(base + "/api/local-pages/compare?key=1-1280", timeout=5) as response:
                    self.assertEqual(json.load(response)["body"][0]["gid"], "3")
                with opener.open(base + "/api/local-pages/image?id=" + self.digest, timeout=5) as response:
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                    self.assertEqual(response.read(), self.blob)
                for path in ["/api/local-pages/image?id=../index.json", "/api/local-pages/compare?key=absent"]:
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        opener.open(base + path, timeout=5)
                    self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_preview_is_get_only_and_cannot_modify_database(self):
        from http.server import ThreadingHTTPServer
        from scripts.preview_local_pages import preview_handler
        database = self.root / "metadata.sqlite3"
        with sqlite3.connect(database) as target:
            self.conn.backup(target)
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        server = ThreadingHTTPServer(("127.0.0.1", 0), preview_handler(self.library, database))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(base + "/api/health", timeout=5) as response:
                self.assertTrue(json.load(response)["read_only"])
            with opener.open(base + "/api/local-pages", timeout=5) as response:
                self.assertEqual(len(json.load(response)["items"]), 4)
            with opener.open(base + "/api/local-pages/compare?key=1-1280", timeout=5) as response:
                self.assertEqual(json.load(response)["body"][0]["gid"], "3")
            for endpoint in ["/api/feedback", "/api/fetch", "/api/visual", "/api/settings"]:
                request = urllib.request.Request(base + endpoint, data=b"{}", headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    opener.open(request, timeout=5)
                self.assertEqual(raised.exception.code, 405)
            for endpoint in ["/static/../README.md", "/api/export"]:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    opener.open(base + endpoint, timeout=5)
                self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)


class LibraryBuildTest(unittest.TestCase):
    def test_verified_build_can_be_loaded_and_does_not_overwrite(self):
        from PIL import Image
        from scripts.build_hath_page_library import build
        from scripts.compare_hath_pages import compare
        from scripts.probe_hath_pages import write_json_new
        root = Path(tempfile.mkdtemp(prefix="exh-library-build-"))
        dataset = root / "dataset-batched"
        dataset.mkdir()
        buffer = io.BytesIO()
        Image.new("RGB", (30, 50), "red").save(buffer, format="PNG")
        blob = buffer.getvalue()
        (dataset / "sample.png").write_bytes(blob)
        sample = {"path": "sample.png", "position": 1, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        manifest = {"schema": "hath-page-experiment-v1", "production_read_only": True, "collected_at": "fixture", "galleries": [
            {"gid": "1", "resolution": "1280", "ordering": "filename_lexical", "image_count": 3, "metadata_status": "matched",
             "pages": [sample, {**sample, "path": "second.png", "position": 2}], "previews": [{**sample, "kind": "cover"}]}
        ]}
        (dataset / "second.png").write_bytes(blob)
        write_json_new(dataset / "manifest.json", manifest)
        comparison = compare(dataset, manifest, lambda blobs: [[1., 0.] for _ in blobs])
        comparison.update(schema="hath-page-comparison-v1", production_read_only=True, encoder="fixture",
                          dataset_manifest_sha256=hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest())
        write_json_new(root / "comparison.json", comparison)
        result = build(root, root / "library")
        self.assertTrue(result["ok"])
        library = LocalPageLibrary(root / "library")
        library.load()
        self.assertEqual(library.rows["1-1280"]["body_vector"], [1., 0.])
        self.assertEqual(len(library.assets), 1)
        self.assertEqual(library.image(sample["sha256"])[0], blob)
        with self.assertRaises(ValueError):
            build(root, root / "library")


if __name__ == "__main__":
    unittest.main()
