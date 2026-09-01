import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hath_observer.core import (
    ObserverConfig,
    StateStore,
    configured_process_uptime,
    configured_process_running,
    observe_log_lines,
    parse_download_directory_name,
    parse_log_lines,
    scan_and_enqueue,
)


class HathObserverTest(unittest.TestCase):
    @patch("hath_observer.core.subprocess.run")
    def test_systemd_unit_reports_process_liveness(self, run):
        config = ObserverConfig(
            "client-a",
            Path("/downloads"),
            Path("/state.sqlite3"),
            systemd_unit="hath.service",
        )
        run.return_value = Mock(returncode=0)
        self.assertTrue(configured_process_running(config))
        run.return_value = Mock(returncode=3)
        self.assertFalse(configured_process_running(config))
        run.assert_called_with(
            ["systemctl", "is-active", "--quiet", "hath.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

    @patch("hath_observer.core.Path.read_text", return_value="12345.67 0.00\n")
    @patch("hath_observer.core.subprocess.run")
    def test_systemd_unit_reports_process_uptime(self, run, read_text):
        config = ObserverConfig(
            "client-a",
            Path("/downloads"),
            Path("/state.sqlite3"),
            systemd_unit="hath.service",
        )
        run.return_value = Mock(returncode=0, stdout="12000000000\n")

        self.assertEqual(configured_process_uptime(config), 345)
        run.assert_called_with(
            [
                "systemctl",
                "show",
                "hath.service",
                "--property=ActiveEnterTimestampMonotonic",
                "--value",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            universal_newlines=True,
        )

    def test_parse_official_and_fallback_directory_names(self):
        self.assertEqual(
            parse_download_directory_name("A Gallery [12345-1280x]"),
            {"gid": "12345", "resolution": "1280", "title": "A Gallery"},
        )
        self.assertEqual(
            parse_download_directory_name("Original Gallery [54321]"),
            {"gid": "54321", "resolution": "org", "title": "Original Gallery"},
        )
        self.assertEqual(
            parse_download_directory_name("12345-1920x"),
            {"gid": "12345", "resolution": "1920", "title": ""},
        )
        self.assertIsNone(parse_download_directory_name("ordinary folder"))

    def test_first_scan_baselines_existing_downloads_without_importing_them(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            downloads = root / "downloads"
            gallery = downloads / "Old Gallery [123]"
            gallery.mkdir(parents=True)
            (gallery / "001.jpg").write_bytes(b"image")
            (gallery / "galleryinfo.txt").write_text("info", encoding="utf-8")
            store = StateStore(root / "state.sqlite3")
            config = ObserverConfig("client-a", downloads, root / "state.sqlite3", heartbeat_interval=60)
            try:
                result = scan_and_enqueue(store, config)
                events = store.pending()
            finally:
                store.close()

        self.assertEqual(result["downloads"], 1)
        self.assertEqual([event["type"] for event in events], ["agent.heartbeat"])

    def test_backfill_reports_completed_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            downloads = root / "downloads"
            gallery = downloads / "Imported Gallery [456-1280x]"
            gallery.mkdir(parents=True)
            (gallery / "001.webp").write_bytes(b"one")
            (gallery / "002.webp").write_bytes(b"two")
            (gallery / "galleryinfo.txt").write_text("info", encoding="utf-8")
            store = StateStore(root / "state.sqlite3")
            config = ObserverConfig("client-a", downloads, root / "state.sqlite3", backfill=True)
            try:
                scan_and_enqueue(store, config)
                events = store.pending()
            finally:
                store.close()

        completed = next(event for event in events if event["type"] == "download.completed")
        self.assertEqual(completed["gid"], "456")
        self.assertEqual(completed["resolution"], "1280")
        self.assertEqual(completed["downloaded_files"], 2)
        self.assertEqual(completed["total_files"], 2)

    def test_later_file_and_completion_changes_emit_progress_then_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            downloads = root / "downloads"
            gallery = downloads / "New Gallery [789]"
            gallery.mkdir(parents=True)
            store = StateStore(root / "state.sqlite3")
            config = ObserverConfig("client-a", downloads, root / "state.sqlite3", heartbeat_interval=3600)
            try:
                scan_and_enqueue(store, config)
                store.mark_sent(event["event_id"] for event in store.pending())
                (gallery / "001.jpg").write_bytes(b"image")
                scan_and_enqueue(store, config)
                progress = store.pending()
                store.mark_sent(event["event_id"] for event in progress)
                (gallery / "galleryinfo.txt").write_text("info", encoding="utf-8")
                scan_and_enqueue(store, config)
                completed = store.pending()
            finally:
                store.close()

        self.assertEqual([event["type"] for event in progress], ["download.progress"])
        self.assertEqual([event["type"] for event in completed], ["download.completed"])

    def test_log_parser_maps_page_and_failure_to_snapshot_gid(self):
        snapshots = {
            "123:org": {
                "gid": "123",
                "resolution": "org",
                "title": "Log Gallery",
                "directory_name": "Log Gallery [123]",
                "downloaded_files": 4,
                "downloaded_bytes": 100,
                "complete": False,
            }
        }
        events = parse_log_lines(
            "client-a",
            [
                "GalleryDownloader: Starting download of gallery: Log Gallery\n",
                "GalleryDownloader: Finished downloading gid=123 page=5: 005.jpg\n",
                "GalleryDownloader: Permanently failed downloading gallery: Log Gallery\n",
            ],
            snapshots,
        )

        self.assertEqual([event["type"] for event in events], ["download.started", "download.progress", "download.failed"])
        self.assertTrue(all(event["gid"] == "123" for event in events))
        self.assertEqual(events[1]["downloaded_files"], 5)

    def test_log_parser_ignores_late_lines_for_completed_snapshot(self):
        snapshots = {
            "123:org": {
                "gid": "123",
                "resolution": "org",
                "title": "Log Gallery",
                "directory_name": "Log Gallery [123]",
                "downloaded_files": 5,
                "downloaded_bytes": 100,
                "complete": True,
            }
        }

        events = parse_log_lines(
            "client-a",
            [
                "GalleryDownloader: Starting download of gallery: Log Gallery\n",
                "GalleryDownloader: Finished downloading gid=123 page=5: 005.jpg\n",
                "GalleryDownloader: Permanently failed downloading gallery: Log Gallery\n",
            ],
            snapshots,
        )

        self.assertEqual(events, [])

    def test_p2p_log_metrics_are_aggregated_without_request_details(self):
        lines = [
            "2026-08-18T12:40:18Z [info] {request/203.0.113.2} Code=200 Bytes=186206 GET /h/secret-path HTTP/1.1\n",
            "2026-08-18T12:40:19Z [info] {request/203.0.113.3} Code=500 Bytes=123 GET /h/other-secret HTTP/1.1\n",
            "2026-08-18T12:41:06Z [info] {request/203.0.113.4} Code=200 Bytes=2000000 GET /t/proxy-test HTTP/1.1\n",
            "2026-08-18T12:41:02Z [debug] Remote host terminated the handshake\n",
            "2026-08-18T12:41:38Z [debug] CacheHandler: Checked cache space (cacheSize=10451597545, cacheSizeWithOverhead=10577883369 cacheLimit=10737418240, cacheFree=159534871)\n",
            "2026-08-18T12:41:47Z [debug] Main thread sleeping, memory total=20696KiB free=13637KiB max=245504KiB\n",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.sqlite3")
            try:
                observe_log_lines(store, lines)
                metrics = store.p2p_metrics(1787056920)
                stored = " ".join(
                    str(value)
                    for row in store.conn.execute("SELECT * FROM p2p_state")
                    for value in row
                )
            finally:
                store.close()

        self.assertEqual(metrics["serve_requests_total"], 2)
        self.assertEqual(metrics["serve_successes_total"], 1)
        self.assertEqual(metrics["serve_bytes_total"], 186206)
        self.assertEqual(metrics["serve_requests_5m"], 2)
        self.assertEqual(metrics["proxy_tests_total"], 1)
        self.assertEqual(metrics["proxy_test_bytes_total"], 2000000)
        self.assertEqual(metrics["handshake_interrupts_total"], 1)
        self.assertEqual(metrics["cache_limit_bytes"], 10737418240)
        self.assertEqual(metrics["jvm_memory_max_bytes"], 245504 * 1024)
        self.assertEqual(metrics["last_serve_at"], "2026-08-18T12:40:18Z")
        self.assertNotIn("secret", stored)
        self.assertNotIn("203.0.113", stored)

    def test_first_log_scan_baselines_p2p_metrics_into_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            downloads = root / "downloads"
            downloads.mkdir()
            log_file = root / "log_out"
            log_file.write_text(
                "2026-08-18T12:40:18Z [info] {request/address} Code=200 Bytes=1024 GET /h/redacted HTTP/1.1\n",
                encoding="utf-8",
            )
            store = StateStore(root / "state.sqlite3")
            config = ObserverConfig("client-a", downloads, root / "state.sqlite3", log_file=log_file)
            try:
                scan_and_enqueue(store, config)
                heartbeat = next(event for event in store.pending() if event["type"] == "agent.heartbeat")
            finally:
                store.close()

        self.assertEqual(heartbeat["metrics"]["serve_requests_total"], 1)
        self.assertEqual(heartbeat["metrics"]["serve_bytes_total"], 1024)


if __name__ == "__main__":
    unittest.main()
