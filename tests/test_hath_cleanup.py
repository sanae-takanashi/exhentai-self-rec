import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hath_observer import archive


class HathCleanupTest(unittest.TestCase):
    def setUp(self):
        # Keep synthetic fixtures for inspection; no real files are deleted by these tests.
        self.root = Path(tempfile.mkdtemp(prefix="hath-cleanup-test-"))
        self.download = self.root / "download"
        self.archives = self.root / "archive"
        self.download.mkdir()
        self.archives.mkdir()
        (self.download / "gallery").mkdir()
        (self.download / "gallery" / "page.jpg").write_bytes(b"image")
        (self.download / "existing.zip").write_bytes(b"existing")
        (self.download / "untouched").mkdir()
        (self.archives / "old.zip").write_bytes(b"archive")
        (self.archives / ".partial.zip").write_bytes(b"partial")
        (self.archives / "notes.txt").write_bytes(b"notes")

    def plan(self, **values):
        request = {"operation": "cleanup-only", "selected": ["gallery"], "archives": ["old.zip"]}
        request.update(values)
        return archive.build_plan(request, str(self.download), str(self.archives))

    def test_preview_is_read_only_and_has_no_upload_or_pack_action(self):
        with patch.object(archive, "command_path", side_effect=AssertionError("No commands")), patch.object(
            archive, "mega_identity", side_effect=AssertionError("No MEGA")
        ), patch.object(archive, "move_without_clobber") as move, patch.object(
            archive, "permanently_delete"
        ) as delete:
            plan = self.plan()
        self.assertEqual(plan["kind"], "cleanup-only")
        self.assertEqual(plan["selected_count"], 2)
        self.assertEqual(plan["input_bytes"], 12)
        self.assertEqual(plan["input_files"], 2)
        self.assertEqual(plan["request"]["cleanup_mode"], "trash")
        self.assertNotIn("upload", plan["request"])
        self.assertEqual([item["kind"] for item in plan["actions"]], ["trash", "trash"])
        self.assertEqual(set(plan["request"]["snapshot"]), {"sources/gallery", "archives/old.zip"})
        move.assert_not_called()
        delete.assert_not_called()

    def test_archives_only_does_not_require_download_directory(self):
        plan = archive.build_plan({"operation": "cleanup-only", "archives": ["old.zip"]},
                                  str(self.root / "missing"), str(self.archives))
        self.assertEqual(plan["source_count"], 0)
        self.assertEqual(plan["archive_count"], 1)

    def test_sources_only_does_not_require_archive_to_exist(self):
        plan = archive.build_plan({"operation": "cleanup-only", "selected": ["existing.zip"]},
                                  str(self.download), str(self.root / "missing"))
        self.assertEqual(plan["selected_count"], 1)

    def test_scans_only_top_level_nonhidden_zip_archives(self):
        (self.archives / ".trash").mkdir()
        (self.archives / ".trash" / "hidden.zip").write_bytes(b"hidden")
        (self.archives / "subdir").mkdir()
        self.assertEqual([item["name"] for item in archive.scan_archives(str(self.archives))], ["old.zip"])

    def test_rejects_empty_invalid_or_too_large_selections(self):
        for values in ({"selected": [], "archives": []}, {"selected": "gallery"},
                       {"archives": None}, {"selected": ["../outside"]},
                       {"selected": ["/outside"]}, {"archives": [".partial.zip"]},
                       {"archives": ["notes.txt"]}, {"cleanup_mode": "invalid"},
                       {"selected": ["item{0}".format(i) for i in range(501)]}):
            with self.subTest(values=values), self.assertRaises(archive.ArchiveError):
                self.plan(**values)

    def test_deduplicates_selections(self):
        plan = self.plan(selected=["gallery", "gallery"], archives=["old.zip", "old.zip"])
        self.assertEqual(plan["selected_count"], 2)

    def test_same_name_in_different_scopes_has_distinct_paths(self):
        (self.download / "old.zip").write_bytes(b"source")
        plan = self.plan(selected=["old.zip"])
        self.assertEqual(len(plan["request"]["snapshot"]), 2)
        self.assertNotEqual(plan["selected"][0]["path"], plan["selected"][1]["path"])

    def test_rejects_duplicate_physical_targets(self):
        with self.assertRaisesRegex(archive.ArchiveError, "distinct paths"):
            archive.build_plan({"operation": "cleanup-only", "selected": ["old.zip"],
                                "archives": ["old.zip"]}, str(self.archives), str(self.archives))

    def test_changed_or_missing_selection_stops_before_any_mutation(self):
        for change in ("content", "added", "missing"):
            with self.subTest(change=change):
                plan = self.plan()
                page = self.download / "gallery" / "page.jpg"
                if change == "content":
                    page.write_bytes(b"changed image")
                elif change == "added":
                    (self.download / "gallery" / "new.jpg").write_bytes(b"new image")
                else:
                    (self.archives / "old.zip").rename(self.archives / "saved.zip")
                with patch.object(archive, "move_without_clobber") as move, patch.object(
                    archive, "permanently_delete"
                ) as delete, self.assertRaises(archive.ArchiveError):
                    archive.execute_plan(plan, str(self.download), str(self.archives))
                move.assert_not_called()
                delete.assert_not_called()

    def test_snapshot_rejects_same_size_changed_file(self):
        plan = self.plan()
        page = self.download / "gallery" / "page.jpg"
        before = page.stat()
        page.write_bytes(b"other")
        os.utime(str(page), ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        with self.assertRaisesRegex(archive.ArchiveError, "changed"):
            archive.build_plan(plan["request"], str(self.download), str(self.archives))

    def test_execute_requires_preview_snapshot(self):
        with self.assertRaisesRegex(archive.ArchiveError, "snapshot"):
            archive.execute_cleanup_plan({"request": {"operation": "cleanup-only"}},
                                         str(self.download), str(self.archives))

    def test_rejects_links_and_special_files(self):
        with patch.object(archive.os.path, "realpath", return_value="outside"):
            with self.assertRaisesRegex(archive.ArchiveError, "symbolic links"):
                archive.cleanup_fingerprint(str(self.download / "gallery"))
        with patch.object(archive.stat, "S_ISDIR", return_value=False), patch.object(
            archive.stat, "S_ISREG", return_value=False
        ), self.assertRaisesRegex(archive.ArchiveError, "special files"):
            archive.cleanup_fingerprint(str(self.download / "gallery"))

    def test_cleanup_moves_only_selected_items_without_external_commands(self):
        plan = self.plan()
        with patch.object(archive, "command_path", side_effect=AssertionError("No commands")), patch.object(
            archive, "mega_identity", side_effect=AssertionError("No MEGA")
        ), patch.object(archive, "job_identifier", return_value="job"), patch.object(
            archive, "move_without_clobber", side_effect=lambda source, dest: os.path.join(dest, os.path.basename(source))
        ) as move, patch.object(archive, "permanently_delete") as delete, patch.object(archive, "emit") as emit:
            archive.execute_plan(plan, str(self.download), str(self.archives))
        paths = [args[0][0] for args in move.call_args_list]
        self.assertEqual(paths, [str(self.download / "gallery"), str(self.archives / "old.zip")])
        self.assertEqual(move.call_args_list[0][0][1], str(self.archives / ".trash" / "job" / "sources"))
        self.assertEqual(move.call_args_list[1][0][1], str(self.archives / ".trash" / "job" / "archives"))
        delete.assert_not_called()
        result = emit.call_args_list[-1][1]["result"]
        self.assertEqual(result["cleaned_count"], 2)
        self.assertEqual(result["freed_bytes"], 0)

    def test_delete_mode_is_explicit_and_deletes_only_selected_paths(self):
        plan = self.plan(cleanup_mode="delete")
        with patch.object(archive, "command_path", side_effect=AssertionError("No commands")), patch.object(
            archive, "move_without_clobber"
        ) as move, patch.object(archive, "permanently_delete") as delete, patch.object(archive, "emit") as emit:
            archive.execute_plan(plan, str(self.download), str(self.archives))
        self.assertEqual([args[0][0] for args in delete.call_args_list],
                         [str(self.download / "gallery"), str(self.archives / "old.zip")])
        move.assert_not_called()
        self.assertEqual(emit.call_args_list[-1][1]["result"]["freed_bytes"], 12)


if __name__ == "__main__":
    unittest.main()
