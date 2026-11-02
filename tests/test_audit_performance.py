import bisect
import json
import random
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from exh_rec import audit, db


def reference_outcomes(conn, window_start, window_end=None, *, visible_only=False):
    """Pre-optimization scan, retained as an independent temporal-join oracle."""
    timestamp = "visible_at" if visible_only else "created_at"
    exposures = {}
    for row in conn.execute(
        f"SELECT gallery_url, {timestamp} AS created_at, ranking_context_json "
        f"FROM recommendation_impressions WHERE {timestamp} >= ? "
        f"AND (? IS NULL OR {timestamp} < ?) ORDER BY gallery_url, {timestamp}, id",
        (window_start, window_end, window_end),
    ):
        exposures.setdefault(str(row["gallery_url"]), []).append(row)
    outcomes = []
    for url, events in audit._outcome_events(conn).items():
        impressions = exposures.get(url) or []
        times = [str(row["created_at"]) for row in impressions]
        for event_at, positive in events:
            if window_end is not None and event_at >= window_end:
                continue
            index = bisect.bisect_right(times, event_at) - 1
            if index < 0:
                continue
            row = impressions[index]
            try:
                context = json.loads(row["ranking_context_json"] or "{}")
            except json.JSONDecodeError:
                context = {}
            outcomes.append({"gallery_url": url, "impression_at": str(row["created_at"]),
                             "outcome_at": event_at, "positive": bool(positive),
                             "context": context if isinstance(context, dict) else {}})
            break
    return outcomes


class AuditQueryPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.addCleanup(self.conn.close)

    def exposure(self, url, created, visible, context):
        self.conn.execute(
            "INSERT INTO recommendation_impressions(request_id,gallery_url,surface,position,"
            "created_at,visible_at,ranking_context_json) VALUES (?,?, 'review',0,?,?,?)",
            (str(self.conn.total_changes), url, created, visible, context),
        )

    def feedback(self, url, created, vote=1, score=None):
        self.conn.execute("INSERT INTO feedback(gallery_url,created_at,vote,score) VALUES (?,?,?,?)",
                          (url, created, vote, score))

    def test_latest_exposure_tie_and_first_eligible_outcome(self):
        url = "https://example.test/g/1/a/"
        self.exposure(url, "2026-08-01 12:00:00", "2026-08-01 12:03:00", '{"id":1}')
        self.exposure(url, "2026-08-01 12:00:00", "2026-08-01 12:02:00", '{"id":2}')
        self.feedback(url, "2026-08-01 11:00:00", -1)
        self.feedback(url, "2026-08-01 12:00:00", score=3)
        self.feedback(url, "2026-08-01 12:00:00", 1)
        self.feedback(url, "2026-08-01 12:04:00", -1)
        args = (self.conn, "2026-08-01 00:00:00", "2026-08-02 00:00:00")
        delivered = audit._prospective_outcomes(*args)
        visible = audit._prospective_outcomes(*args, visible_only=True)
        self.assertEqual(delivered, reference_outcomes(*args))
        self.assertEqual(visible, reference_outcomes(*args, visible_only=True))
        self.assertEqual(delivered[0]["context"], {"id": 2})
        self.assertTrue(delivered[0]["positive"])
        self.assertEqual(visible[0]["context"], {"id": 1})
        self.assertFalse(visible[0]["positive"])

    def test_randomized_temporal_joins_and_full_report_are_identical(self):
        rng = random.Random(2901)
        origin = datetime(2026, 7, 15)
        stamp = lambda hour: (origin + timedelta(hours=hour)).isoformat(sep=" ")
        contexts = ["{bad", "[]", "null", "{}", json.dumps({
            "legacy_bottom_20": True, "personalized_bottom_20": False,
            "personalized_model_version": "fixture", "prospective_threshold_model_version": "fixture",
            "prospective_threshold": 0.2, "prospective_threshold_triage": True,
        })]
        for index in range(35):
            url = f"https://example.test/g/{index}/a/"
            for _ in range(30):
                created = rng.randrange(1100)
                visible = stamp(created + rng.randrange(8)) if rng.random() < .6 else None
                self.exposure(url, stamp(created), visible, rng.choice(contexts))
            for _ in range(index % 7):
                self.feedback(url, stamp(rng.randrange(1100)), rng.choice([-1, 0, 1]),
                              rng.choice([None, 1, 3, 4, 5]))
            if index % 3 == 0:
                self.conn.execute("INSERT INTO gallery_marks(gallery_url,kind,updated_at) VALUES (?,?,?)",
                                  (url, rng.choice(["favorite", "ban"]), stamp(rng.randrange(1100))))
        for end in (None, "2026-08-10 00:00:00"):
            for visible in (False, True):
                args = (self.conn, "2026-08-01 00:00:00", end)
                self.assertEqual(audit._prospective_outcomes(*args, visible_only=visible),
                                 reference_outcomes(*args, visible_only=visible))
        current = audit.low_interest_audit_report(self.conn, as_of="2026-08-20")
        with patch.object(audit, "_prospective_outcomes", side_effect=reference_outcomes):
            self.assertEqual(current, audit.low_interest_audit_report(self.conn, as_of="2026-08-20"))
        self.assertFalse(current["ready"])
        self.assertFalse(current["checks"]["candidate_protocol_validated"])

    def test_duplicate_exposures_preserve_distinct_threshold_cohorts(self):
        for version in ("v1", "v2"):
            context = json.dumps({"personalized_model_version": version,
                "prospective_threshold_model_version": version,
                "prospective_threshold": .2, "prospective_threshold_triage": True})
            self.exposure("fixture", "2026-08-01 12:00:00", None, context)
        before = audit.low_interest_audit_report(self.conn, as_of="2026-08-10")
        for _ in range(50):
            self.exposure("fixture", "2026-08-01 12:00:00", None, context)
        after = audit.low_interest_audit_report(self.conn, as_of="2026-08-10")
        self.assertEqual(after["threshold_cohort_count"], 2)
        self.assertFalse(after["checks"]["single_threshold_cohort"])
        self.assertEqual(after["prospective_threshold_exposures"], before["prospective_threshold_exposures"])
        self.assertEqual(after["visibility_counts"]["delivered"], 52)


if __name__ == "__main__":
    unittest.main()
