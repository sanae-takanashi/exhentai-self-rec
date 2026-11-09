"""Tray lifecycle and fixed-content notifications; no production service calls."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.launcher_core import LauncherError
from scripts.launcher_tray import DEFAULTS, Notifications, TrayBridge, load_preferences, save_preferences
from tests.test_launcher_jobs import job_payload


class TrayTest(unittest.TestCase):
    def test_preferences_are_strict_atomic_and_read_back(self):
        path = Path(tempfile.mkdtemp(prefix="tray-settings-")) / "synthetic.ui.json"
        self.assertEqual(load_preferences(path), DEFAULTS)
        save_preferences(path, close_to_tray=False, notifications=True)
        self.assertFalse(load_preferences(path)["close_to_tray"])
        save_preferences(path, close_to_tray=True, notifications=False)
        self.assertEqual(len(list(path.parent.glob("*.bak"))), 1)
        self.assertFalse(json.loads(path.read_text())["notifications"])
        with self.assertRaises(LauncherError):
            save_preferences(path, close_to_tray=1, notifications=True)

    def test_invalid_settings_fail_without_printing_payload(self):
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", return_value='{"secret":"FAKE_SECRET"}'):
            with self.assertRaises(LauncherError) as error:
                load_preferences(Path("unused"))
        self.assertNotIn("FAKE_SECRET", str(error.exception))

    def test_notification_baseline_dedupe_and_private_content_exclusion(self):
        tracker = Notifications()
        job = job_payload("queued")
        job["name"] = "FAKE_SECRET"
        job["steps"][0]["params"] = {"query": "FAKE_SECRET"}
        data = {"availability": "available", "jobs": [job]}
        self.assertEqual(tracker.observe(data), [])
        job["state"] = "succeeded"
        self.assertEqual(tracker.observe(data), ["Fetch: succeeded."])
        self.assertEqual(tracker.observe(data), [])
        self.assertEqual(tracker.observe({"availability": "unavailable"}), [])
        self.assertEqual(tracker.observe(data), [])
        job["state"] = "failed"
        self.assertNotIn("FAKE_SECRET", json.dumps(tracker.observe(data)))
        self.assertEqual(Notifications().observe(data), [])

    def test_notifications_are_aggregated_for_large_completion_batches(self):
        tracker = Notifications()
        tracker.observe({"availability": "available", "jobs": []})
        jobs = [{**job_payload("succeeded"), "id": f"{index:032x}"} for index in range(8)]
        self.assertEqual(tracker.observe({"availability": "available", "jobs": jobs}), ["8 jobs finished. Open Jobs for results."])

    def test_failed_dependency_keeps_normal_launcher_available(self):
        bridge = TrayBridge()
        with patch.dict("sys.modules", {"pystray": None}):
            self.assertFalse(bridge.start("unused"))
        self.assertFalse(bridge.available)
        bridge.close()

    @unittest.skipUnless(os.name == "nt", "Windows tray")
    def test_native_icon_callbacks_enqueue_and_thread_stops_cleanly(self):
        bridge = TrayBridge()
        try:
            self.assertTrue(bridge.start(Path(__file__).resolve().parents[1] / "assets/launcher-256.png"))
            self.assertTrue(bridge.ready.wait(5))
            self.assertTrue(bridge.available)
            self.assertTrue(bridge.icon.visible)
            bridge.update({"state": "stopped"}, jobs=False)
            entries = list(bridge.icon.menu.items)
            self.assertEqual(str(entries[0]), "Show Launcher")
            worker = threading.Thread(target=lambda: entries[0](bridge.icon))
            worker.start()
            worker.join(3)
            self.assertEqual(bridge.events.get(timeout=2), "show")
            self.assertFalse(next(item for item in entries if str(item) == "Open Home").enabled)
            self.assertTrue(next(item for item in entries if str(item) == "Start Saved Profile").enabled)
        finally:
            bridge.close()
        self.assertFalse(bridge.thread.is_alive())
        self.assertFalse(bridge.available)

    @unittest.skipUnless(os.name == "nt", "Windows tray")
    def test_immediate_exit_during_tray_start_does_not_orphan_icon(self):
        bridge = TrayBridge()
        self.assertTrue(bridge.start(Path(__file__).resolve().parents[1] / "assets/launcher-256.png"))
        bridge.close()
        self.assertFalse(bridge.thread.is_alive())
