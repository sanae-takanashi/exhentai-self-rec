import unittest
from unittest.mock import call, patch

from hath_observer.archive import (
    ArchiveError,
    PURGE_TRASH_CONFIRMATION,
    build_plan,
    build_trash_plan,
    execute_plan,
    execute_trash_plan,
    mega_remote_file,
    normalize_request,
    purge_archive_trash,
)


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
                "cleanup_mode": "delete",
            }
        )

        self.assertEqual(request["archive_name"], "manual-batch.zip")
        self.assertEqual(request["selected"], ["Gallery One", "Existing.zip"])
        self.assertTrue(request["trash_sources"])
        self.assertTrue(request["trash_archive_after_upload"])
        self.assertEqual(request["cleanup_mode"], "delete")

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
        with self.assertRaisesRegex(ArchiveError, "cleanup_mode"):
            normalize_request(
                {
                    "selected": ["Gallery One"],
                    "upload": False,
                    "cleanup_mode": "somewhere-else",
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
        self.assertEqual(
            [action["kind"] for action in plan["actions"]],
            ["pack-galleries", "archive", "upload", "trash-sources"],
        )
        self.assertEqual(
            [item["archive_member"] for item in plan["selected"]],
            ["Gallery One.zip", "Existing.zip"],
        )
        self.assertIn("/H@H/batch.zip", plan["actions"][2]["message"])

    def test_mega_remote_file_preserves_archive_name(self):
        self.assertEqual(
            mega_remote_file("/H@H Archives", "20260819.zip"),
            "/H@H Archives/20260819.zip",
        )
        self.assertEqual(mega_remote_file("/", "20260819.zip"), "/20260819.zip")

    def test_build_plan_reports_permanent_cleanup(self):
        candidates = [
            {"name": "Gallery One", "kind": "gallery", "size_bytes": 100, "file_count": 2}
        ]
        with patch("hath_observer.archive.scan_candidates", return_value=candidates), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ):
            plan = build_plan(
                {
                    "selected": ["Gallery One"],
                    "upload": True,
                    "trash_sources": True,
                    "trash_archive_after_upload": True,
                    "cleanup_mode": "delete",
                },
                "/downloads",
                "/archives",
            )

        self.assertEqual(
            [action["kind"] for action in plan["actions"]],
            ["pack-galleries", "archive", "upload", "delete-sources", "delete-archive"],
        )

    def test_build_plan_rejects_conflicting_inner_zip_names(self):
        candidates = [
            {"name": "Gallery One", "kind": "gallery", "size_bytes": 100, "file_count": 2},
            {"name": "Gallery One.zip", "kind": "zip", "size_bytes": 50, "file_count": 1},
        ]
        with patch("hath_observer.archive.scan_candidates", return_value=candidates), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ):
            with self.assertRaisesRegex(ArchiveError, "same inner ZIP name"):
                build_plan(
                    {"selected": ["Gallery One", "Gallery One.zip"], "upload": False},
                    "/downloads",
                    "/archives",
                )

    def test_purge_trash_requires_exact_confirmation(self):
        with self.assertRaisesRegex(ArchiveError, "requires --confirm"):
            purge_archive_trash("/archives", "yes")
        empty_stats = {
            "path": "/archives/.trash",
            "size_bytes": 0,
            "file_count": 0,
            "job_count": 0,
        }
        with patch("hath_observer.archive.trash_stats", return_value=empty_stats), patch(
            "hath_observer.archive.os.path.isdir", return_value=False
        ):
            result = purge_archive_trash("/archives", PURGE_TRASH_CONFIRMATION)

        self.assertTrue(result["purged"])

    def test_trash_plan_is_read_only_and_rejects_missing_jobs(self):
        jobs = [
            {
                "name": "20260819T010203Z-abc",
                "size_bytes": 500,
                "file_count": 4,
                "modified_at": "2026-08-19T01:02:03Z",
            }
        ]
        with patch("hath_observer.archive.scan_trash_jobs", return_value=jobs):
            plan = build_trash_plan({"selected": [jobs[0]["name"]]}, "/archives")

        self.assertEqual(plan["kind"], "trash-delete")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["input_bytes"], 500)
        self.assertEqual(plan["input_files"], 4)
        self.assertEqual(plan["actions"][0]["kind"], "delete-trash-jobs")

        with patch("hath_observer.archive.scan_trash_jobs", return_value=[]):
            with self.assertRaisesRegex(ArchiveError, "missing or unsupported"):
                build_trash_plan({"selected": [jobs[0]["name"]]}, "/archives")

    def test_execute_trash_plan_deletes_only_selected_job_directories(self):
        plan = {
            "request": {"selected": ["job-one", "job-two"]},
            "input_bytes": 800,
            "input_files": 6,
        }
        with patch("hath_observer.archive.permanently_delete") as delete, patch(
            "hath_observer.archive.emit"
        ) as emit:
            execute_trash_plan(plan, "/archives")

        self.assertEqual(
            delete.call_args_list,
            [call("/archives/.trash/job-one"), call("/archives/.trash/job-two")],
        )
        self.assertEqual(emit.call_args_list[-1].args, ("result",))
        self.assertEqual(emit.call_args_list[-1].kwargs["result"]["freed_bytes"], 800)

    def test_execute_plan_permanently_deletes_only_after_successful_upload(self):
        plan = {
            "request": {
                "selected": ["Gallery One"],
                "archive_name": "batch.zip",
                "upload": True,
                "mega_destination": "/H@H",
                "trash_sources": True,
                "trash_archive_after_upload": True,
                "cleanup_mode": "delete",
            },
            "archive_path": "/archives/batch.zip",
            "selected_count": 1,
            "selected": [
                {
                    "name": "Gallery One",
                    "kind": "gallery",
                    "archive_member": "Gallery One.zip",
                }
            ],
        }
        with patch("hath_observer.archive.job_identifier", return_value="job"), patch(
            "hath_observer.archive.command_path", side_effect=lambda name: name
        ), patch(
            "hath_observer.archive.run_checked", return_value=""
        ) as run, patch("hath_observer.archive.os.path.isdir", return_value=True), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ), patch("hath_observer.archive.os.makedirs"), patch(
            "hath_observer.archive.os.rmdir"
        ), patch("hath_observer.archive.os.rename"), patch(
            "hath_observer.archive.permanently_delete"
        ) as delete, patch("hath_observer.archive.emit"):
            execute_plan(plan, "/downloads", "/archives")

        self.assertEqual(
            delete.call_args_list,
            [call("/downloads/Gallery One"), call("/archives/batch.zip")],
        )
        self.assertEqual(
            run.call_args_list[2:],
            [
                call(["mega-mkdir", "-p", "/H@H"]),
                call(["mega-put", "/archives/batch.zip", "/H@H/batch.zip"]),
            ],
        )
        self.assertEqual(
            run.call_args_list[:2],
            [
                call(
                    [
                        "zip",
                        "-r",
                        "/archives/.staging/job/Gallery One.zip",
                        "--",
                        "Gallery One",
                    ],
                    cwd="/downloads",
                ),
                call(
                    [
                        "zip",
                        "-0",
                        "-m",
                        "/archives/.batch.zip.job.partial",
                        "--",
                        "Gallery One.zip",
                    ],
                    cwd="/archives/.staging/job",
                ),
            ],
        )

    def test_execute_plan_stores_gallery_and_existing_zip_as_inner_members(self):
        plan = {
            "request": {
                "selected": ["Gallery One", "Existing.zip"],
                "archive_name": "batch.zip",
                "upload": False,
                "mega_destination": "/H@H",
                "trash_sources": False,
                "trash_archive_after_upload": False,
                "cleanup_mode": "trash",
            },
            "archive_path": "/archives/batch.zip",
            "selected_count": 2,
            "selected": [
                {
                    "name": "Gallery One",
                    "kind": "gallery",
                    "archive_member": "Gallery One.zip",
                },
                {
                    "name": "Existing.zip",
                    "kind": "zip",
                    "archive_member": "Existing.zip",
                },
            ],
        }
        with patch("hath_observer.archive.job_identifier", return_value="job"), patch(
            "hath_observer.archive.command_path", return_value="zip"
        ), patch("hath_observer.archive.run_checked", return_value="") as run, patch(
            "hath_observer.archive.stage_existing_zip"
        ) as stage_zip, patch("hath_observer.archive.os.path.isdir", return_value=True), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ), patch("hath_observer.archive.os.makedirs"), patch(
            "hath_observer.archive.os.rmdir"
        ), patch("hath_observer.archive.os.rename"), patch(
            "hath_observer.archive.os.path.getsize", return_value=123
        ), patch("hath_observer.archive.emit"):
            execute_plan(plan, "/downloads", "/archives")

        stage_zip.assert_called_once_with(
            "/downloads/Existing.zip",
            "/archives/.staging/job/Existing.zip",
        )
        self.assertEqual(
            run.call_args_list[-1],
            call(
                [
                    "zip",
                    "-0",
                    "-m",
                    "/archives/.batch.zip.job.partial",
                    "--",
                    "Gallery One.zip",
                    "Existing.zip",
                ],
                cwd="/archives/.staging/job",
            ),
        )

    def test_execute_plan_keeps_sources_when_upload_fails(self):
        plan = {
            "request": {
                "selected": ["Gallery One"],
                "archive_name": "batch.zip",
                "upload": True,
                "mega_destination": "/H@H",
                "trash_sources": True,
                "trash_archive_after_upload": True,
                "cleanup_mode": "delete",
            },
            "archive_path": "/archives/batch.zip",
            "selected_count": 1,
            "selected": [
                {
                    "name": "Gallery One",
                    "kind": "gallery",
                    "archive_member": "Gallery One.zip",
                }
            ],
        }
        with patch("hath_observer.archive.command_path", side_effect=lambda name: name), patch(
            "hath_observer.archive.run_checked",
            side_effect=["", "", "", ArchiveError("upload failed")],
        ), patch("hath_observer.archive.os.path.isdir", return_value=True), patch(
            "hath_observer.archive.os.path.exists", return_value=False
        ), patch("hath_observer.archive.os.makedirs"), patch(
            "hath_observer.archive.os.rmdir"
        ), patch("hath_observer.archive.os.rename"), patch(
            "hath_observer.archive.permanently_delete"
        ) as delete, patch("hath_observer.archive.emit"):
            with self.assertRaisesRegex(ArchiveError, "upload failed"):
                execute_plan(plan, "/downloads", "/archives")

        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
