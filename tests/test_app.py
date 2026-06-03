import unittest
import sqlite3
import threading
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

from exh_rec import db
from exh_rec.app import (
    ApiError,
    REFRESH_STATE,
    REFRESH_WAKE,
    background_refresh,
    build_queries,
    build_query_plan,
    enrich_feedback_gallery,
    enrich_recommendations,
    ensure_gallery_exists,
    fetch_and_store,
    feedback_history_payload,
    fetch_runs,
    format_generated_query,
    get_access_check,
    get_settings,
    import_preferences_payload,
    is_remote_search_preference,
    parse_bool,
    parse_feedback_request,
    query_int,
    recommend_candidate_limit,
    recommendation_payload,
    refresh_summary,
    save_settings,
    select_detail_candidates,
    select_recommendation_detail_candidates,
)
from exh_rec.exhentai import Gallery
from exh_rec.recommender import learned_query_tags, parse_bootstrap_tags, record_feedback, store_galleries, upsert_bootstrap_tags


class AppTest(unittest.TestCase):
    def test_build_queries_combines_bootstrap_and_learned_tags(self):
        queries = build_queries(
            [{"tag": "artist:seed", "weight": 1.0}, {"tag": "female:skip", "weight": -1.0}],
            ["artist:seed", "female:learned", ""],
        )
        self.assertEqual(queries, [None, "artist:seed", "female:learned"])

    def test_build_queries_quotes_generated_multi_word_tags(self):
        queries = build_queries(
            [{"tag": "artist:two words", "weight": 1.0}, {"tag": "female:skip", "weight": -1.0}],
            ["parody:space title", "plain phrase"],
        )
        self.assertEqual(queries, [None, 'artist:"two words"', 'parody:"space title"', '"plain phrase"'])

    def test_parse_bootstrap_underscore_input_generates_quoted_query(self):
        tags = parse_bootstrap_tags("artist:test_artist:2")

        queries = build_queries([{"tag": tag, "weight": weight} for tag, weight in tags], [])

        self.assertEqual(queries, [None, 'artist:"test artist"'])

    def test_build_queries_respects_force_query(self):
        self.assertEqual(build_queries([], ["artist:learned"], force_query="language:english"), ["language:english"])

    def test_build_queries_skips_local_metadata_bootstrap_for_remote_search(self):
        queries = build_queries(
            [
                {"tag": "category:manga", "weight": 2.0},
                {"tag": "uploader:trusted", "weight": 2.0},
                {"tag": "artist:remote", "weight": 1.0},
            ],
            [],
        )
        self.assertEqual(queries, [None, "artist:remote"])

    def test_build_query_plan_labels_sources_and_dedupes(self):
        plan = build_query_plan(
            [{"tag": "artist:seed", "weight": 1.0}, {"tag": "female:skip", "weight": -1.0}],
            ["artist:seed", "female:learned"],
        )
        self.assertEqual(
            [(item["query"], item["source"]) for item in plan],
            [(None, "recent"), ("artist:seed", "bootstrap"), ("female:learned", "learned")],
        )

    def test_build_query_plan_keeps_plain_labels_for_formatted_queries(self):
        plan = build_query_plan([{"tag": "artist:two words", "weight": 1.0}], ["plain phrase"])
        self.assertEqual(
            [(item["query"], item["label"]) for item in plan],
            [(None, "Recent galleries"), ('artist:"two words"', "artist:two words"), ('"plain phrase"', "plain phrase")],
        )

    def test_build_query_plan_prioritizes_bootstrap_by_weight(self):
        plan = build_query_plan(
            [
                {"tag": "artist:low", "weight": 0.5},
                {"tag": "artist:top", "weight": 4.0},
                {"tag": "artist:mid", "weight": 2.0},
                {"tag": "category:manga", "weight": 10.0},
            ],
            [],
        )

        self.assertEqual(
            [(item["query"], item["weight"]) for item in plan if item["source"] == "bootstrap"],
            [("artist:top", 4.0), ("artist:mid", 2.0), ("artist:low", 0.5)],
        )

    def test_build_query_plan_caps_bootstrap_after_highest_weights(self):
        tags = [{"tag": f"artist:{idx}", "weight": float(idx)} for idx in range(1, 9)]

        plan = build_query_plan(tags, [])

        bootstrap_queries = [item["query"] for item in plan if item["source"] == "bootstrap"]
        self.assertEqual(bootstrap_queries, ["artist:8", "artist:7", "artist:6", "artist:5", "artist:4", "artist:3"])

    def test_build_query_plan_force_query(self):
        plan = build_query_plan([], ["artist:learned"], force_query=" language:english ")
        self.assertEqual(plan, [{"query": "language:english", "source": "manual", "label": "language:english"}])

    def test_build_query_plan_blank_force_query_uses_normal_plan(self):
        plan = build_query_plan([{"tag": "artist:seed", "weight": 1.0}], ["female:learned"], force_query="   ")

        self.assertEqual(
            [(item["query"], item["source"]) for item in plan],
            [(None, "recent"), ("artist:seed", "bootstrap"), ("female:learned", "learned")],
        )

    def test_build_query_plan_skips_learned_tags_blocked_by_negative_bootstrap(self):
        plan = build_query_plan(
            [
                {"tag": "artist:seed", "weight": 1.0},
                {"tag": "female:avoid", "weight": -2.0},
            ],
            ["female:avoid", "female:good"],
        )

        self.assertEqual(
            [(item["query"], item["source"]) for item in plan],
            [(None, "recent"), ("artist:seed", "bootstrap"), ("female:good", "learned")],
        )

    def test_build_query_plan_force_query_can_use_negative_bootstrap_tag(self):
        plan = build_query_plan(
            [{"tag": "female:avoid", "weight": -2.0}],
            ["female:avoid"],
            force_query="female:avoid",
        )

        self.assertEqual(plan, [{"query": "female:avoid", "source": "manual", "label": "female:avoid"}])

    def test_parse_bool(self):
        self.assertTrue(parse_bool("1"))
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("on"))
        self.assertTrue(parse_bool("yes"))
        self.assertTrue(parse_bool(True))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool("off"))
        self.assertFalse(parse_bool(None))

    def test_query_int_defaults_invalid_values_and_clamps_bounds(self):
        self.assertEqual(query_int({"limit": ["bad"]}, "limit", default=40, lower=1, upper=100), 40)
        self.assertEqual(query_int({"limit": ["500"]}, "limit", default=40, lower=1, upper=100), 100)
        self.assertEqual(query_int({"offset": ["-5"]}, "offset", default=0, lower=0, upper=10000), 0)
        self.assertEqual(query_int({}, "limit", default=40, lower=1, upper=100), 40)

    def test_parse_feedback_request_validates_bad_numeric_values(self):
        self.assertEqual(parse_feedback_request({"vote": "1"}), (1, None))
        self.assertEqual(parse_feedback_request({"score": "5"}), (None, 5))

        for payload, message in [
            ({"score": "bad"}, "score must be between 1 and 5"),
            ({"score": 1.5}, "score must be between 1 and 5"),
            ({"score": True}, "score must be between 1 and 5"),
            ({"vote": "bad"}, "vote must be 1 or -1"),
            ({"vote": 0}, "vote must be 1 or -1"),
            ({}, "vote or score is required"),
        ]:
            with self.assertRaises(ApiError) as ctx:
                parse_feedback_request(payload)
            self.assertEqual(ctx.exception.status.value, 400)
            self.assertEqual(ctx.exception.message, message)

    def test_format_generated_query(self):
        self.assertEqual(format_generated_query("artist:one"), "artist:one")
        self.assertEqual(format_generated_query("artist:two words"), 'artist:"two words"')
        self.assertEqual(format_generated_query("two words"), '"two words"')
        self.assertEqual(format_generated_query('artist:"already quoted"'), 'artist:"already quoted"')

    def test_is_remote_search_preference(self):
        self.assertFalse(is_remote_search_preference("category:manga"))
        self.assertFalse(is_remote_search_preference("uploader:name"))
        self.assertTrue(is_remote_search_preference("artist:name"))
        self.assertTrue(is_remote_search_preference("plain title term"))

    def test_get_access_check_ignores_bad_json(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        db.set_setting(conn, "last_access_check", "{bad")
        self.assertIsNone(get_access_check(conn))
        conn.close()

    def test_fetch_runs_decodes_history_safely(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        conn.execute(
            """
            INSERT INTO fetch_runs(trigger, status, queries_json, fetched_count, stored_count, enriched_count, errors_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("manual", "partial", '["artist:test"]', 10, 9, 3, "{bad"),
        )

        history = fetch_runs(conn)

        self.assertEqual(history[0]["queries"], ["artist:test"])
        self.assertEqual(history[0]["errors"], [])
        self.assertEqual(history[0]["enriched_count"], 3)
        conn.close()

    def test_recommendation_payload_includes_last_fetch_summary(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        store_galleries(conn, [Gallery(url="https://exhentai.org/g/9/a/", gid="9", token="a", title="Payload Item")])
        conn.execute(
            """
            INSERT INTO fetch_runs(trigger, status, queries_json, fetched_count, stored_count, enriched_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("manual", "success", "[null]", 3, 3, 1),
        )

        payload = recommendation_payload(conn, limit=1)

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["last_fetch"]["status"], "success")
        self.assertEqual(payload["last_fetch"]["enriched_count"], 1)
        conn.close()

    def test_refresh_summary_explains_auto_refresh_readiness(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        db.set_setting(conn, "auto_refresh", "1")
        db.set_setting(conn, "refresh_interval_minutes", "45")

        waiting = refresh_summary(conn)
        self.assertFalse(waiting["ready"])
        self.assertEqual(waiting["message"], "Auto refresh waiting for a saved cookie")

        db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
        ready = refresh_summary(conn)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["interval_minutes"], 45)
        self.assertEqual(ready["message"], "Auto refresh every 45 minutes")

        db.set_setting(conn, "auto_refresh", "0")
        disabled = refresh_summary(conn)
        self.assertFalse(disabled["ready"])
        self.assertEqual(disabled["message"], "Auto refresh disabled")
        conn.close()

    def test_refresh_summary_includes_worker_schedule_metadata(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        previous = dict(REFRESH_STATE)
        try:
            db.set_setting(conn, "auto_refresh", "1")
            db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
            REFRESH_STATE.update(
                {
                    "last_checked_at": "2026-06-03 01:00:00",
                    "next_check_at": "2026-06-03 01:30:00",
                    "last_error": "temporary failure",
                }
            )

            summary = refresh_summary(conn)

            self.assertEqual(summary["last_checked_at"], "2026-06-03 01:00:00")
            self.assertEqual(summary["next_check_at"], "2026-06-03 01:30:00")
            self.assertEqual(summary["last_error"], "temporary failure")
        finally:
            REFRESH_STATE.clear()
            REFRESH_STATE.update(previous)
            conn.close()

    def test_feedback_history_payload_includes_gallery_and_latest_signal(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        gallery_url = "https://exhentai.org/g/10/a/"
        store_galleries(conn, [Gallery(url=gallery_url, gid="10", token="a", title="Feedback Payload")])
        record_feedback(conn, gallery_url, vote=1)
        record_feedback(conn, gallery_url, score=4)

        payload = feedback_history_payload(conn, gallery_url)

        self.assertEqual(payload["gallery"]["title"], "Feedback Payload")
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["latest"]["score"], 4)
        self.assertEqual(payload["latest"]["vote"], 0.5)
        conn.close()

    def test_ensure_gallery_exists_raises_not_found_for_missing_feedback_target(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)

        with self.assertRaises(ApiError) as ctx:
            ensure_gallery_exists(conn, "https://exhentai.org/g/missing/token/")

        self.assertEqual(ctx.exception.status.value, 404)
        self.assertEqual(ctx.exception.message, "Gallery not found")
        conn.close()

    def test_select_detail_candidates_skips_already_enriched(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        enriched = Gallery(url="https://exhentai.org/g/1/a/", gid="1", token="a", title="Enriched")
        plain = Gallery(url="https://exhentai.org/g/2/b/", gid="2", token="b", title="Plain")
        second_plain = Gallery(url="https://exhentai.org/g/3/c/", gid="3", token="c", title="Second Plain")
        store_galleries(conn, [enriched], detail_fetched=True)
        store_galleries(conn, [plain, second_plain])

        selected = select_detail_candidates(conn, [enriched, plain, second_plain], remaining_limit=1)

        self.assertEqual([gallery.url for gallery in selected], [plain.url])
        conn.close()

    def test_select_detail_candidates_prefers_current_model_match(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        plain = Gallery(url="https://exhentai.org/g/4/a/", gid="4", token="a", title="Plain First")
        matched = Gallery(
            url="https://exhentai.org/g/5/b/",
            gid="5",
            token="b",
            title="Matched Later",
            tags=["artist:favored"],
        )
        store_galleries(conn, [plain, matched])
        upsert_bootstrap_tags(conn, [("artist:favored", 2.0)])

        selected = select_detail_candidates(conn, [plain, matched], remaining_limit=1)

        self.assertEqual([gallery.url for gallery in selected], [matched.url])
        conn.close()

    def test_select_recommendation_detail_candidates_uses_top_unenriched_recommendations(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        enriched = Gallery(
            url="https://exhentai.org/g/6/a/",
            gid="6",
            token="a",
            title="Already Enriched",
            tags=["artist:favored"],
        )
        matched = Gallery(
            url="https://exhentai.org/g/7/b/",
            gid="7",
            token="b",
            title="Matched Candidate",
            tags=["artist:favored"],
        )
        plain = Gallery(url="https://exhentai.org/g/8/c/", gid="8", token="c", title="Plain Candidate")
        store_galleries(conn, [enriched], detail_fetched=True)
        store_galleries(conn, [plain, matched])
        upsert_bootstrap_tags(conn, [("artist:favored", 2.0)])

        selected = select_recommendation_detail_candidates(conn, limit=2)

        self.assertEqual([gallery.url for gallery in selected], [matched.url, plain.url])
        conn.close()

    def test_enrich_feedback_gallery_skips_without_cookie(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/31/a/"
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url=gallery_url, gid="31", token="a", title="No Cookie")])

                with patch("exh_rec.app.fetch_gallery_detail") as fetch_detail:
                    result = enrich_feedback_gallery(gallery_url)

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "no cookie")
                fetch_detail.assert_not_called()

    def test_enrich_feedback_gallery_retrains_from_detail_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/32/a/"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="32", token="a", title="Needs Detail")])
                    record_feedback(conn, gallery_url, vote=1)

                detailed = Gallery(
                    url=gallery_url,
                    gid="32",
                    token="a",
                    title="Needs Detail",
                    tags=["artist:detailfav"],
                )
                with patch("exh_rec.app.fetch_gallery_detail", return_value=detailed) as fetch_detail:
                    result = enrich_feedback_gallery(gallery_url)

                with db.connect() as conn:
                    row = conn.execute("SELECT detail_fetched_at FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
                    learned = learned_query_tags(conn, limit=5)

                self.assertEqual(result["status"], "success")
                fetch_detail.assert_called_once()
                self.assertEqual(fetch_detail.call_args.kwargs["delay"], 0)
                self.assertIsNotNone(row["detail_fetched_at"])
                self.assertIn("artist:detailfav", learned)

    def test_fetch_and_store_retrains_after_detail_enrichment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/33/a/"
                gallery = Gallery(url=gallery_url, gid="33", token="a", title="Fetch Detail")
                detailed = Gallery(
                    url=gallery_url,
                    gid="33",
                    token="a",
                    title="Fetch Detail",
                    tags=["artist:fetchdetail"],
                )
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [gallery])
                    record_feedback(conn, gallery_url, vote=1)

                with patch("exh_rec.app.fetch_galleries", return_value=[gallery]), patch(
                    "exh_rec.app.fetch_gallery_detail", return_value=detailed
                ):
                    result = fetch_and_store()

                with db.connect() as conn:
                    learned = learned_query_tags(conn, limit=5)

                self.assertTrue(result["model_retrained"])
                self.assertIn("artist:fetchdetail", learned)

    def test_fetch_and_store_marks_empty_recent_fetch_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                with patch("exh_rec.app.fetch_galleries", return_value=[]):
                    result = fetch_and_store()

                with db.connect() as conn:
                    history = fetch_runs(conn, limit=1)

                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["fetched"], 0)
                self.assertIn("check the saved cookie or ExHentai access", result["errors"][0])
                self.assertEqual(history[0]["status"], "failed")
                self.assertEqual(history[0]["fetched_count"], 0)
                self.assertEqual(history[0]["errors"], result["errors"])

    def test_fetch_and_store_empty_manual_query_mentions_search_terms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                with patch("exh_rec.app.fetch_galleries", return_value=[]):
                    result = fetch_and_store(force_query="artist:no_results")

                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "failed")
                self.assertIn("search terms", result["errors"][0])

    def test_fetch_and_store_marks_running_run_failed_on_unexpected_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery = Gallery(url="https://exhentai.org/g/36/a/", gid="36", token="a", title="Unexpected Fetch")
                detailed = Gallery(
                    url=gallery.url,
                    gid="36",
                    token="a",
                    title="Unexpected Fetch",
                    tags=["artist:unexpectedfetch"],
                )
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                with patch("exh_rec.app.fetch_galleries", return_value=[gallery]), patch(
                    "exh_rec.app.fetch_gallery_detail", return_value=detailed
                ), patch("exh_rec.app.retrain_model", side_effect=RuntimeError("retrain exploded")):
                    with self.assertRaises(RuntimeError):
                        fetch_and_store()

                with db.connect() as conn:
                    history = fetch_runs(conn, limit=1)

                self.assertEqual(history[0]["status"], "failed")
                self.assertEqual(history[0]["fetched_count"], 1)
                self.assertEqual(history[0]["stored_count"], 1)
                self.assertEqual(history[0]["enriched_count"], 1)
                self.assertIn("internal: retrain exploded", history[0]["errors"])
                self.assertIsNotNone(history[0]["finished_at"])

    def test_enrich_recommendations_retrains_after_detail_enrichment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/34/a/"
                detailed = Gallery(
                    url=gallery_url,
                    gid="34",
                    token="a",
                    title="Enrich Detail",
                    tags=["artist:enrichdetail"],
                )
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="34", token="a", title="Enrich Detail")])
                    record_feedback(conn, gallery_url, vote=1)

                with patch("exh_rec.app.fetch_gallery_detail", return_value=detailed):
                    result = enrich_recommendations(include_rated=True, limit=1)

                with db.connect() as conn:
                    learned = learned_query_tags(conn, limit=5)

                self.assertTrue(result["model_retrained"])
                self.assertIn("artist:enrichdetail", learned)

    def test_enrich_recommendations_defaults_invalid_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/35/a/"
                detailed = Gallery(
                    url=gallery_url,
                    gid="35",
                    token="a",
                    title="Invalid Limit Detail",
                    tags=["artist:invalidlimit"],
                )
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "detail_fetch_limit", "1")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="35", token="a", title="Invalid Limit Detail")])

                with patch("exh_rec.app.fetch_gallery_detail", return_value=detailed) as fetch_detail:
                    result = enrich_recommendations(limit="bad")

                self.assertEqual(result["enriched"], 1)
                fetch_detail.assert_called_once()

    def test_enrich_recommendations_marks_running_run_failed_on_unexpected_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                with patch(
                    "exh_rec.app.select_recommendation_detail_candidates",
                    side_effect=RuntimeError("selection exploded"),
                ):
                    with self.assertRaises(RuntimeError):
                        enrich_recommendations(limit=1)

                with db.connect() as conn:
                    history = fetch_runs(conn, limit=1)

                self.assertEqual(history[0]["status"], "failed")
                self.assertEqual(history[0]["enriched_count"], 0)
                self.assertIn("internal: selection exploded", history[0]["errors"])
                self.assertIsNotNone(history[0]["finished_at"])

    def test_save_settings_clear_cookie_removes_access_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "last_access_check", '{"ok": true}')

                save_settings({"cookie_header": ""})
                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "ipb_member_id=123; ipb_pass_hash=abc")

                save_settings({"clear_cookie": True})
                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "")
                    self.assertIsNone(get_access_check(conn))

    def test_save_settings_parses_string_false_booleans(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                save_settings({"clear_cookie": "false", "auto_refresh": "false"})

                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "ipb_member_id=123; ipb_pass_hash=abc")
                    self.assertEqual(db.get_setting(conn, "auto_refresh", ""), "0")

    def test_save_settings_new_cookie_clears_stale_access_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "last_access_check", '{"ok": true, "message": "old check"}')

                save_settings({"cookie_header": "ipb_member_id=456; ipb_pass_hash=def"})

                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "ipb_member_id=456; ipb_pass_hash=def")
                    self.assertIsNone(get_access_check(conn))

    def test_save_settings_rejects_malformed_cookie_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "last_access_check", '{"ok": true, "message": "old check"}')

                with self.assertRaises(ApiError) as ctx:
                    save_settings({"cookie_header": "this is not a cookie"})

                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "ipb_member_id=123; ipb_pass_hash=abc")
                    self.assertIsNotNone(get_access_check(conn))
                self.assertEqual(ctx.exception.status.value, 400)
                self.assertEqual(ctx.exception.message, "Cookie input must contain name=value pairs")

    def test_save_settings_clamps_recommend_candidate_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()

                save_settings({"recommend_candidate_limit": 5})
                with db.connect() as conn:
                    self.assertEqual(recommend_candidate_limit(conn), 100)

                save_settings({"recommend_candidate_limit": 50000})
                with db.connect() as conn:
                    self.assertEqual(recommend_candidate_limit(conn), 10000)

    def test_save_settings_defaults_invalid_numeric_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()

                save_settings(
                    {
                        "refresh_interval_minutes": None,
                        "fetch_pages": "",
                        "detail_fetch_limit": "bad",
                        "learned_query_limit": None,
                        "recommend_candidate_limit": "bad",
                    }
                )

                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "refresh_interval_minutes", ""), "30")
                    self.assertEqual(db.get_setting(conn, "fetch_pages", ""), "1")
                    self.assertEqual(db.get_setting(conn, "detail_fetch_limit", ""), "8")
                    self.assertEqual(db.get_setting(conn, "learned_query_limit", ""), "6")
                    self.assertEqual(recommend_candidate_limit(conn), 2000)

    def test_get_settings_defaults_corrupt_numeric_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "refresh_interval_minutes", "bad")
                    db.set_setting(conn, "fetch_pages", "bad")
                    db.set_setting(conn, "detail_fetch_limit", "bad")
                    db.set_setting(conn, "learned_query_limit", "bad")
                    db.set_setting(conn, "recommend_candidate_limit", "bad")

                settings = get_settings()

                self.assertEqual(settings["refresh_interval_minutes"], 30)
                self.assertEqual(settings["fetch_pages"], 1)
                self.assertEqual(settings["detail_fetch_limit"], 8)
                self.assertEqual(settings["learned_query_limit"], 6)
                self.assertEqual(settings["recommend_candidate_limit"], 2000)

    def test_save_settings_wakes_background_refresh_after_cookie_added(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                stop = threading.Event()
                called = threading.Event()
                REFRESH_WAKE.clear()

                def fake_fetch(*, trigger="manual", **_kwargs):
                    if trigger == "background":
                        called.set()
                        stop.set()
                    return {"ok": True}

                with patch("exh_rec.app.fetch_and_store", side_effect=fake_fetch):
                    worker = threading.Thread(target=background_refresh, args=(stop,), daemon=True)
                    worker.start()
                    time.sleep(0.05)
                    save_settings({"cookie_header": "ipb_member_id=123; ipb_pass_hash=abc", "auto_refresh": True})

                    self.assertTrue(called.wait(2))
                    stop.set()
                    REFRESH_WAKE.set()
                    worker.join(2)
                    REFRESH_WAKE.clear()

    def test_import_preferences_payload_rejects_unknown_schema_as_bad_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()

                with self.assertRaises(ApiError) as ctx:
                    import_preferences_payload({"schema": "unknown"})

                self.assertEqual(ctx.exception.status.value, 400)
                self.assertEqual(ctx.exception.message, "Unsupported preference export schema")

    def test_import_preferences_payload_returns_model_for_valid_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                payload = {
                    "schema": "exh-rec-preferences-v1",
                    "bootstrap_tags": [{"tag": "artist:payload", "weight": 2.0}],
                    "galleries": [],
                    "feedback": [],
                }

                result = import_preferences_payload(payload)

                self.assertTrue(result["ok"])
                self.assertEqual(result["imported"]["bootstrap_tags"], 1)
                self.assertIn("model", result)


if __name__ == "__main__":
    unittest.main()
