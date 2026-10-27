"""Windows-native launcher tests using retained, isolated synthetic fixtures."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from scripts import launcher_core as core
from scripts.launcher_logs import log_path, read_recent_logs
from scripts.launcher_windows import (
    LauncherWindowsError, StartupRegistration, StopEvent, WindowsJob, kernel, signal_stop,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/launcher_fixture.py"


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(pid):
    api = kernel()
    handle = api.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 0x102
    finally:
        api.CloseHandle(handle)


def eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("Synthetic launcher condition timed out")
        time.sleep(0.05)


@contextmanager
def endpoint(mode):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Other/1" if mode == "other" else "ExhRec/fixture"

        def log_message(self, *args):
            pass

        def do_GET(self):
            if mode == "slow":
                time.sleep(0.4)
            code = 200
            body = b'{"service":"exh-rec","status":"ok"}'
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/api/health")
                self.end_headers()
                return
            if mode == "legacy":
                code = 404 if self.path == "/api/health" else 200
                body = b"<title>ExHentai Recommender</title>"
            if mode == "bad-json":
                body = b"not-json"
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        parent = os.environ.get("EXH_LAUNCHER_TEST_ROOT")
        self.directory = Path(tempfile.mkdtemp(prefix="profile & spaces-", dir=parent))
        self.path = self.directory / "profile.json"
        self.records = self.directory / "processes.json"
        self.config = replace(core.LaunchConfig.defaults(), host="127.0.0.1", port=unused_port(),
                              data_dir=str(self.directory / "service-data"), startup_timeout=10)
        self.controller = core.LauncherController(self.path)
        self.processes = []

    def tearDown(self):
        if core.mutex_active(core.mutex_name(self.path)):
            try:
                self.controller.stop()
            except (core.LauncherError, LauncherWindowsError):
                pass
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if self.records.exists():
            pids = json.loads(self.records.read_text(encoding="utf-8"))
            eventually(lambda: not any(alive(pid) for pid in pids.values()))

    def fixture(self, scenario="normal"):
        core.save_config(self.config, self.path)
        process = subprocess.Popen([sys.executable, str(FIXTURE), "supervise", "--config", str(self.path),
                                    "--records", str(self.records), "--scenario", scenario], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.processes.append(process)
        return process

    def running_fixture(self, scenario="normal"):
        process = self.fixture(scenario)
        eventually(lambda: self.controller.status(self.config)["state"] == "running")
        return process

    def test_config_roundtrip_and_timestamped_backup(self):
        core.save_config(self.config, self.path)
        self.assertEqual(core.load_config(self.path), self.config)
        changed = replace(self.config, port=unused_port())
        core.save_config(changed, self.path)
        backups = list(self.directory.glob("profile.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(core.load_config(backups[0]), self.config)
        self.assertEqual(core.load_config(self.path), changed)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_invalid_fields_types_and_proxy_credentials_fail_closed(self):
        invalid = [{"unknown": 1}, {"port": True}, {"port": 0}, {"schema": 2}, {"hath_enabled": 1},
                   {"host": "remote.example"}, {"venv_path": ""}, {"startup_timeout": 9},
                   {"proxy": "http://user:dummy@localhost:8080"}, {"proxy": "http://localhost"},
                   {"visual_encoder": "unknown"}, {"dino_device": "unknown"}, {"data_dir": "x\ny"},
                   {"hath_enabled": True}, {"hath_enabled": True, "hath_token_file": "dummy",
                                             "hath_ssh_config": "dummy", "hath_ssh_host": "x & y"}]
        for payload in invalid:
            with self.subTest(fields=list(payload)), self.assertRaises(core.LauncherError):
                core.LaunchConfig.from_dict(payload)

    def test_missing_and_corrupt_config_and_file_validation(self):
        self.assertEqual(core.load_config(self.path), core.LaunchConfig.defaults())
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(core.LauncherError, "config-read-failed"):
            core.load_config(self.path)
        with self.assertRaisesRegex(core.LauncherError, "venv-python-missing"):
            replace(self.config, venv_path=str(self.directory / "absent")).validate(check_files=True)

    def test_command_and_environment_are_literal_and_do_not_contain_token(self):
        config = replace(self.config, hath_enabled=True, hath_token_file=str(self.directory / "dummy & token"),
                         hath_ssh_config=str(self.directory / "dummy config"), proxy="http://127.0.0.1:8080")
        command = config.service_command()
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("--token-file") + 1], config.hath_token_file)
        self.assertEqual(command[command.index("--ssh-host") + 1], "exh-rec-hath")
        self.assertEqual(command[command.index("--local-port") + 1], str(config.port))
        with patch.dict(os.environ, {"EXH_REC_HATH_TOKEN": "fixture-only-not-a-real-secret", "EXH_REC_HATH_SSH_HOST": "old"}):
            env = config.environment()
        self.assertNotIn("EXH_REC_HATH_TOKEN", env)
        self.assertNotIn("EXH_REC_HATH_SSH_HOST", env)
        self.assertEqual(env["EXH_REC_DATA_DIR"], config.data_dir)
        self.assertEqual(env["EXH_REC_PROXY"], config.proxy)
        self.assertEqual(config.home_url(), f"http://127.0.0.1:{config.port}/")
        self.assertEqual(replace(config, host="0.0.0.0").home_url(), config.home_url())

    def test_probe_identity_legacy_wrong_server_redirect_and_timeout(self):
        for mode, expected in [("ready", "ready"), ("legacy", "legacy"), ("other", "occupied"),
                               ("bad-json", "occupied"), ("redirect", "occupied"), ("slow", "occupied")]:
            with self.subTest(mode=mode), endpoint(mode) as port:
                self.assertEqual(core.probe_service(replace(self.config, port=port), timeout=0.1), expected)
                if mode == "ready":
                    self.assertEqual(core.probe_service(replace(self.config, host="0.0.0.0", port=port)), "ready")
        self.assertEqual(core.probe_service(self.config), "stopped")

    def test_health_handler_does_not_open_database_or_compute_recommendations(self):
        from exh_rec.app import Handler
        handler = Handler.__new__(Handler)
        handler.path = "/api/health"
        handler.send_json = Mock()
        handler.handle_error = lambda error: self.fail(type(error).__name__)
        with patch("exh_rec.app.db.connect", side_effect=AssertionError("Unexpected DB read")), patch(
            "exh_rec.app.get_status", side_effect=AssertionError("Unexpected status computation")
        ):
            handler.do_GET()
        handler.send_json.assert_called_once_with({"service": "exh-rec", "status": "ok"})

    def test_starting_service_probe_never_binds_its_port(self):
        with patch("scripts.launcher_core.socket.socket") as sock, patch(
            "scripts.launcher_core.socket.create_connection", side_effect=ConnectionRefusedError
        ):
            self.assertEqual(core.probe_service(self.config, check_available=False), "stopped")
        sock.assert_not_called()

    def test_external_service_is_detected_but_not_started_or_stopped(self):
        with endpoint("ready") as port:
            config = replace(self.config, port=port)
            self.assertEqual(self.controller.status(config)["state"], "external")
            with patch("scripts.launcher_core.subprocess.Popen") as launch:
                self.assertEqual(self.controller.start(config), "already-running")
            launch.assert_not_called()
            with self.assertRaisesRegex(core.LauncherError, "no-managed-service"):
                self.controller.stop()
            self.assertEqual(core.probe_service(config), "ready")

    def test_occupied_port_prevents_start(self):
        with endpoint("other") as port, self.assertRaisesRegex(core.LauncherError, "port-occupied"):
            self.controller.start(replace(self.config, port=port))

    def test_open_home_uses_default_browser_only_when_ready(self):
        with self.assertRaisesRegex(core.LauncherError, "service-not-ready"):
            self.controller.open_home(self.config)
        with endpoint("ready") as port, patch("scripts.launcher_core.webbrowser.open", return_value=True) as browser:
            config = replace(self.config, port=port)
            self.assertTrue(self.controller.open_home(config))
            browser.assert_called_once_with(config.home_url(), new=2)

    def test_mutex_singleton_and_stop_remove_descendants(self):
        process = self.running_fixture()
        original = core.read_runtime(self.path)
        pids = json.loads(self.records.read_text(encoding="utf-8"))
        self.assertTrue(all(alive(pid) for pid in pids.values()))
        duplicate = self.fixture()
        self.assertEqual(duplicate.wait(timeout=10), 0)
        self.assertEqual(core.read_runtime(self.path)["run_id"], original["run_id"])
        self.assertEqual(self.controller.start(self.config), "already-running")
        self.controller.stop()
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(core.read_runtime(self.path)["state"], "stopped")
        self.assertEqual(self.controller.status(self.config)["state"], "stopped")
        eventually(lambda: not any(alive(pid) for pid in pids.values()))

    def test_worker_crash_kills_its_owned_descendants(self):
        process = self.running_fixture()
        pids = json.loads(self.records.read_text(encoding="utf-8"))
        process.terminate()
        process.wait(timeout=10)
        eventually(lambda: not any(alive(pid) for pid in pids.values()))
        self.assertFalse(self.controller.status(self.config)["managed"])

    def test_background_logs_survive_ui_close_and_capture_output_without_private_text(self):
        self.running_fixture()
        eventually(lambda: any("HTTP server started" in line for line in read_recent_logs(self.path)))
        self.assertNotIn("fixture-private-output-never-save", log_path(self.path).read_text(encoding="utf-8"))
        import tkinter as tk
        from scripts.launcher import LauncherApp
        root = tk.Tk()
        root.withdraw()
        app = LauncherApp(root, self.path)
        app.close()
        self.controller.stop()
        self.assertIn("Managed service stopped", read_recent_logs(self.path)[-1])

    def test_large_process_output_is_drained_without_blocking_service_startup(self):
        self.running_fixture("log-flood")
        eventually(lambda: any("Feedback processed" in line for line in read_recent_logs(self.path)))
        self.assertLess(log_path(self.path).stat().st_size, 16 * 1024)

    def test_closing_actual_ui_preserves_running_background_worker(self):
        import tkinter as tk
        from scripts.launcher import LauncherApp
        process = self.running_fixture()
        root = tk.Tk()
        root.withdraw()
        app = LauncherApp(root, self.path)
        app.close()
        self.assertIsNone(process.poll())
        self.assertEqual(self.controller.status(self.config)["state"], "running")

    def test_running_but_unresponsive_is_not_mislabeled_as_starting(self):
        self.running_fixture()
        with patch("scripts.launcher_core.probe_service", return_value="occupied"):
            status = self.controller.status(self.config)
        self.assertEqual(status["state"], "unresponsive")
        self.assertTrue(status["managed"])

    def test_unresponsive_managed_service_cannot_be_started_again(self):
        self.running_fixture()
        original = self.path.read_bytes()
        with patch("scripts.launcher_core.probe_service", return_value="occupied"), patch(
            "scripts.launcher_core.subprocess.Popen"
        ) as launch:
            self.assertEqual(self.controller.start(replace(self.config, port=unused_port())), "already-running")
        launch.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_assignment_failure_does_not_release_application_gate(self):
        process = self.fixture("assign-failure")
        self.assertEqual(process.wait(timeout=15), 1)
        self.assertFalse(self.records.exists())
        self.assertEqual(core.read_runtime(self.path)["error"], "containment-unavailable")
        self.assertEqual(core.probe_service(self.config), "stopped")

    def test_cleanup_failure_is_not_reported_as_stopped_and_job_still_closes(self):
        process = self.running_fixture("cleanup-failure")
        with self.assertRaisesRegex(core.LauncherError, "cleanup-failed"):
            self.controller.stop()
        self.assertEqual(process.wait(timeout=10), 1)
        self.assertEqual(self.controller.status(self.config)["state"], "failed")

    def test_startup_timeout_and_child_exit_publish_sanitized_failure(self):
        process = self.fixture("timeout")
        self.assertEqual(process.wait(timeout=20), 1)
        self.assertEqual(core.read_runtime(self.path)["error"], "startup-timeout")
        process = self.fixture("exit")
        self.assertEqual(process.wait(timeout=15), 1)
        self.assertEqual(core.read_runtime(self.path)["error"], "service-exited")
        logs = "\n".join(read_recent_logs(self.path))
        self.assertIn("required Python module", logs)
        self.assertIn("service-exited", logs)
        self.assertNotIn("fixture-only-private-module", logs)

    def test_stale_runtime_pid_does_not_grant_ownership(self):
        payload = {"profile_id": core.profile_id(self.path), "run_id": uuid.uuid4().hex,
                   "supervisor_pid": os.getpid(), "state": "running", "error": "",
                   "host": self.config.host, "port": self.config.port}
        core.atomic_json(core.runtime_path(self.path), payload)
        self.assertEqual(self.controller.status(self.config)["state"], "stopped")
        with self.assertRaisesRegex(core.LauncherError, "no-managed-service"):
            self.controller.stop()
        for field, value in [("run_id", 42), ("port", "bad"), ("host", {}), ("state", "unknown"), ("supervisor_pid", True)]:
            core.atomic_json(core.runtime_path(self.path), {**payload, field: value})
            self.assertEqual(core.read_runtime(self.path), {})

    def test_stop_event_is_per_run_and_read_back(self):
        name = core.event_name(self.path, uuid.uuid4().hex)
        event = StopEvent(name)
        try:
            self.assertFalse(event.wait(0))
            signal_stop(name)
            self.assertTrue(event.wait(0))
        finally:
            event.close()
        with self.assertRaisesRegex(LauncherWindowsError, "stop-event-missing"):
            signal_stop(name)

    def test_registry_enable_disable_and_conflict_with_independent_readback(self):
        import winreg
        key = "Software\\ExhRecLauncherTests\\" + uuid.uuid4().hex
        registration = core.startup_registration(self.path)
        registration = StartupRegistration(registration.name, registration.command, key=key)
        self.assertFalse(registration.enabled())
        try:
            registration.set_enabled(True)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                command, kind = winreg.QueryValueEx(handle, registration.name)
            self.assertEqual(command, registration.command)
            self.assertEqual(kind, winreg.REG_SZ)
            self.assertIn("--background", command)
            self.assertNotIn("token", command)
            registration.set_enabled(False)
            self.assertFalse(registration.enabled())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.SetValueEx(handle, registration.name, 0, winreg.REG_SZ, "fixture-other-command")
            with self.assertRaisesRegex(LauncherWindowsError, "startup-entry-conflict"):
                registration.set_enabled(True)
        finally:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, registration.name)

    def test_startup_registered_command_dispatches_hidden_service_not_ui(self):
        self.prepare_database()
        core.save_config(self.config, self.path)
        registration = core.startup_registration(self.path)
        registration = StartupRegistration(registration.name, registration.command,
                                          key="Software\\ExhRecLauncherTests\\" + uuid.uuid4().hex)
        try:
            registration.set_enabled(True)
            # Windows CreateProcess receives the same literal command as Run.
            process = subprocess.Popen(registration.read(), cwd=self.directory, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            self.processes.append(process)
            eventually(lambda: self.controller.status(self.config)["state"] == "running", timeout=60)
            self.controller.stop()
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            registration.set_enabled(False)
        self.assertIsNone(registration.read())

    def prepare_database(self):
        from exh_rec import db
        directory = Path(self.config.data_dir)
        with patch.object(db, "DATA_DIR", directory), patch.object(db, "DB_PATH", directory / "recommender.sqlite3"):
            db.init_db()
            with db.connect() as conn:
                db.set_setting(conn, "auto_refresh", "0")
                db.set_setting(conn, "model_retrain_mode", "manual")

    def test_actual_background_entry_start_stop_and_duplicate_detection(self):
        self.prepare_database()
        self.assertEqual(self.controller.start(self.config), "start-requested")
        eventually(lambda: self.controller.status(self.config)["state"] == "running", timeout=60)
        run_id = core.read_runtime(self.path)["run_id"]
        self.assertEqual(self.controller.start(self.config), "already-running")
        self.assertEqual(core.read_runtime(self.path)["run_id"], run_id)
        self.controller.stop()
        self.assertEqual(core.probe_service(self.config), "stopped")

    def test_actual_hath_supervisor_with_dummy_files_and_non_network_ssh(self):
        self.prepare_database()
        token = self.directory / "dummy-token.secret"
        ssh = self.directory / "dummy-ssh-config"
        token.write_text("synthetic-fixture-token-000000000000000000", encoding="utf-8")
        ssh.write_text("# fixture: no SSH hosts\n", encoding="utf-8")
        # Python rejects SSH's -F flag and exits without opening any connection.
        config = replace(self.config, hath_enabled=True, hath_token_file=str(token),
                         hath_ssh_config=str(ssh), ssh_executable=sys.executable)
        self.assertEqual(self.controller.start(config), "start-requested")
        eventually(lambda: self.controller.status(config)["state"] == "running", timeout=60)
        self.controller.stop()
        self.assertEqual(core.probe_service(config), "stopped")
        self.assertNotIn("synthetic-fixture-token", self.path.read_text(encoding="utf-8"))

    def test_windowless_double_click_bootstrap_environment_check(self):
        result = subprocess.run([str(Path(os.environ["WINDIR"]) / "System32/cscript.exe"), "//nologo",
                                 str(ROOT / "launch.vbs"), "--check"], capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), b"ready")


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class LauncherUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from scripts.launcher import LauncherApp
        parent = os.environ.get("EXH_LAUNCHER_TEST_ROOT")
        self.path = Path(tempfile.mkdtemp(prefix="gui-", dir=parent)) / "profile.json"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LauncherApp(self.root, self.path)

    def tearDown(self):
        self.app.close()

    def pump(self, seconds=0.4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

    def test_polling_timer_does_not_accumulate(self):
        with patch.object(self.app.controller, "status", return_value={"state": "stopped", "managed": False}):
            self.pump(2)
            self.assertLessEqual(len(self.root.tk.call("after", "info")), 3)

    def test_stale_status_result_cannot_restore_old_profile(self):
        self.app.refresh = Mock()
        self.app.config_generation = 2
        self.app.results.put(("status", {"state": "external", "managed": False}, None, None, 1))
        self.app.drain()
        self.assertEqual(self.app.latest["state"], "checking")
        self.app.refresh.assert_called_once()

    def test_busy_and_managed_states_disable_parameter_edits(self):
        self.app.variables["hath_enabled"].set(True)
        self.app.busy = True
        self.app.render_status({"state": "stopped", "managed": False})
        self.assertTrue(all(str(widget.cget("state")) == "disabled" for widget, _ in self.app.hath_widgets))
        self.assertEqual(str(self.app.save_button.cget("state")), "disabled")
        self.app.busy = False
        self.app.latest = {"state": "running", "managed": True}
        self.app.render_status(self.app.latest)
        self.assertTrue(all(str(widget.cget("state")) == "disabled" for widget, _ in self.app.form_widgets))

    def test_save_bad_input_and_start_failure_allow_retry(self):
        self.app.variables["port"].set("not a number")
        self.assertIsNone(self.app.save())
        self.assertFalse(self.path.exists())
        self.app.variables["port"].set(str(unused_port()))
        self.assertIsNotNone(self.app.save())
        with patch.object(self.app.controller, "start", side_effect=core.LauncherError("port-occupied")):
            self.app.start()
            self.pump()
        self.assertFalse(self.app.busy)
        self.assertIn("occupied", self.app.notice.get())

    def test_closing_ui_does_not_request_service_stop(self):
        with patch.object(self.app.controller, "stop") as stop:
            self.app.close()
        stop.assert_not_called()
        self.assertEqual(self.app.timers, set())
        self.tearDown = lambda: None

    def test_startup_failure_restores_authoritative_checkbox(self):
        registration = Mock()
        registration.enabled.return_value = False
        registration.set_enabled.side_effect = LauncherWindowsError("startup-save-failed")
        self.app.auto_start.set(True)
        with patch("scripts.launcher.startup_registration", return_value=registration):
            self.app.apply_startup()
        self.assertFalse(self.app.auto_start.get())

    def test_disabling_startup_does_not_require_valid_form_fields(self):
        registration = Mock()
        registration.enabled.return_value = False
        self.app.variables["port"].set("not a number")
        self.app.auto_start.set(False)
        with patch("scripts.launcher.startup_registration", return_value=registration):
            self.app.apply_startup()
        registration.set_enabled.assert_called_once_with(False)
        self.assertFalse(self.app.auto_start.get())
        self.assertFalse(self.path.exists())

    def test_recent_log_widget_is_read_only_and_respects_follow_toggle(self):
        lines = [f"10-28 12:00:00  INFO   [service] Entry {index}" for index in range(80)]
        self.app.render_logs(lines)
        self.assertEqual(str(self.app.log_text.cget("state")), "disabled")
        self.assertIn("Entry 79", self.app.log_text.get("1.0", "end"))
        self.app.follow_logs.set(False)
        self.app.log_text.yview_moveto(0)
        self.app.render_logs(lines + ["10-28 12:00:01  ERROR  [launcher] Failed."])
        self.assertEqual(self.app.log_text.yview()[0], 0)
        self.assertTrue(self.app.log_text.tag_ranges("error"))
        self.app.render_logs([], external=True)
        self.assertIn("unavailable", self.app.log_status.cget("text"))

    def test_start_selects_logs_and_status_refresh_reads_persisted_history(self):
        from scripts.launcher_logs import RecentLog
        log = RecentLog(self.path, uuid.uuid4().hex)
        log.add("launcher.running")
        log.flush(force=True)
        with patch.object(self.app.controller, "status", return_value={"state": "stopped", "managed": False}):
            self.app.refresh()
            self.pump()
        self.assertIn("Service is ready", self.app.log_text.get("1.0", "end"))
        with patch.object(self.app.controller, "start", return_value="already-running"):
            self.app.start()
            self.assertEqual(self.app.notebook.select(), str(self.app.logs_tab))
            self.pump()


if __name__ == "__main__":
    unittest.main()
