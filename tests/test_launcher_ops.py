"""Operational launcher checks use retained synthetic data, never production files."""
from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.launcher_core import LaunchConfig, LauncherError, config_digest
from scripts import launcher_ops as ops


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="launcher-ops-", dir=os.environ.get("EXH_LAUNCHER_TEST_ROOT")))
        self.directory = self.root / "data"
        self.directory.mkdir()
        self.database = self.directory / "recommender.sqlite3"
        self.config = replace(LaunchConfig.defaults(), data_dir=str(self.directory))
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
            conn.execute("INSERT INTO settings VALUES ('fake-cookie', 'FAKE_SECRET')")
            conn.execute("CREATE TABLE hath_clients (last_seen_at TEXT)")
            conn.execute("INSERT INTO hath_clients VALUES ('2026-11-05 01:00:00')")
            conn.commit()

    def test_database_check_and_observer_are_read_only(self):
        before = self.database.read_bytes()
        self.assertTrue(ops.check_database(self.config, root=self.root)["ok"])
        result = ops.observer_status(self.config, root=self.root)
        self.assertEqual(result["clients"], 1)
        self.assertEqual(result["last_seen_at"], "2026-11-05 01:00:00")
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        self.assertEqual(before, self.database.read_bytes())
        with closing(ops.read_database(self.database)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO settings VALUES ('x', 'y')")

    def test_missing_database_is_never_created(self):
        config = replace(self.config, data_dir=str(self.root / "absent"))
        with self.assertRaisesRegex(LauncherError, "database-unavailable"):
            ops.check_database(config, root=self.root)
        self.assertFalse((self.root / "absent").exists())

    def test_backup_contains_committed_wal_and_is_independently_verified(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO settings VALUES ('wal', 'committed')")
            writer.commit()
            result = ops.backup_database(self.config, root=self.root)
            backup = Path(result["path"])
            with closing(sqlite3.connect(backup)) as reader:
                self.assertEqual(reader.execute("SELECT value FROM settings WHERE key='wal'").fetchone(), ("committed",))
            receipt = json.loads(backup.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertTrue(receipt["verified"])
            self.assertEqual(receipt["sha256"], ops.hash_file(backup))
            before = backup.read_bytes()
            again = ops.backup_database(self.config, root=self.root)
            self.assertNotEqual(result["path"], again["path"])
            self.assertEqual(before, backup.read_bytes())

    def test_timed_out_backup_does_not_publish_verified_receipt(self):
        with self.assertRaisesRegex(LauncherError, "maintenance-timeout"):
            ops.backup_database(self.config, root=self.root, seconds=-1)
        self.assertEqual(list((self.directory / "backups").glob("*.json")), [])
        self.assertTrue(self.database.exists())

    def test_storage_scan_has_bounded_partial_sizes_and_backup_timestamp(self):
        thumbs = self.directory / "thumbs"
        thumbs.mkdir()
        for index in range(4):
            (thumbs / str(index)).write_bytes(b"abc")
        usage = ops.bounded_usage(thumbs, limit=2)
        self.assertTrue(usage["partial"])
        self.assertEqual(usage["files"], 2)
        empty = self.directory / "empty"
        empty.mkdir()
        self.assertTrue(ops.bounded_usage(empty, seconds=-1)["partial"])
        ops.backup_database(self.config, root=self.root)
        result = ops.inspect_storage(self.config, root=self.root)
        self.assertGreater(result["free_bytes"], 0)
        self.assertEqual(len(result["rows"]), 6)
        self.assertIsNotNone(result["latest_backup"])
        self.assertEqual(next(row for row in result["rows"] if row["category"] == "Thumbnail cache")["bytes"], 12)

    def test_invalid_backup_receipt_is_not_claimed_verified(self):
        result = ops.backup_database(self.config, root=self.root)
        backup = Path(result["path"])
        backup.write_bytes(b"changed size")
        self.assertIsNone(ops.inspect_storage(self.config, root=self.root)["latest_backup"])

    def test_diagnostics_are_allowlisted_not_a_status_or_config_dump(self):
        config = replace(self.config, proxy="http://private.invalid:1234", hath_token_file="secret-name.txt")
        status = {"state": "running", "managed": True, "health": "ready", "home_url": "http://private.invalid/",
                  "error": "FAKE_SECRET", "logs": ["FAKE_SECRET"],
                  "fetch_activity": {"activity": {"running": True, "current_item": "FAKE_SECRET", "queries": ["FAKE_SECRET"]}}}
        before = self.database.read_bytes()
        result = ops.export_diagnostics(config, status, root=self.root)
        text = Path(result["path"]).read_text(encoding="utf-8")
        for private in ("FAKE_SECRET", "private.invalid", "secret-name", str(self.directory), "current_item", "queries"):
            self.assertNotIn(private, text)
        self.assertTrue(json.loads(text)["fetch"]["running"])
        self.assertEqual(before, self.database.read_bytes())

    def test_proxy_probe_is_tcp_only_and_blank_is_not_direct_claim(self):
        self.assertEqual(ops.check_proxy(self.config)["state"], "inherited")
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            config = replace(self.config, proxy=f"http://127.0.0.1:{server.getsockname()[1]}")
            self.assertEqual(ops.check_proxy(config)["state"], "reachable")
        self.assertEqual(ops.check_proxy(config)["state"], "unreachable")

    def test_proxy_auth_is_rejected_without_connecting(self):
        with patch.object(ops.socket, "create_connection") as connect:
            with self.assertRaises(LauncherError):
                ops.check_proxy(replace(self.config, proxy="http://fake:FAKE_SECRET@127.0.0.1:1234"))
        connect.assert_not_called()

    def test_environment_response_is_validated_and_child_output_is_never_returned_on_failure(self):
        config = replace(self.config, venv_path=str(self.root / "venv"))
        python = config.service_python(self.root)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fixture")
        with patch.object(ops.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"FAKE_SECRET")):
            with self.assertRaisesRegex(LauncherError, "environment-check-failed"):
                ops.inspect_environment(config, root=self.root)
        with patch.object(ops.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b'{"python":"FAKE_SECRET"}')):
            with self.assertRaisesRegex(LauncherError, "environment-check-failed"):
                ops.inspect_environment(config, root=self.root)

    def test_profile_identity_changes_with_launch_parameters(self):
        self.assertEqual(config_digest(self.config), config_digest(replace(self.config)))
        self.assertNotEqual(config_digest(self.config), config_digest(replace(self.config, port=12345)))

    def test_environment_allowlists_fields_and_reports_missing_required_packages(self):
        config = replace(self.config, venv_path=str(self.root / "venv"))
        python = config.service_python(self.root)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fixture")
        payload = {"schema": 1, "python": "3.12.10", "accelerator": "cpu", "raw": "FAKE_SECRET",
                   "packages": {name: "missing" if name == "numpy" else "2.0" for name in
                                ("numpy", "scipy", "scikit-learn", "joblib", "Pillow", "torch", "torchvision", "transformers", "PySocks")}}
        with patch.object(ops.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode())):
            result = ops.inspect_environment(config, root=self.root)
        self.assertEqual(result["missing_required"], ["numpy"])
        self.assertNotIn("FAKE_SECRET", json.dumps(result))

    def test_utc_database_times_have_explicit_local_offset(self):
        self.assertIn("2026-11-05", ops.local_time("2026-11-05 01:00:00"))
        self.assertRegex(ops.local_time("2026-11-05 01:00:00"), r"[+-]\d{4}$")
        self.assertEqual(ops.local_time(None), "Unknown")

    def test_directory_links_are_rejected_for_maintenance(self):
        with patch.object(ops.Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(LauncherError, "maintenance-path-unavailable"):
                ops.check_database(self.config, root=self.root)


if __name__ == "__main__":
    unittest.main()
