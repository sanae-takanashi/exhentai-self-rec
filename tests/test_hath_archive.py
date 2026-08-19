import unittest
from unittest.mock import patch

from hath_observer.archive import ArchiveError, build_plan, normalize_request


class HathArchiveHelperTest(unittest.TestCase):
    def test_normalize_request_supports_custom_archive_and_cleanup(self):
        request = normalize_request(
            {
                "selected": ["Gallery One", "Existing.zip"],
                "archive_name": "manual-batch",
                "upload": True,
                "mega_destination": "/H@H/Manual",
                "trash_sources": True,
                "trash_archive_after_upload": True,
            }
        )

        self.assertEqual(request["archive_name"], "manual-batch.zip")
        self.assertEqual(request["selected"], ["Gallery One", "Existing.zip"])
        self.assertTrue(request["trash_sources"])
        self.assertTrue(request["trash_archive_after_upload"])

    def test_normalize_request_rejects_paths_and_archive_cleanup_without_upload(self):
        with self.assertRaisesRegex(ArchiveError, "safe file name"):
            normalize_request({"selected": ["../outside"], "upload": False})
        with self.assertRaisesRegex(ArchiveError, "requires upload"):
            normalize_request(
                {
                    "selected": ["Gallery One"],
                    "upload": False,
                    "trash_archive_after_upload": True,
                }
            )
        with self.assertRaisesRegex(ArchiveError, "absolute MEGA path"):
            normalize_request(
                {
                    "selected": ["Gallery One"],
                    "upload": True,
                    "mega_destination": "-q",
                }
            )

    def test_build_plan_is_read_only_and_reports_exact_actions(self):
        candidates = [
            {"name": "Gallery One", "kind": "gallery", "size_bytes": 100, "file_count": 2},
            {"name": "Existing.zip", "kind": "zip", "size_bytes": 50, "file_count": 1},
        ]
        with patch("hath_observer.archive.scan_candidates", return_value=candidates), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ):
            plan = build_plan(
                {
                    "selected": ["Gallery One", "Existing.zip"],
                    "archive_name": "batch.zip",
                    "upload": True,
                    "mega_destination": "/H@H",
                    "trash_sources": True,
                },
                "/downloads",
                "/archives",
            )

        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["input_bytes"], 150)
        self.assertEqual(plan["input_files"], 3)
        self.assertEqual([action["kind"] for action in plan["actions"]], ["archive", "upload", "trash-sources"])


if __name__ == "__main__":
    unittest.main()
