"""Synthetic diagnostics fixtures; raw credentials must never reach the log."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from scripts.launcher_logs import (
    LOG_LIMIT, MAX_LOG_BYTES, MAX_OUTPUT_LINE, RecentLog, classify_output,
    log_path, read_recent_logs,
)


class LauncherLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="launcher-log-", dir=os.environ.get("EXH_LAUNCHER_TEST_ROOT")))
        self.path = self.directory / "profile.json"
        self.log = RecentLog(self.path, uuid.uuid4().hex)

    def test_bounded_snapshot_roundtrip_and_new_run_replaces_old_entries(self):
        for _ in range(LOG_LIMIT + 40):
            self.log.add("service.fetch")
        self.log.add("launcher.running")
        self.log.flush(force=True)
        lines = read_recent_logs(self.path)
        self.assertEqual(len(lines), LOG_LIMIT)
        self.assertIn("Service is ready", lines[-1])
        self.assertLess(log_path(self.path).stat().st_size, MAX_LOG_BYTES)
        replacement = RecentLog(self.path, uuid.uuid4().hex)
        replacement.add("launcher.starting")
        replacement.flush(force=True)
        self.assertEqual(len(read_recent_logs(self.path)), 1)

    def test_raw_tokens_cookies_urls_paths_and_exception_details_are_never_saved(self):
        marker = "fixture-private-value-do-not-persist"
        output = "\n".join([
            f"Authorization: Bearer {marker}", f"Cookie: session={marker}",
            f"H@H receiver token loaded from C:/{marker}; remote ...",
            f"Starting H@H SSH tunnel through {marker}...",
            f"[feedback] received action=record url=https://example/{marker} vote=1 score=None",
            f"ModuleNotFoundError: {marker}", f"H@H launcher error: {marker}",
            f'127.0.0.1 - "POST /api/feedback?token={marker} HTTP/1.1" 200 -',
            f'127.0.0.1 - "GET /private/{marker} HTTP/1.1" 404 -',
            f"Traceback (most recent call last):\n  secret = '{marker}'",
        ]) + "\n"
        self.log.capture(io.BytesIO(output.encode("utf-8")))
        self.log.flush(force=True)
        saved = log_path(self.path).read_text(encoding="utf-8")
        self.assertNotIn(marker, saved)
        lines = "\n".join(read_recent_logs(self.path))
        self.assertNotIn(marker, lines)
        self.assertIn("POST /api/feedback -> 200", lines)
        self.assertIn("GET other -> 404", lines)
        self.assertIn("required Python module", lines)
        self.assertEqual(sum(entry["code"] == "process.omitted" for entry in self.log.entries), 1)

    def test_health_polling_is_silent_and_known_runtime_events_are_classified(self):
        self.assertIsNone(classify_output('127.0.0.1 - "GET /api/health HTTP/1.1" 200 -'))
        self.assertIsNone(classify_output('127.0.0.1 - "GET /api/fetch-progress HTTP/1.1" 200 -'))
        for line, code in [
            ("Serving ExHentai recommender at http://127.0.0.1:1", "service.ready"),
            ("H@H SSH tunnel exited with code 1; retrying in 5s", "hath.retry"),
            ("background refresh failed: private", "service.refresh-error"),
            ("host: Permission denied (publickey).", "ssh.auth-error"),
            ("ssh: connect to host private: Connection refused", "ssh.connection-error"),
        ]:
            self.assertEqual(classify_output(line)[0], code)

    def test_overlong_lines_are_discarded_in_full_before_next_event(self):
        output = b"x" * (MAX_OUTPUT_LINE * 4) + b"[fetch] not a new line\n[feedback] action=record\n"
        self.log.capture(io.BytesIO(output))
        self.assertEqual([entry["code"] for entry in self.log.entries], ["process.omitted", "service.feedback-done"])

    def test_untrusted_snapshot_and_invalid_fields_fail_closed(self):
        self.assertEqual(read_recent_logs(self.path), [])
        for payload in [[], {"schema": 1, "run_id": "private"},
                        {"schema": 1, "run_id": uuid.uuid4().hex, "entries": [{"at": "private"}]},
                        {"schema": 1, "run_id": uuid.uuid4().hex, "entries": [
                            {"at": "2026-10-28T00:00:00+00:00", "code": "http.request",
                             "fields": {"method": "GET", "route": "private", "status": 200}}]}]:
            log_path(self.path).write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(read_recent_logs(self.path), ["Recent logs unavailable."])
        with self.assertRaises(ValueError):
            self.log.add("launcher.failed", reason="untrusted failure text")
        log_path(self.path).write_bytes(b"x" * (MAX_LOG_BYTES + 1))
        self.assertEqual(read_recent_logs(self.path), ["Recent logs unavailable."])

    def test_logging_storage_failure_does_not_interrupt_service(self):
        from unittest.mock import patch
        self.log.add("launcher.running")
        with patch("scripts.launcher_logs.os.replace", side_effect=PermissionError):
            self.log.flush(force=True)
        self.log.flush(force=True)
        self.assertIn("Service is ready", read_recent_logs(self.path)[0])


if __name__ == "__main__":
    unittest.main()
