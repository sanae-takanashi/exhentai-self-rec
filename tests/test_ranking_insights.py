"""Synthetic, read-only report checks; no production data or model is loaded."""
from datetime import datetime, timezone
import json
import sqlite3
import unittest

from exh_rec import db
from exh_rec.ranking_insights import ranking_insights


class RankingInsightsTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(db.SCHEMA)
        self.addCleanup(self.conn.close)
        self.now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        self.serial = 0

    def impression(self, gallery="g1", surface="review", policy="personalized_trial",
                   artifact="a1", trained="2026-11-01", version="v5", visible=True,
                   protocol="viewport-v1", created="2026-11-04 10:00:00", context=None):
        self.conn.execute("INSERT OR IGNORE INTO galleries(url,title) VALUES (?,?)", (gallery, gallery))
        self.serial += 1
        context = context if context is not None else json.dumps({
            "ranking_policy": policy, "ranking_artifact_id": artifact, "ranking_trained_at": trained})
        return self.conn.execute("""INSERT INTO recommendation_impressions
            (request_id,gallery_url,surface,position,model_version,ranking_context_json,
             visibility_protocol,visible_at,created_at) VALUES (?,?,?,0,?,?,?,?,?)""",
            (str(self.serial), gallery, surface, version, context, protocol,
             created if visible else None, created)).lastrowid

    def feedback(self, gallery, impression=None, vote=1, reason=None, surface="review",
                 created="2026-11-04 11:00:00"):
        self.conn.execute("INSERT OR IGNORE INTO galleries(url,title) VALUES (?,?)", (gallery, gallery))
        fid = self.conn.execute("""INSERT INTO feedback(gallery_url,vote,reason_code,surface,created_at)
                                  VALUES (?,?,?,?,?)""", (gallery, vote, reason, surface, created)).lastrowid
        if impression is not None:
            self.conn.execute("INSERT INTO feedback_impression_links VALUES (?,?)", (fid, impression))
        return fid

    def report(self, **kwargs):
        return ranking_insights(self.conn, now=self.now, **kwargs)

    def test_empty_and_read_only(self):
        self.conn.execute("PRAGMA query_only=ON")
        report = self.report()
        self.assertEqual(report["cohorts"], [])
        self.assertEqual(report["summary"]["delivered_galleries"], 0)
        self.assertFalse(report["review_milestone"]["automatic_promotion"])

    def test_distinct_galleries_events_and_unknown_are_separate(self):
        first = self.impression()
        self.impression()
        self.impression("unseen", visible=False)
        self.impression("old-client", protocol=None)
        self.feedback("g1", first)
        self.conn.execute("PRAGMA query_only=ON")
        row = self.report()["cohorts"][0]
        self.assertEqual((row["delivered_events"], row["delivered_galleries"]), (4, 3))
        self.assertEqual((row["visible_events"], row["visible_galleries"]), (2, 1))
        self.assertEqual(row["unknown_preference_galleries"], 2)
        self.assertEqual(row["negative"], 0)
        self.assertEqual(row["observed_positive_per_100_visible"], 100)
        self.assertEqual(row["visibility_uninstrumented_events"], 1)

    def test_latest_correction_and_undo_restore_exact_link(self):
        impression = self.impression()
        self.feedback("g1", impression)
        correction = self.feedback("g1", vote=-1)
        report = self.report()
        self.assertEqual(report["cohorts"][0]["positive"], 0)
        self.assertEqual(report["summary"]["unknown_attribution_reasons"], {"no_link": 1})
        self.conn.execute("DELETE FROM feedback WHERE id=?", (correction,))
        self.assertEqual(self.report()["cohorts"][0]["positive"], 1)

    def test_neutral_duplicate_and_negative_reasons(self):
        for gallery, vote, reason in [("neutral", 0, None), ("dup", -1, "duplicate_update"),
                                      ("bad", -1, "art_style")]:
            self.feedback(gallery, self.impression(gallery), vote, reason)
        row = self.report()["cohorts"][0]
        self.assertEqual((row["neutral"], row["negative"], row["excluded_duplicate_update"]), (1, 1, 1))
        self.assertEqual(row["negative_reasons"], {"art_style": 1})
        self.assertEqual(row["explicit_outcomes"], 1)
        self.assertEqual(row["unknown_preference_galleries"], 1)

    def test_exact_cohorts_and_cross_cohort_overlap(self):
        self.impression(policy="legacy")
        trial = self.impression()
        self.impression(artifact="a2")
        self.impression(trained="2026-11-02")
        self.impression(version="v6")
        self.impression(surface="low-interest")
        self.feedback("g1", trial)
        report = self.report()
        self.assertEqual(report["summary"]["cohort_count"], 6)
        self.assertEqual(report["summary"]["delivered_galleries"], 1)
        self.assertEqual(sum(c["positive"] for c in report["cohorts"]), 1)
        self.assertEqual(len(self.report(surface="low-interest")["cohorts"]), 1)

    def test_unknown_and_mismatched_links_do_not_receive_credit(self):
        wrong = self.impression("other")
        self.feedback("g1", wrong)
        self.feedback("unlinked")
        self.feedback("bad-surface", self.impression("bad-surface", surface="discovery"))
        future = self.impression("future", created="2026-11-04 12:00:00")
        self.feedback("future", future)
        report = self.report()
        self.assertEqual(report["summary"]["unknown_attribution_reasons"], {"mismatched_link": 3, "no_link": 1})
        self.assertEqual(sum(c["explicit_outcomes"] for c in report["cohorts"]), 0)

    def test_time_boundaries_outside_window_and_future_correction(self):
        self.impression("start", created="2026-11-04 12:00:00")
        self.impression("end", created="2026-11-05 12:00:00")
        old = self.impression("old", created="2026-11-04 11:59:59")
        self.feedback("old", old, created="2026-11-04 13:00:00")
        current = self.impression("current", created="2026-11-04T13:00:00+00:00")
        self.feedback("current", current, created="2026-11-04 14:00:00")
        self.feedback("current", vote=-1, created="2026-11-06 00:00:00")
        report = self.report(days=1)
        self.assertEqual(report["summary"]["delivered_galleries"], 2)
        self.assertEqual(report["summary"]["unknown_attribution_reasons"], {"outside_delivery_window_or_surface": 1})
        self.assertEqual(sum(c["positive"] for c in report["cohorts"]), 1)

    def test_visibility_after_feedback_does_not_inflate_visible_outcomes(self):
        impression = self.impression()
        self.conn.execute("UPDATE recommendation_impressions SET visible_at='2026-11-04 12:00:00'")
        self.feedback("g1", impression)
        row = self.report()["cohorts"][0]
        self.assertEqual(row["positive"], 1)
        self.assertEqual(row["visible_positive"], 0)

    def test_malformed_context_and_missing_identity_stay_unknown(self):
        self.impression(context="{broken")
        self.impression("list", context="[]")
        row = self.report()["cohorts"][0]
        self.assertEqual(row["policy"], "unknown")
        self.assertEqual(row["artifact_id"], "unknown")
        self.assertEqual(row["delivered_galleries"], 2)

    def test_validation(self):
        for days in (0, 366, True, 1.5, "30"):
            with self.assertRaises(ValueError):
                self.report(days=days)
        with self.assertRaises(ValueError):
            self.report(surface="invented")

    def test_cross_surface_correction_supersedes_previous_review_outcome(self):
        impression = self.impression()
        self.feedback("g1", impression)
        self.feedback("g1", surface="history", vote=0)
        review = self.report(surface="review")
        self.assertEqual(review["cohorts"][0]["positive"], 0)
        self.assertEqual(review["cohorts"][0]["unknown_preference_galleries"], 1)

    def test_milestone_is_observational_and_excludes_nonpreferences(self):
        for index in range(100):
            gallery = f"explicit-{index}"
            self.feedback(gallery, self.impression(gallery), vote=1 if index % 2 else -1)
        self.feedback("dup", self.impression("dup"), reason="duplicate_update")
        self.feedback("neutral", self.impression("neutral"), vote=0)
        report = self.report()
        self.assertEqual(report["review_milestone"]["window_attributed_explicit_galleries"], 100)
        self.assertTrue(report["review_milestone"]["review_due"])
        self.assertFalse(report["review_milestone"]["automatic_promotion"])
        self.assertTrue(report["observational"])


if __name__ == "__main__":
    unittest.main()
