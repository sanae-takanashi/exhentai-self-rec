import json
import sqlite3
import unittest
from datetime import date, timedelta

from exh_rec import db
from exh_rec.audit import (
    low_interest_audit_report,
    prepare_daily_low_interest_audits,
    record_visible_impressions,
    wilson_upper_bound,
)
from exh_rec.exhentai import Gallery
from exh_rec.recommender import (
    annotate_interest_bands,
    record_feedback,
    record_impressions,
    resolve_low_interest_threshold_policy,
    store_galleries,
)


class LowInterestAuditTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_daily_audit_uniformly_promotes_bottom_band_and_records_probability(self):
        galleries = [
            Gallery(url=f"https://exhentai.org/g/{1000 + index}/a/", gid=str(1000 + index), token="a", title=str(index))
            for index in range(50)
        ]
        store_galleries(self.conn, galleries)
        scored = [
            {
                "url": gallery.url,
                "score": 1.0 - index / 100,
                "rank_score": 1.0 - index / 100,
                "served_rank_score": 1.0 - index / 100,
                "served_model": "legacy",
                "served_rank": index + 1,
                "legacy_score": 1.0 - index / 100,
                "legacy_rank": index + 1,
            }
            for index, gallery in enumerate(galleries)
        ]
        annotate_interest_bands(scored, 20)

        status = prepare_daily_low_interest_audits(
            self.conn, scored, 6, audit_date="2026-08-01"
        )
        audited = [item for item in scored if item.get("audit_id")]

        self.assertEqual(status["frame_size"], 10)
        self.assertEqual(status["selected_count"], 6)
        self.assertEqual(len(audited), 6)
        self.assertTrue(all(not item["low_interest"] for item in audited))
        self.assertTrue(all(item["audit_original_low_interest"] for item in audited))
        self.assertTrue(all(abs(item["audit_selection_probability"] - 0.6) < 1e-9 for item in audited))

        rerun = prepare_daily_low_interest_audits(
            self.conn, scored, 6, audit_date="2026-08-01"
        )
        self.assertAlmostEqual(rerun["selection_probability"], 0.6)

        record_feedback(self.conn, audited[0]["url"], vote=1, surface="audit", retrain=False)
        row = self.conn.execute(
            "SELECT status, outcome_positive, outcome_source FROM low_interest_audits WHERE id = ?",
            (audited[0]["audit_id"],),
        ).fetchone()
        self.assertEqual(dict(row), {"status": "completed", "outcome_positive": 1, "outcome_source": "feedback"})

    def test_each_day_avoids_recently_audited_tail_items(self):
        galleries = [
            Gallery(url=f"https://exhentai.org/g/repeat-{index}/a/", gid=f"repeat-{index}", token="a", title=str(index))
            for index in range(20)
        ]
        store_galleries(self.conn, galleries)

        def scored_items():
            items = [
                {"url": gallery.url, "score": 1.0 - index / 10, "served_rank_score": 1.0 - index / 10}
                for index, gallery in enumerate(galleries)
            ]
            annotate_interest_bands(items, 20)
            return items

        first = scored_items()
        prepare_daily_low_interest_audits(self.conn, first, 2, audit_date="2026-08-01")
        first_urls = {item["url"] for item in first if item.get("audit_id")}
        self.conn.execute("UPDATE low_interest_audits SET visible_at='2026-08-01 12:00:00'")
        second = scored_items()
        prepare_daily_low_interest_audits(self.conn, second, 2, audit_date="2026-08-02")
        second_urls = {item["url"] for item in second if item.get("audit_id")}

        self.assertTrue(first_urls.isdisjoint(second_urls))
        self.assertEqual(len(first_urls), 2)
        self.assertEqual(len(second_urls), 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM low_interest_audits").fetchone()[0],
            4,
        )

    def test_same_day_audit_identity_survives_reranking(self):
        galleries = [
            Gallery(url=f"https://exhentai.org/g/rerank-{index}/a/", gid=f"rerank-{index}", token="a", title=str(index))
            for index in range(10)
        ]
        store_galleries(self.conn, galleries)
        initial = [
            {"url": gallery.url, "score": 1.0 - index / 10, "served_rank_score": 1.0 - index / 10}
            for index, gallery in enumerate(galleries)
        ]
        annotate_interest_bands(initial, 20)
        prepare_daily_low_interest_audits(self.conn, initial, 1, audit_date="2026-08-01")
        audited_url = next(item["url"] for item in initial if item.get("audit_id"))

        reranked = list(reversed(initial))
        for item in reranked:
            item.pop("audit_id", None)
            item.pop("audit_source", None)
            item.pop("audit_selection_probability", None)
        annotate_interest_bands(reranked, 20)
        prepare_daily_low_interest_audits(self.conn, reranked, 1, audit_date="2026-08-01")
        audited = next(item for item in reranked if item["url"] == audited_url)

        self.assertIsNotNone(audited.get("audit_id"))
        self.assertFalse(audited["low_interest"])

    def test_unexposed_pending_audit_survives_later_active_day(self):
        galleries = [
            Gallery(url=f"https://exhentai.org/g/pending-{index}/a/", gid=f"pending-{index}", token="a", title=str(index))
            for index in range(10)
        ]
        store_galleries(self.conn, galleries)

        def scored_items():
            items = [
                {"url": gallery.url, "score": 1.0 - index / 10, "served_rank_score": 1.0 - index / 10}
                for index, gallery in enumerate(galleries)
            ]
            annotate_interest_bands(items, 20)
            return items

        first = scored_items()
        prepare_daily_low_interest_audits(self.conn, first, 1, audit_date="2026-08-01")
        audit_id = next(item["audit_id"] for item in first if item.get("audit_id"))

        second = scored_items()
        prepare_daily_low_interest_audits(self.conn, second, 1, audit_date="2026-08-08")
        status = self.conn.execute(
            "SELECT status, exposed_at FROM low_interest_audits WHERE id = ?", (audit_id,)
        ).fetchone()

        self.assertEqual(status["status"], "pending")
        self.assertIsNone(status["exposed_at"])

        self.conn.execute(
            "UPDATE low_interest_audits SET visible_at = '2026-08-08 12:00:00' WHERE id = ?",
            (audit_id,),
        )
        third = scored_items()
        prepare_daily_low_interest_audits(self.conn, third, 1, audit_date="2026-08-09")
        self.assertEqual(
            self.conn.execute("SELECT status FROM low_interest_audits WHERE id = ?", (audit_id,)).fetchone()[0],
            "incomplete",
        )

    def test_impression_persists_shadow_context_and_validates_audit_gallery(self):
        first = Gallery(url="https://exhentai.org/g/impression-a/a/", gid="impression-a", token="a", title="A")
        second = Gallery(url="https://exhentai.org/g/impression-b/a/", gid="impression-b", token="a", title="B")
        store_galleries(self.conn, [first, second])
        cursor = self.conn.execute(
            """
            INSERT INTO low_interest_audits(
                audit_date, gallery_url, selection_probability, frame_size
            ) VALUES ('2026-08-01', ?, 0.1, 20)
            """,
            (first.url,),
        )
        audit_id = int(cursor.lastrowid)

        inserted = record_impressions(
            self.conn,
            "shadow-request",
            "review",
            [{
                "gallery_url": second.url,
                "position": 0,
                "audit_id": audit_id,
                "legacy_score": 1.25,
                "legacy_rank": 4,
                "personalized_model_version": "shadow-v1",
                "personalized_like_probability": 0.23,
                "personalized_rank": 9,
            }],
        )
        row = self.conn.execute(
            "SELECT audit_id, feature_snapshot_id, ranking_context_json FROM recommendation_impressions"
        ).fetchone()
        context = json.loads(row["ranking_context_json"])

        self.assertEqual(inserted, 1)
        self.assertIsNone(row["audit_id"])
        self.assertIsNotNone(row["feature_snapshot_id"])
        self.assertEqual(context["legacy_rank"], 4)
        self.assertEqual(context["personalized_model_version"], "shadow-v1")
        self.assertEqual(context["personalized_like_probability"], 0.23)
        self.assertIsNone(
            self.conn.execute("SELECT exposed_at FROM low_interest_audits WHERE id = ?", (audit_id,)).fetchone()[0]
        )

    def test_wilson_gate_allows_seven_of_150_but_rejects_eight(self):
        self.assertLessEqual(wilson_upper_bound(7, 150), 0.10)
        self.assertGreater(wilson_upper_bound(8, 150), 0.10)

        start = date(2026, 7, 2)
        galleries = [
            Gallery(url=f"https://exhentai.org/g/audit-{index}/a/", gid=f"audit-{index}", token="a", title=str(index))
            for index in range(150)
        ]
        store_galleries(self.conn, galleries)
        for index, gallery in enumerate(galleries):
            audit_day = start + timedelta(days=index // 5)
            self.conn.execute(
                """
                INSERT INTO low_interest_audits(
                    audit_date, gallery_url, status, selection_probability,
                    frame_size, outcome_positive, completed_at, visible_at
                ) VALUES (?, ?, 'completed', 0.1, 50, ?, ?, ?)
                """,
                (
                    audit_day.isoformat(),
                    gallery.url,
                    1 if index < 7 else 0,
                    f"{audit_day.isoformat()} 12:00:00",
                    f"{audit_day.isoformat()} 11:00:00",
                ),
            )
        for index, gallery in enumerate(galleries[:50]):
            impression_at = start + timedelta(days=index % 30)
            self.conn.execute(
                """
                INSERT INTO recommendation_impressions(
                    request_id, gallery_url, surface, position, ranking_context_json, created_at
                ) VALUES (?, ?, 'review', 0, ?, ?)
                """,
                (
                    f"request-{index}",
                    gallery.url,
                    json.dumps({
                        "prospective_threshold_triage": index >= 20,
                        "prospective_threshold": 0.2,
                        "prospective_threshold_model_version": "test-v1",
                        "personalized_model_version": "test-v1",
                    }),
                    f"{impression_at.isoformat()} 08:00:00",
                ),
            )
            if index < 20:
                self.conn.execute(
                    "INSERT INTO feedback(gallery_url, vote, created_at) VALUES (?, 1, ?)",
                    (gallery.url, f"{impression_at.isoformat()} 09:00:00"),
                )

        report = low_interest_audit_report(self.conn, as_of="2026-08-05")

        self.assertFalse(report["ready"])
        self.assertTrue(report["checks"]["positive_rate_upper_95"])
        self.assertEqual(report["status"], "prospective-candidate-protocol-unavailable")
        self.assertEqual(report["completed_samples"], 150)
        self.assertEqual(report["positive_count"], 7)
        self.assertGreaterEqual(report["eligible_fold_count"], 3)
        self.assertEqual(report["positive_loss_rate"], 0.0)

        self.conn.execute(
            "UPDATE low_interest_audits SET outcome_positive = 1 WHERE gallery_url = ?",
            (galleries[7].url,),
        )
        rejected = low_interest_audit_report(self.conn, as_of="2026-08-05")
        self.assertFalse(rejected["ready"])
        self.assertEqual(rejected["status"], "audit-wilson-limit-exceeded")

    def test_missing_decisions_do_not_dilute_loss_and_mixed_thresholds_do_not_pool(self):
        for index, context in enumerate([
            {},
            {"legacy_bottom_20": False},
            {"legacy_bottom_20": True, "personalized_bottom_20": False},
            {"legacy_bottom_20": "false", "personalized_bottom_20": False},
        ]):
            gallery = Gallery(url=f"https://example.test/g/{index}/a/", gid=str(index), token="a", title="Test")
            store_galleries(self.conn, [gallery])
            self.conn.execute(
                """INSERT INTO recommendation_impressions
                (request_id,gallery_url,surface,position,ranking_context_json,created_at)
                VALUES (?,?,'review',0,?,'2026-08-01 10:00:00')""",
                (str(index), gallery.url, json.dumps(context)),
            )
            self.conn.execute(
                "INSERT INTO feedback(gallery_url,vote,created_at) VALUES (?,1,'2026-08-01 11:00:00')",
                (gallery.url,),
            )
        report = low_interest_audit_report(self.conn, as_of="2026-08-10")
        self.assertEqual(report["paired_positive_count"], 1)
        self.assertEqual(report["paired_excluded_positive_count"], 3)
        self.assertEqual(report["model_comparison"]["legacy"]["positive_loss_rate"], 1.0)
        self.assertIsNone(report["positive_loss_rate"])
        self.assertFalse(report["checks"]["single_threshold_cohort"])
        for index in range(4):
            self.conn.execute(
                "UPDATE recommendation_impressions SET ranking_context_json=? WHERE request_id=?",
                (json.dumps({
                    "prospective_threshold_triage": False,
                    "prospective_threshold": 0.2,
                    "prospective_threshold_model_version": f"v{index % 2}",
                    "personalized_model_version": f"v{index % 2}",
                }), str(index)),
            )
        mixed = low_interest_audit_report(self.conn, as_of="2026-08-10")
        self.assertEqual(mixed["threshold_cohort_count"], 2)
        self.assertIsNone(mixed["positive_loss_rate"])
        self.assertFalse(mixed["ready"])

    def test_report_does_not_use_outcomes_after_its_cutoff(self):
        gallery = Gallery(url="https://example.test/g/late/a/", gid="late", token="a", title="Late")
        store_galleries(self.conn, [gallery])
        self.conn.execute(
            """INSERT INTO recommendation_impressions
            (request_id,gallery_url,surface,position,created_at)
            VALUES ('late',?,'review',0,'2026-08-01 10:00:00')""", (gallery.url,)
        )
        self.conn.execute(
            "INSERT INTO feedback(gallery_url,vote,created_at) VALUES (?,1,'2026-09-01 10:00:00')",
            (gallery.url,),
        )
        self.assertEqual(
            low_interest_audit_report(self.conn, as_of="2026-08-10")["prospective_labeled"], 0
        )

    def test_acceptance_requires_active_days_in_addition_to_elapsed_span(self):
        for index in range(150):
            audit_day = "2026-07-02" if index < 75 else "2026-07-29"
            gallery_url = f"https://exhentai.org/g/sparse-{index}/a/"
            self.conn.execute(
                "INSERT INTO galleries(url, gid, token, title) VALUES (?, ?, 'a', ?)",
                (gallery_url, f"sparse-{index}", str(index)),
            )
            self.conn.execute(
                """
                INSERT INTO low_interest_audits(
                    audit_date, gallery_url, status, selection_probability,
                    frame_size, outcome_positive, completed_at, visible_at
                ) VALUES (?, ?, 'completed', 0.1, 50, 0, ?, ?)
                """,
                (
                    audit_day,
                    gallery_url,
                    f"{audit_day} 12:00:00",
                    f"{audit_day} 11:00:00",
                ),
            )
        report = low_interest_audit_report(self.conn, as_of="2026-08-05")

        self.assertEqual(report["elapsed_span_days"], 28)
        self.assertEqual(report["distinct_audit_days"], 2)
        self.assertEqual(report["active_audit_days"], 2)
        self.assertFalse(report["checks"]["active_audit_days"])
        self.assertEqual(report["status"], "audit-insufficient-active-days")

    def test_visible_requires_instrumented_delivery_and_is_idempotent(self):
        gallery = Gallery(url="https://example.test/g/visible/a/", gid="visible", token="a", title="Visible")
        store_galleries(self.conn, [gallery])
        item = {"gallery_url": gallery.url, "position": 0}
        self.assertEqual(record_visible_impressions(self.conn, "request", "review", [item]), 0)
        record_impressions(self.conn, "legacy", "review", [item])
        self.assertEqual(record_visible_impressions(self.conn, "legacy", "review", [item]), 0)
        item["visibility_protocol"] = "viewport-v1"
        record_impressions(self.conn, "request", "review", [item])
        self.assertIsNone(self.conn.execute(
            "SELECT visible_at FROM recommendation_impressions WHERE request_id='request'"
        ).fetchone()[0])
        self.assertEqual(record_visible_impressions(self.conn, "request", "preview", [item]), 0)
        self.assertEqual(record_visible_impressions(self.conn, "request", "review", [item]), 1)
        self.assertEqual(record_visible_impressions(self.conn, "request", "review", [item]), 0)

    def test_legacy_visibility_migration_does_not_backfill(self):
        old = sqlite3.connect(":memory:")
        old.row_factory = sqlite3.Row
        try:
            old.executescript(
                db.SCHEMA.replace("    visible_at TEXT,\n", "").replace("    visibility_protocol TEXT,\n", "")
            )
            old.execute(
                """INSERT INTO recommendation_impressions
                (request_id,gallery_url,surface,position) VALUES ('old','old','review',0)"""
            )
            for table, column in [
                ("recommendation_impressions", "visible_at"),
                ("recommendation_impressions", "visibility_protocol"),
                ("low_interest_audits", "visible_at"),
            ]:
                db.ensure_column(old, table, column, "TEXT")
                db.ensure_column(old, table, column, "TEXT")
            row = old.execute("SELECT visible_at,visibility_protocol FROM recommendation_impressions").fetchone()
            self.assertIsNone(row["visible_at"])
            self.assertIsNone(row["visibility_protocol"])
        finally:
            old.close()

    def test_carried_audit_preserves_identity_and_freezes_daily_budget(self):
        galleries = [
            Gallery(url=f"https://example.test/g/carry-{i}/a/", gid=f"carry-{i}", token="a", title="Carry")
            for i in range(20)
        ]
        store_galleries(self.conn, galleries)

        def scored():
            items = [{"url": g.url, "score": i} for i, g in enumerate(galleries)]
            annotate_interest_bands(items, 20)
            return items

        first = scored()
        prepare_daily_low_interest_audits(self.conn, first, 2, audit_date="2026-08-01")
        original = {i["audit_id"]: i["audit_selection_probability"] for i in first if i.get("audit_id")}
        later = scored()
        prepare_daily_low_interest_audits(self.conn, later, 2, audit_date="2026-08-08")
        self.assertEqual(original, {
            i["audit_id"]: i["audit_selection_probability"] for i in later if i.get("audit_id")
        })
        self.assertTrue(all(i["audit_carried"] for i in later if i.get("audit_id")))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM low_interest_audits").fetchone()[0], 2)
        self.conn.execute("UPDATE low_interest_audits SET status='completed',outcome_positive=0")
        refreshed = scored()
        prepare_daily_low_interest_audits(self.conn, refreshed, 2, audit_date="2026-08-08")
        self.assertFalse(any(i.get("audit_id") for i in refreshed))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM low_interest_audits").fetchone()[0], 2)

    def test_runtime_threshold_requires_prospective_gate_when_supplied(self):
        artifact = {
            "ready": True,
            "accepted": True,
            "score_scale": "probability",
            "model_version": "model-v1",
            "calibration": {"ready": True},
            "low_interest_threshold": {
                "ready": True,
                "status": "ready",
                "threshold": 0.2,
                "model_version": "model-v1",
            },
        }
        blocked = resolve_low_interest_threshold_policy(
            artifact,
            enabled=True,
            model_mode="hybrid",
            max_percent=35,
            prospective_validation={"ready": False, "status": "audit-insufficient-samples"},
        )
        active = resolve_low_interest_threshold_policy(
            artifact,
            enabled=True,
            model_mode="hybrid",
            max_percent=35,
            prospective_validation={"ready": True, "status": "ready"},
        )

        self.assertFalse(blocked["active"])
        self.assertEqual(blocked["status"], "audit-insufficient-samples")
        self.assertTrue(active["active"])


if __name__ == "__main__":
    unittest.main()
