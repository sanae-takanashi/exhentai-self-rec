import os
import sqlite3
import unittest
from unittest.mock import patch

from exh_rec import db
from exh_rec.app import ApiError, require_hath_authorization
from exh_rec.exhentai import Gallery
from exh_rec.hath import HathPayloadError, hath_status, ingest_event_batch
from exh_rec.personalized import training_examples
from exh_rec.recommender import recommend, record_feedback, retrain_model, store_galleries


def completed_event(event_id="event-1", gid="123"):
    return {
        "schema": "hath-observer-events-v1",
        "client_id": "desktop-a",
        "events": [
            {
                "event_id": event_id,
                "type": "download.completed",
                "occurred_at": "2026-08-18T08:30:00Z",
                "gid": gid,
                "resolution": "org",
                "title": "Downloaded Gallery",
                "directory_name": "Downloaded Gallery [123]",
                "downloaded_files": 20,
                "total_files": 20,
                "downloaded_bytes": 123456,
            }
        ],
    }


class HathIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        store_galleries(
            self.conn,
            [
                Gallery(
                    url="https://exhentai.org/g/123/abc/",
                    gid="123",
                    token="abc",
                    title="Downloaded Gallery",
                    tags=["artist:downloaded"],
                ),
                Gallery(
                    url="https://exhentai.org/g/124/def/",
                    gid="124",
                    token="def",
                    title="Candidate Gallery",
                    tags=["artist:other"],
                ),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_completed_event_is_idempotent_and_links_by_gid(self):
        first = ingest_event_batch(self.conn, completed_event())
        second = ingest_event_batch(self.conn, completed_event())
        status = hath_status(self.conn)

        self.assertEqual(first["accepted"], 1)
        self.assertTrue(first["completed_changed"])
        self.assertEqual(second["accepted"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertFalse(second["completed_changed"])
        self.assertEqual(status["counts"], {"completed": 1})
        self.assertEqual(status["downloads"][0]["gallery_url"], "https://exhentai.org/g/123/abc/")

    def test_completed_download_is_an_implicit_positive_until_explicit_feedback(self):
        ingest_event_batch(self.conn, completed_event())
        examples = {item["url"]: item for item in training_examples(self.conn)}

        self.assertEqual(examples["https://exhentai.org/g/123/abc/"]["label"], 1)
        self.assertEqual(examples["https://exhentai.org/g/123/abc/"]["label_source"], "hath-download")
        self.assertEqual(examples["https://exhentai.org/g/123/abc/"]["sample_weight"], 1.25)

        record_feedback(self.conn, "https://exhentai.org/g/123/abc/", vote=-1)
        examples = {item["url"]: item for item in training_examples(self.conn)}

        self.assertEqual(examples["https://exhentai.org/g/123/abc/"]["label"], 0)
        self.assertEqual(examples["https://exhentai.org/g/123/abc/"]["label_source"], "vote")

    def test_status_reports_client_totals_and_resolved_recommendation_signal(self):
        ingest_event_batch(self.conn, completed_event())

        status = hath_status(self.conn)
        summary = status["summary"]
        statistics = status["clients"][0]["statistics"]
        self.assertEqual(summary["tracked_downloads"], 1)
        self.assertEqual(summary["downloaded_files"], 20)
        self.assertEqual(summary["downloaded_bytes"], 123456)
        self.assertEqual(summary["linked_downloads"], 1)
        self.assertEqual(summary["signals"], {"hath-download": 1})
        self.assertEqual(summary["event_types"], {"download.completed": 1})
        self.assertEqual(statistics["completed_downloads"], 1)
        self.assertEqual(statistics["event_count"], 1)
        self.assertEqual(statistics["last_event_type"], "download.completed")
        self.assertEqual(status["downloads"][0]["recommendation_signal"], "hath-download")

        record_feedback(self.conn, "https://exhentai.org/g/123/abc/", vote=1)
        status = hath_status(self.conn)
        self.assertEqual(status["summary"]["signals"], {"positive-vote": 1})
        self.assertEqual(status["downloads"][0]["recommendation_signal"], "positive-vote")

        self.conn.execute(
            "INSERT INTO gallery_marks(gallery_url, kind) VALUES (?, 'favorite')",
            ("https://exhentai.org/g/123/abc/",),
        )
        status = hath_status(self.conn)
        self.assertEqual(status["summary"]["signals"], {"favorite": 1})
        self.assertEqual(status["downloads"][0]["recommendation_signal"], "favorite")

    def test_completed_download_is_hidden_from_review_but_available_when_including_rated(self):
        ingest_event_batch(self.conn, completed_event())
        retrain_model(self.conn)

        normal_urls = [item["url"] for item in recommend(self.conn)]
        all_urls = [item["url"] for item in recommend(self.conn, include_rated=True)]

        self.assertNotIn("https://exhentai.org/g/123/abc/", normal_urls)
        self.assertIn("https://exhentai.org/g/123/abc/", all_urls)

    def test_failed_download_is_not_a_training_label(self):
        payload = completed_event()
        payload["events"][0].update({"event_id": "failed-1", "type": "download.failed"})
        ingest_event_batch(self.conn, payload)

        self.assertEqual(training_examples(self.conn), [])

    def test_bad_payload_is_rejected(self):
        with self.assertRaises(HathPayloadError):
            ingest_event_batch(
                self.conn,
                {
                    "schema": "hath-observer-events-v1",
                    "client_id": "bad client id",
                    "events": [{}],
                },
            )

    def test_orphan_download_links_when_gallery_is_discovered_later(self):
        ingest_event_batch(self.conn, completed_event(event_id="orphan-1", gid="999"))
        before = self.conn.execute(
            "SELECT gallery_url FROM hath_downloads WHERE gid = '999'"
        ).fetchone()
        store_galleries(
            self.conn,
            [Gallery(url="https://exhentai.org/g/999/newtoken/", gid="999", token="newtoken", title="Later")],
        )
        after = self.conn.execute(
            "SELECT gallery_url FROM hath_downloads WHERE gid = '999'"
        ).fetchone()

        self.assertIsNone(before["gallery_url"])
        self.assertEqual(after["gallery_url"], "https://exhentai.org/g/999/newtoken/")

    def test_idle_status_clears_the_last_active_download(self):
        ingest_event_batch(self.conn, completed_event())
        ingest_event_batch(
            self.conn,
            {
                "schema": "hath-observer-events-v1",
                "client_id": "desktop-a",
                "events": [
                    {
                        "event_id": "idle-1",
                        "type": "client.status",
                        "occurred_at": "2026-08-18T08:31:00Z",
                        "metrics": {"status": "idle"},
                    }
                ],
            },
        )

        client = hath_status(self.conn)["clients"][0]
        self.assertEqual(client["status"], "idle")
        self.assertIsNone(client["active_gid"])
        self.assertIsNone(client["active_title"])

    def test_heartbeat_keeps_only_aggregate_safe_metrics(self):
        ingest_event_batch(
            self.conn,
            {
                "schema": "hath-observer-events-v1",
                "client_id": "desktop-a",
                "events": [
                    {
                        "event_id": "heartbeat-safe-1",
                        "type": "agent.heartbeat",
                        "occurred_at": "2026-08-18T08:31:00Z",
                        "process_running": True,
                        "metrics": {
                            "serve_requests_total": 42,
                            "serve_bytes_1h": 123456,
                            "last_serve_at": "2026-08-18T08:30:00Z",
                            "raw_url": "http://example.invalid/rpc?actkey=secret",
                            "remote_ip": "203.0.113.9",
                        },
                    }
                ],
            },
        )

        metrics = hath_status(self.conn)["clients"][0]["metrics"]
        self.assertEqual(metrics["serve_requests_total"], 42)
        self.assertEqual(metrics["serve_bytes_1h"], 123456)
        self.assertNotIn("raw_url", metrics)
        self.assertNotIn("remote_ip", metrics)

    def test_receiver_token_is_required_and_compared(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ApiError) as missing:
                require_hath_authorization("Bearer secret")
            self.assertEqual(missing.exception.status.value, 503)

        with patch.dict(os.environ, {"EXH_REC_HATH_TOKEN": "secret"}, clear=True):
            require_hath_authorization("Bearer secret")
            with self.assertRaises(ApiError) as invalid:
                require_hath_authorization("Bearer wrong")
            self.assertEqual(invalid.exception.status.value, 401)


if __name__ == "__main__":
    unittest.main()
