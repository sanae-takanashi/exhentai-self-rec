import hashlib
import json
import ast
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hath_observer.core import parse_download_directory_name
from scripts.audit_hath_library import audit_database, main, remote_scan, scan_library


@contextmanager
def retained_fixture():
    yield Path(tempfile.mkdtemp(prefix="hath-library-audit-fixture-"))


class HathLibraryAuditTests(unittest.TestCase):
    def test_scan_ignores_incomplete_directories_and_samples_complete_only(self):
        with retained_fixture() as temporary:
            root = Path(temporary)
            complete = root / "Fixture [123-1280x]"
            complete.mkdir()
            (complete / "galleryinfo.txt").write_text(
                "https://exhentai.org/g/123/abcdef/", encoding="utf-8")
            for page in range(5):
                (complete / ("%03d.jpg" % page)).write_bytes(b"identical fixture")
            incomplete = root / "Fixture [124-1280x]"
            incomplete.mkdir()
            (incomplete / "001.jpg").write_bytes(b"incomplete")
            result = scan_library(root, parse_download_directory_name)
            self.assertEqual(result["summary"]["complete_galleries"], 1)
            self.assertEqual(result["summary"]["incomplete_galleries"], 1)
            self.assertEqual(result["summary"]["image_count"], 5)
            self.assertEqual(result["summary"]["sample_count"], 3)
            self.assertEqual(result["summary"]["sample_exact_duplicate_extra_files"], 2)
            self.assertTrue(result["galleries"][0]["marker_has_matching_gallery_url"])
            self.assertNotIn("identical fixture", json.dumps(result))
            self.assertNotIn("Fixture", json.dumps(result))

    def test_database_audit_is_read_only_and_does_not_export_private_fields(self):
        with retained_fixture() as temporary:
            path = Path(temporary) / "fixture.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("""
                    CREATE TABLE hath_downloads(gid, resolution, gallery_url, status);
                    CREATE TABLE galleries(gid, url, page_count, samples_json,
                        visual_embedding_json, detail_fetched_at, last_seen_at);
                    CREATE TABLE gallery_visual_images(gallery_url);
                    CREATE TABLE settings(key, value);
                    INSERT INTO hath_downloads VALUES ('123', '1280', 'private-url', 'completed');
                    INSERT INTO hath_downloads VALUES ('124', 'org', NULL, 'completed');
                    INSERT INTO galleries VALUES ('123', 'private-url', 5, '["private-thumb"]',
                        '[1]', '2026-10-30', '2026-10-31');
                    INSERT INTO gallery_visual_images VALUES ('private-url');
                    INSERT INTO settings VALUES ('cookie_header', 'NEVER_EXPORT_THIS');
                """)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            remote = {"galleries": [
                {"gid": "123", "resolution": "1280", "image_count": 5},
                {"gid": "125", "resolution": "1280", "image_count": 2,
                 "marker_has_matching_gallery_url": True},
            ]}
            result = audit_database(path, remote)
            self.assertEqual(result["completed_records"], 2)
            self.assertEqual(result["unlinked_records"], 1)
            self.assertEqual(result["current_galleries_in_metadata"], 1)
            self.assertEqual(result["current_galleries_missing_metadata"], 1)
            self.assertEqual(result["missing_metadata_with_gallery_url_in_marker"], 1)
            self.assertEqual(result["current_galleries_with_page_vector_records"], 1)
            self.assertEqual(result["current_galleries_missing_completion_record"], 1)
            self.assertEqual(result["current_image_count_differs_from_metadata"], 0)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(sorted(child.name for child in Path(temporary).iterdir()),
                             ["fixture.sqlite3"])
            for private in ("private-url", "private-thumb", "NEVER_EXPORT_THIS"):
                self.assertNotIn(private, json.dumps(result))

    def test_missing_database_is_not_created(self):
        with retained_fixture() as temporary:
            path = Path(temporary) / "missing.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                audit_database(path, {"galleries": []})
            self.assertFalse(path.exists())

    def test_remote_source_remains_python36_compatible(self):
        source = Path(__file__).resolve().parents[1] / "scripts/audit_hath_library.py"
        ast.parse(source.read_text(encoding="utf-8"), feature_version=(3, 6))

    def test_hash_budget_is_enforced(self):
        with retained_fixture() as root:
            gallery = root / "Fixture [123-1280x]"
            gallery.mkdir()
            (gallery / "galleryinfo.txt").write_text("fixture", encoding="utf-8")
            (gallery / "001.jpg").write_bytes(b"over the fixture budget")
            with patch("scripts.audit_hath_library.HASH_TOTAL_LIMIT", 1):
                result = scan_library(root, parse_download_directory_name)
            self.assertEqual(result["summary"]["hash_skipped"], 1)
            self.assertEqual(result["summary"]["hashed_bytes"], 0)

    def test_linked_completion_marker_is_not_read(self):
        with retained_fixture() as root:
            gallery = root / "Fixture [123-1280x]"
            gallery.mkdir()
            (gallery / "galleryinfo.txt").write_text("fixture", encoding="utf-8")
            with patch.object(Path, "is_symlink", lambda path: path.name == "galleryinfo.txt"):
                result = scan_library(root, parse_download_directory_name)
            self.assertEqual(result["summary"]["complete_galleries"], 0)
            self.assertEqual(result["summary"]["incomplete_galleries"], 1)

    def test_existing_report_is_preserved_without_remote_access(self):
        with retained_fixture() as root:
            report = root / "report.json"
            report.write_text("previous evidence", encoding="utf-8")
            with patch("sys.argv", ["audit", "--output", str(report)]), \
                    patch("scripts.audit_hath_library.remote_scan") as remote:
                with self.assertRaisesRegex(ValueError, "output_already_exists"):
                    main()
            remote.assert_not_called()
            self.assertEqual(report.read_text(encoding="utf-8"), "previous evidence")

    def test_invalid_ssh_host_is_rejected_before_subprocess(self):
        with patch("scripts.audit_hath_library.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "invalid_ssh_host"):
                remote_scan({"hath_ssh_host": "-oUnsafe=yes"}, "unused")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
