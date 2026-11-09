"""Startup identity and checkup tests use synthetic sources, not production data."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from exh_rec import runtime_identity as identity
from scripts import launcher_checkup as checkup
from scripts import launcher_runtime as runtime
from scripts.launcher_core import LaunchConfig, LauncherError


REVISION = "a" * 40
DIGEST = "b" * 64
STARTED = "2026-11-09T01:00:00+00:00"
PAYLOAD = {"service": "exh-rec", "schema": 1, "revision": REVISION, "source_digest": DIGEST,
           "python": "3.12.10", "started_at": STARTED}


@contextmanager
def runtime_endpoint(mode="ready", payload=PAYLOAD):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Other/1" if mode == "other" else "ExhRec/fixture"

        def log_message(self, *args):
            pass

        def do_GET(self):
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/api/runtime")
                self.end_headers()
                return
            body = b"x" * 4097 if mode == "oversized" else json.dumps(payload).encode()
            self.send_response(404 if mode == "unsupported" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="startup-source-", dir=os.environ.get("EXH_LAUNCHER_TEST_ROOT")))
        for name in ("exh_rec", "scripts", ".git/refs/heads"):
            (self.root / name).mkdir(parents=True)
        (self.root / "exh_rec/app.py").write_text("# synthetic application\n", encoding="ascii")
        for name in ("service_bootstrap.py", "run_with_hath.py"):
            (self.root / "scripts" / name).write_text("# synthetic\n", encoding="ascii")
        (self.root / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        (self.root / ".git/refs/heads/main").write_text(REVISION + "\n", encoding="ascii")

    def test_revision_and_source_snapshot_detect_uncommitted_backend_change(self):
        started = identity.capture_runtime(self.root)
        self.assertEqual(started["revision"], REVISION)
        current = identity.capture_source(self.root)
        self.assertEqual(runtime.compare_source(current, {"availability": "available", **started}), "matching")
        (self.root / "exh_rec/app.py").write_text("# modified without a commit\n", encoding="ascii")
        current = identity.capture_source(self.root)
        self.assertEqual(current["revision"], started["revision"])
        self.assertEqual(runtime.compare_source(current, {"availability": "available", **started}), "changed")

    def test_source_ignores_private_data_cache_and_launcher_ui(self):
        before = identity.capture_source(self.root)
        (self.root / "data").mkdir()
        (self.root / "data/fake.secret").write_text("FAKE_SECRET", encoding="ascii")
        (self.root / "scripts/launcher.py").write_text("# UI only\n", encoding="ascii")
        (self.root / "exh_rec/__pycache__").mkdir()
        (self.root / "exh_rec/__pycache__/stale.py").write_text("# not source\n", encoding="ascii")
        self.assertEqual(before, identity.capture_source(self.root))
        self.assertNotIn("FAKE_SECRET", json.dumps(before))

    def test_detached_packed_and_worktree_heads(self):
        head = self.root / ".git/HEAD"
        head.write_text(REVISION, encoding="ascii")
        self.assertEqual(identity.local_revision(self.root), REVISION)
        head.write_text("ref: refs/heads/packed", encoding="ascii")
        (self.root / ".git/packed-refs").write_text(REVISION + " refs/heads/packed\n", encoding="ascii")
        self.assertEqual(identity.local_revision(self.root), REVISION)
        worktree = self.root / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: ../.git/worktrees/synthetic", encoding="ascii")
        directory = self.root / ".git/worktrees/synthetic"
        directory.mkdir(parents=True)
        (directory / "HEAD").write_text("ref: refs/heads/packed", encoding="ascii")
        (directory / "commondir").write_text("../..", encoding="ascii")
        self.assertEqual(identity.local_revision(worktree), REVISION)

    def test_unknown_or_incomplete_identity_never_matches(self):
        for path in (self.root / "missing",):
            self.assertIsNone(identity.local_revision(path))
            self.assertIsNone(identity.source_digest(path))
        with patch.object(identity, "_linked", return_value=True):
            self.assertIsNone(identity.source_digest(self.root))
        with patch.object(identity.os, "walk", side_effect=PermissionError):
            self.assertIsNone(identity.source_digest(self.root))
        (self.root / "exh_rec/too_large.py").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        self.assertIsNone(identity.source_digest(self.root))
        self.assertEqual(runtime.compare_source({"source_digest": None}, {"availability": "available", "source_digest": DIGEST}), "unknown")


class RuntimeReaderTests(unittest.TestCase):
    def test_reader_strips_extra_fields_and_handles_older_servers(self):
        with runtime_endpoint(payload={**PAYLOAD, "private": "FAKE_SECRET"}) as home:
            result = runtime.read_runtime_identity(home)
        self.assertEqual(result["availability"], "available")
        self.assertEqual(result["revision"], REVISION)
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        for mode in ("unsupported", "other", "redirect", "oversized"):
            with self.subTest(mode=mode), runtime_endpoint(mode) as home:
                expected = "unsupported" if mode == "unsupported" else "unavailable"
                self.assertEqual(runtime.read_runtime_identity(home), {"availability": expected})

    def test_runtime_shape_and_timestamps_fail_closed(self):
        for key, value in (("schema", True), ("revision", "FAKE_SECRET"), ("source_digest", "x"),
                           ("python", "FAKE_SECRET"), ("started_at", "2026-11-09T01:00:00"),
                           ("started_at", "FAKE_SECRET"), ("revision", 42)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                runtime.validate_runtime({**PAYLOAD, key: value})
        self.assertIsNone(runtime.validate_runtime({**PAYLOAD, "revision": None})["revision"])

    def test_unsafe_urls_do_not_make_a_request(self):
        with patch.object(runtime.urllib.request, "build_opener") as open_request:
            for url in ("https://127.0.0.1:12/", "http://user:fake@127.0.0.1:12/", "http://127.0.0.1:12/?x=y",
                        "http://localhost:12/", "http://127.0.0.1:12/other"):
                self.assertEqual(runtime.read_runtime_identity(url), {"availability": "unavailable"})
        open_request.assert_not_called()

    def test_runtime_endpoint_is_in_memory_and_does_not_open_database(self):
        from exh_rec import app
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/api/runtime"
        handler.send_json = Mock()
        handler.handle_error = lambda error: self.fail(type(error).__name__)
        original = dict(app.RUNTIME_IDENTITY)
        with patch.object(app.db, "connect", side_effect=AssertionError("Unexpected DB read")), patch.object(
                app, "get_status", side_effect=AssertionError("Unexpected status computation")), patch.object(
                app, "capture_runtime", side_effect=AssertionError("Unexpected source scan")):
            handler.do_GET()
            handler.do_GET()
        self.assertEqual(handler.send_json.call_count, 2)
        self.assertEqual(handler.send_json.call_args.args[0], original)
        self.assertNotIn("data_dir", original)


class CheckupTests(unittest.TestCase):
    def run_checkup(self, *, hath=False, state="running", source=DIGEST, environment_error=False):
        config = replace(LaunchConfig(), hath_enabled=hath, hath_token_file="fake-token", hath_ssh_config="fake-ssh")
        controller = Mock(path=Path("unused-profile.json"))
        controller.status.return_value = {"state": state, "health": "ready", "managed": state != "external",
                                          "run_id": "a", "home_url": "http://127.0.0.1:12345/"}
        environment = {"python": "3.12.10", "packages": {}, "missing_required": []}
        environment_probe = {"side_effect": TimeoutError("FAKE_SECRET")} if environment_error else {"return_value": environment}
        emitted = []
        with patch.object(checkup, "capture_source", return_value={"revision": REVISION, "source_digest": source}), patch.object(
                checkup, "read_runtime_identity", return_value={"availability": "available", **runtime.validate_runtime(PAYLOAD)}), patch.object(
                LaunchConfig, "validate"), patch.object(checkup.ops, "inspect_environment", **environment_probe), patch.object(
                checkup.ops, "check_proxy", return_value={"state": "reachable"}), patch.object(
                checkup.ops, "observer_status", return_value={"clients": 1, "last_seen_at": STARTED}) as observer, patch.object(
                checkup, "read_log_snapshot", return_value={"run_id": "old", "entries": [{"code": "ssh.auth-error", "at": STARTED}]}):
            report = checkup.run_checkup(config, controller, emitted.append)
        controller.start.assert_not_called()
        controller.stop.assert_not_called()
        self.assertEqual(len(emitted), len(checkup.CHECKS))
        self.assertEqual([row["key"] for row in emitted], [item[0] for item in checkup.CHECKS])
        self.assertNotIn("FAKE_SECRET", json.dumps(report))
        return report, observer

    def test_partial_proofs_remain_unknown_and_disabled_hath_is_not_read(self):
        report, observer = self.run_checkup()
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(rows["proxy"]["outcome"], "unknown")
        self.assertEqual(rows["tunnel"]["outcome"], "skip")
        self.assertEqual(rows["source"]["outcome"], "pass")
        observer.assert_not_called()
        self.assertTrue(all("checked_at" in row for row in rows.values()))

    def test_stale_tunnel_logs_and_receipts_do_not_prove_live_connectivity(self):
        report, observer = self.run_checkup(hath=True)
        rows = {row["key"]: row for row in report["rows"]}
        observer.assert_called_once()
        self.assertEqual(rows["tunnel"]["outcome"], "unknown")
        self.assertEqual(rows["observer"]["outcome"], "unknown")
        self.assertNotIn("authentication error", rows["tunnel"]["summary"])

    def test_environment_timeout_does_not_abort_later_checks(self):
        report, _ = self.run_checkup(environment_error=True, source="c" * 64)
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(rows["environment"]["outcome"], "unknown")
        self.assertEqual(rows["source"]["outcome"], "warn")
        self.assertEqual(report["rows"][-1]["key"], "observer")
        self.assertIn("finished_at", report)

    def test_external_readiness_is_read_only_and_failures_are_actionable(self):
        report, _ = self.run_checkup(state="external")
        self.assertIn("read-only", report["rows"][0]["action"])
        report, _ = self.run_checkup(state="occupied")
        self.assertEqual(report["rows"][0]["outcome"], "fail")


if __name__ == "__main__":
    unittest.main()
