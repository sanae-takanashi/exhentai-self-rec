import json
import sqlite3
import unittest
from unittest.mock import patch

from exh_rec import db
from exh_rec.preference_tools import (
    PreferenceConflict, apply_preference, preferences_report, preview_preference,
)


class PreferenceToolsTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.conn.execute("INSERT INTO bootstrap_tags(tag, weight) VALUES ('artist:alpha', 2)")
        self.conn.execute("INSERT INTO bootstrap_tags(tag, weight) VALUES ('language:english', 1)")
        self.conn.execute("""INSERT INTO feature_weights
            (feature, weight, positive_count, negative_count) VALUES ('tag:artist:alpha', 0.7, 3, 2)""")
        for index, outcome in enumerate((1, -1, 0, None, -1)):
            url = f"https://example.invalid/g/{index}/a/"
            self.conn.execute("INSERT INTO galleries(url, title, tags_json) VALUES (?, ?, ?)",
                              (url, f"Example {index}", json.dumps(["artist:alpha"])))
            if outcome is not None:
                self.conn.execute("INSERT INTO feedback(gallery_url, vote, reason_code) VALUES (?, ?, ?)",
                                  (url, outcome, "duplicate_update" if index == 4 else None))
        self.conn.execute("INSERT INTO galleries(url, title, tags_json) VALUES ('unmatched', 'Other', ?)",
                          (json.dumps(["artist:alphabet"]),))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def preview(self, **changes):
        payload = {"tag": "artist:alpha", "action": "set", "weight": 0, **changes}
        result = preview_preference(self.conn, payload)
        self.conn.commit()
        return result

    def apply(self, preview, **changes):
        return apply_preference(self.conn, {
            **preview["proposal"], "preview_id": preview["preview_id"], **changes,
        })

    def test_report_and_preview_are_query_only_and_do_not_train(self):
        self.conn.execute("PRAGMA query_only=ON")
        previous_changes = self.conn.total_changes
        with patch("exh_rec.recommender.retrain_model", side_effect=AssertionError("training")):
            report = preferences_report(self.conn)
            preview = self.preview()
        self.assertEqual(report["preferences"][0], {"tag": "artist:alpha", "weight": 2})
        self.assertEqual(report["learned_features"][0]["positive_count"], 3)
        self.assertEqual(preview["counts"], {
            "matched": 5, "positive": 1, "negative": 1, "neutral": 1,
            "unknown": 1, "duplicate_update": 1,
        })
        self.assertEqual(self.conn.total_changes, previous_changes)

    def test_preview_uses_latest_feedback_and_not_title_substring_for_namespaced_tag(self):
        self.conn.execute("INSERT INTO feedback(gallery_url, vote) VALUES (?, 1)",
                          ("https://example.invalid/g/1/a/",))
        self.conn.commit()
        preview = self.preview()
        self.assertEqual(preview["counts"]["positive"], 2)
        self.assertEqual(preview["counts"]["negative"], 0)
        self.assertEqual(preview["counts"]["matched"], 5)
        self.assertEqual(preview["previous_legacy_matching_contribution"], 2)
        self.assertEqual(preview["proposed_legacy_matching_contribution"], 0)
        self.assertEqual(preview["legacy_matching_score_delta"], -2)
        self.assertTrue(all(item["previous_contribution"] == 2 for item in preview["examples"]))

    def test_pause_changes_only_one_explicit_preference_and_can_restore(self):
        learned = [tuple(row) for row in self.conn.execute("SELECT * FROM feature_weights")]
        preview = self.preview()
        result = self.apply(preview)
        self.conn.commit()
        self.assertEqual(result["previous_weight"], 2)
        self.assertEqual(self.conn.execute("SELECT weight FROM bootstrap_tags WHERE tag='artist:alpha'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT weight FROM bootstrap_tags WHERE tag='language:english'").fetchone()[0], 1)
        self.assertEqual([tuple(row) for row in self.conn.execute("SELECT * FROM feature_weights")], learned)
        restore = self.preview(weight=result["previous_weight"])
        self.apply(restore)
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT weight FROM bootstrap_tags WHERE tag='artist:alpha'").fetchone()[0], 2)

    def test_add_normalizes_name_and_remove_requires_its_own_preview(self):
        preview = self.preview(tag="  LANGUAGE%3A%45NGLISH  ", weight=-2)
        self.assertEqual(preview["proposal"]["tag"], "language:english")
        self.apply(preview)
        self.conn.commit()
        add = self.preview(tag="artist:new_name", weight=1.5)
        self.assertIsNone(add["previous_weight"])
        self.apply(add)
        self.conn.commit()
        remove = self.preview(tag="artist:new_name", action="remove")
        self.assertEqual(remove["legacy_matching_score_delta"], -1.5)
        self.apply(remove)
        self.conn.commit()
        self.assertIsNone(self.conn.execute("SELECT * FROM bootstrap_tags WHERE tag='artist:new name'").fetchone())

    def test_changed_or_missing_proposal_binding_is_rejected(self):
        for changes in ({"weight": 1}, {"tag": "artist:beta"}, {"action": "remove"},
                        {"preview_id": None}, {"preview_id": "fake"}):
            preview = self.preview()
            with self.subTest(changes=changes), self.assertRaises(PreferenceConflict):
                self.apply(preview, **changes)
            self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT weight FROM bootstrap_tags WHERE tag='artist:alpha'").fetchone()[0], 2)

    def test_metadata_feedback_marks_and_preference_changes_invalidate_preview(self):
        writes = (
            "UPDATE galleries SET title='Changed' WHERE url='unmatched'",
            "UPDATE feedback SET vote=0 WHERE id=1",
            "INSERT INTO gallery_marks(gallery_url, kind) VALUES ('unmatched', 'favorite')",
            "UPDATE bootstrap_tags SET weight=3 WHERE tag='language:english'",
        )
        for statement in writes:
            preview = self.preview()
            self.conn.execute(statement)
            self.conn.commit()
            with self.subTest(statement=statement), self.assertRaises(PreferenceConflict):
                self.apply(preview)
            self.conn.rollback()

    def test_aba_preference_change_and_replay_are_rejected(self):
        preview = self.preview()
        self.conn.execute("UPDATE bootstrap_tags SET weight=3 WHERE tag='artist:alpha'")
        self.conn.execute("UPDATE bootstrap_tags SET weight=2 WHERE tag='artist:alpha'")
        self.conn.commit()
        with self.assertRaises(PreferenceConflict):
            self.apply(preview)
        self.conn.rollback()
        fresh = self.preview()
        self.apply(fresh)
        self.conn.commit()
        with self.assertRaises(PreferenceConflict):
            self.apply(fresh)
        self.conn.rollback()

    def test_apply_acquires_immediate_write_lock_before_revision_check(self):
        preview = self.preview()
        trace = []
        self.conn.set_trace_callback(trace.append)
        self.apply(preview)
        self.conn.set_trace_callback(None)
        self.assertEqual(trace[0], "BEGIN IMMEDIATE")
        self.assertTrue(any(statement.startswith("SELECT name, revision") for statement in trace))

    def test_apply_lock_prevents_a_second_tab_writing_during_validation(self):
        shared = sqlite3.connect("file:preference-lock-test?mode=memory&cache=shared", uri=True)
        shared.row_factory = sqlite3.Row
        self.conn.backup(shared)
        self.conn.close()
        self.conn = shared
        other = sqlite3.connect("file:preference-lock-test?mode=memory&cache=shared", uri=True)
        preview = self.preview()

        def competing_write(conn, payload):
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("UPDATE bootstrap_tags SET weight=9 WHERE tag='artist:alpha'")
            return preview_preference(conn, payload)

        try:
            with patch("exh_rec.preference_tools.preview_preference", side_effect=competing_write):
                self.apply(preview)
            self.conn.commit()
            self.assertEqual(other.execute("SELECT weight FROM bootstrap_tags WHERE tag='artist:alpha'").fetchone()[0], 0)
        finally:
            other.close()

    def test_missing_revision_tracking_fails_closed(self):
        self.conn.execute("DELETE FROM cache_revisions WHERE name='feedback'")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "revision tracking"):
            self.preview()

    def test_invalid_payloads_fail_without_mutation(self):
        payloads = [None, [], {}, {"tag": 4, "weight": 1}, {"tag": "x", "weight": True},
                    {"tag": "x", "weight": float("nan")}, {"tag": "x", "weight": float("inf")},
                    {"tag": "x", "weight": 10 ** 400}, {"tag": "x", "weight": "1"},
                    {"tag": "x", "weight": 10.1}, {"tag": "a,b", "weight": 1},
                    {"tag": "a\nb", "weight": 1}, {"tag": "x", "weight": 1, "action": []},
                    {"tag": "x" * 301, "weight": 1}]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                preview_preference(self.conn, payload)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bootstrap_tags").fetchone()[0], 2)

    def test_database_identity_binds_preview(self):
        preview = self.preview()
        self.conn.execute("UPDATE cache_revisions SET revision=revision+1 WHERE name='__identity__'")
        self.conn.commit()
        with self.assertRaises(PreferenceConflict):
            self.apply(preview)
        self.conn.rollback()

    def test_malformed_tags_do_not_break_preview_and_examples_are_bounded(self):
        for index in range(20):
            self.conn.execute("INSERT INTO galleries(url, title, tags_json) VALUES (?, ?, ?)",
                              (f"more-{index}", "Plain term", "{bad json" if index == 0 else "{}"))
        self.conn.commit()
        preview = self.preview(tag="plain term")
        self.assertEqual(preview["counts"]["matched"], 20)
        self.assertEqual(len(preview["examples"]), 12)


if __name__ == "__main__":
    unittest.main()
