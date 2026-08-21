import io
import json
import time
import unittest
from unittest.mock import patch

from exh_rec.hath_pack import (
    HathPackConflict,
    HathPackRunner,
    build_pack_ssh_command,
    pack_configuration,
)


class HathPackTest(unittest.TestCase):
    def test_configuration_uses_fixed_default_remote_helper(self):
        configuration = pack_configuration(
            {
                "EXH_REC_HATH_SSH_EXECUTABLE": "ssh.exe",
                "EXH_REC_HATH_SSH_CONFIG": "C:/secrets/config",
                "EXH_REC_HATH_SSH_HOST": "hath-host",
            }
        )

        self.assertEqual(configuration["remote_helper"], "/srv/hath-observer/hath_observer/archive.py")

    def test_ssh_command_uses_fixed_helper_and_operation(self):
        command = build_pack_ssh_command(
            {
                "ssh_executable": "ssh.exe",
                "ssh_config": "C:/secrets/config",
                "ssh_host": "hath-host",
                "remote_helper": "/srv/hath-observer/hath_observer/archive.py",
            },
            "plan",
        )

        self.assertEqual(command[:3], ["ssh.exe", "-F", "C:/secrets/config"])
        self.assertEqual(command[-2], "hath-host")
        self.assertEqual(command[-1], "/usr/bin/python3 /srv/hath-observer/hath_observer/archive.py plan")

    def test_ssh_command_rejects_unknown_operation(self):
        with self.assertRaisesRegex(ValueError, "Unsupported archive operation"):
            build_pack_ssh_command(
                {
                    "ssh_executable": "ssh.exe",
                    "ssh_config": "config",
                    "ssh_host": "host",
                    "remote_helper": "/helper.py",
                },
                "arbitrary-command",
            )

    def test_ssh_command_supports_trash_operations(self):
        command = build_pack_ssh_command(
            {
                "ssh_executable": "ssh.exe",
                "ssh_config": "config",
                "ssh_host": "host",
                "remote_helper": "/helper.py",
            },
            "trash-run",
        )

        self.assertEqual(command[-1], "/usr/bin/python3 /helper.py trash-run")

    def test_unconfigured_runner_reports_missing_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            status = HathPackRunner().status()

        self.assertFalse(status["configured"])
        self.assertEqual(status["state"], "idle")
        self.assertIn("ssh_executable", status["configuration_error"])

    def test_runner_captures_output_and_success(self):
        request = {
            "selected": ["Gallery One"],
            "archive_name": "20260819.zip",
            "upload": False,
            "mega_destination": "/H@H Archives",
            "trash_sources": False,
            "trash_archive_after_upload": False,
        }
        plan = {
            "schema": "hath-archive-v1",
            "request": request,
            "selected_count": 1,
            "input_bytes": 100,
            "input_files": 2,
            "actions": [],
        }

        class FakeProcess:
            stdin = io.StringIO()
            stdout = iter(
                [
                    json.dumps({"event": "progress", "stage": "packing", "message": "Creating archive"}) + "\n",
                    json.dumps({"event": "result", "state": "succeeded", "message": "Archive job completed", "result": {"uploaded": False}}) + "\n",
                ]
            )

            @staticmethod
            def wait():
                return 0

        environment = {
            "EXH_REC_HATH_SSH_EXECUTABLE": "ssh.exe",
            "EXH_REC_HATH_SSH_CONFIG": "C:/secrets/config",
            "EXH_REC_HATH_SSH_HOST": "hath-host",
        }
        runner = HathPackRunner()
        with patch.dict("os.environ", environment, clear=True), patch(
            "exh_rec.hath_pack.run_remote_json", return_value=plan
        ), patch("exh_rec.hath_pack.subprocess.Popen", return_value=FakeProcess()):
            preview = runner.preview(request)
            started = runner.start(preview["preview_id"])
            deadline = time.monotonic() + 1
            while runner.status()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
            status = runner.status()

        self.assertEqual(started["run_id"], 1)
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(status["stage"], "finished")
        self.assertEqual(status["result"], {"uploaded": False})
        self.assertEqual(status["logs"], ["Creating archive", "Archive job completed"])

    def test_runner_requires_a_current_preview(self):
        runner = HathPackRunner()

        with self.assertRaisesRegex(HathPackConflict, "Preview is missing or stale"):
            runner.start("missing")

    def test_runner_binds_trash_preview_to_trash_run(self):
        request = {"selected": ["job-one"]}
        plan = {
            "schema": "hath-archive-v1",
            "kind": "trash-delete",
            "request": request,
            "selected_count": 1,
            "input_bytes": 100,
            "input_files": 2,
            "actions": [],
        }

        class FakeProcess:
            stdin = io.StringIO()
            stdout = iter(
                [
                    json.dumps(
                        {
                            "event": "result",
                            "state": "succeeded",
                            "message": "Trash deleted",
                            "result": {"freed_bytes": 100},
                        }
                    )
                    + "\n"
                ]
            )

            @staticmethod
            def wait():
                return 0

        environment = {
            "EXH_REC_HATH_SSH_EXECUTABLE": "ssh.exe",
            "EXH_REC_HATH_SSH_CONFIG": "config",
            "EXH_REC_HATH_SSH_HOST": "host",
        }
        runner = HathPackRunner()
        with patch.dict("os.environ", environment, clear=True), patch(
            "exh_rec.hath_pack.run_remote_json", return_value=plan
        ) as remote, patch(
            "exh_rec.hath_pack.subprocess.Popen", return_value=FakeProcess()
        ) as popen:
            preview = runner.preview_trash(request)
            runner.start(preview["preview_id"])
            deadline = time.monotonic() + 1
            while runner.status()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)

        remote.assert_called_once_with("trash-plan", request)
        self.assertTrue(popen.call_args.args[0][-1].endswith("archive.py trash-run"))
        self.assertEqual(runner.status()["result"], {"freed_bytes": 100})


if __name__ == "__main__":
    unittest.main()
