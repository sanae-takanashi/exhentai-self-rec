import io
import unittest
import sqlite3
import threading
import time
import tempfile
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from exh_rec import db
from exh_rec.app import (
    ApiError,
    FETCH_LOCK,
    FETCH_STATE,
    REFRESH_STATE,
    REFRESH_WAKE,
    Handler,
    background_refresh,
    build_queries,
    build_query_plan,
    cached_thumbnail,
    cached_gallery_sample,
    check_saved_access,
    collect_gallery_samples,
    configured_dinov2_device,
    configured_language_filter,
    configured_model_mode,
    configured_review_require_bootstrap_match,
    configured_visual_encoder,
    enrich_feedback_gallery,
    enrich_recommendations,
    ensure_gallery_exists,
    gallery_sample_entry,
    render_sample_entry,
    SpriteCropUnavailable,
    fetch_and_store,
    feedback_enrichment_plan,
    feedback_history_payload,
    feedback_update_summary,
    finish_interrupted_fetch_runs,
    fetch_runs,
    format_generated_query,
    get_access_check,
    get_settings,
    import_preferences_payload,
    is_allowed_thumbnail_url,
    is_remote_search_preference,
    marked_gallery_payload,
    mark_update_summary,
    missing_common_cookie_keys,
    model_snapshot,
    model_signature,
    network_proxy,
    parse_bool,
    parse_feedback_request,
    parse_mark_kind,
    query_float,
    query_int,
    reaction_history_payload,
    recommend_candidate_limit,
    recommendation_payload,
    refresh_summary,
    refresh_thumbnails,
    reset_library_payload,
    sample_count_for,
    save_visual_embedding_payload,
    save_settings,
    select_detail_candidates,
    select_recommendation_detail_candidates,
    server_display_url,
    short_repeat_payload,
    thumbnail_referer,
)
from exh_rec.exhentai import Gallery
from exh_rec.recommender import (
    learned_query_tags,
    parse_bootstrap_tags,
    record_feedback,
    record_gallery_mark,
    store_galleries,
    store_gallery_samples,
    upsert_bootstrap_tags,
)
from exh_rec.visual import DINOV2_VISUAL_VERSION, SIMPLE_VISUAL_VERSION


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

    def test_query_float_defaults_invalid_values_and_clamps_bounds(self):
        self.assertEqual(query_float({"freshness_weight": ["bad"]}, "freshness_weight", default=1.0, lower=0.0, upper=10.0), 1.0)
        self.assertEqual(query_float({"freshness_weight": ["50"]}, "freshness_weight", default=1.0, lower=0.0, upper=10.0), 10.0)
        self.assertEqual(query_float({"freshness_weight": ["-5"]}, "freshness_weight", default=1.0, lower=0.0, upper=10.0), 0.0)
        self.assertEqual(query_float({}, "freshness_weight", default=1.0, lower=0.0, upper=10.0), 1.0)

    def test_server_display_url_uses_loopback_for_wildcard_bind(self):
        self.assertEqual(server_display_url("0.0.0.0", 8787), "http://127.0.0.1:8787")
        self.assertEqual(server_display_url("192.0.2.10", 8787), "http://192.0.2.10:8787")

    def test_thumbnail_referer_accepts_only_exhentai_gallery_urls(self):
        self.assertEqual(
            thumbnail_referer("https://exhentai.org/g/123/abcdef/"),
            "https://exhentai.org/g/123/abcdef/",
        )
        self.assertEqual(thumbnail_referer("https://example.test/g/123/abcdef/"), "https://exhentai.org/")

    def test_cached_thumbnail_fetches_with_cookie_referer_and_reuses_cache(self):
        class Headers:
            def get_content_type(self):
                return "image/webp"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, size=-1):
                return b"thumb-bytes"

        calls = []

        def fake_open_url(request, timeout, proxy_url=""):
            calls.append(request)
            return Response()

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc; igneous=secret")

                with patch("exh_rec.net.open_url", fake_open_url):
                    first = cached_thumbnail(
                        "https://s.exhentai.org/w/01/913/40046-dq9gs0zn.webp",
                        "https://exhentai.org/g/123/abcdef/",
                    )
                    second = cached_thumbnail(
                        "https://s.exhentai.org/w/01/913/40046-dq9gs0zn.webp",
                        "https://exhentai.org/g/123/abcdef/",
                    )

        self.assertEqual(first, (b"thumb-bytes", "image/webp"))
        self.assertEqual(second, (b"thumb-bytes", "image/webp"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].full_url, "https://s.exhentai.org/w/01/913/40046-dq9gs0zn.webp")
        self.assertEqual(calls[0].get_header("Cookie"), "ipb_member_id=123; ipb_pass_hash=abc; igneous=secret")
        self.assertEqual(calls[0].get_header("Referer"), "https://exhentai.org/g/123/abcdef/")

    def test_cached_thumbnail_uses_configured_proxy(self):
        class Headers:
            def get_content_type(self):
                return "image/webp"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, size=-1):
                return b"thumb-bytes"

        calls = []

        def fake_open_url(request, timeout, proxy_url=""):
            calls.append(proxy_url)
            return Response()

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc; igneous=secret")
                    db.set_setting(conn, "network_proxy", "socks5://127.0.0.1:1080")

                with patch("exh_rec.net.open_url", fake_open_url):
                    cached_thumbnail(
                        "https://s.exhentai.org/w/01/913/40046-dq9gs0zn.webp",
                        "https://exhentai.org/g/123/abcdef/",
                    )

        self.assertEqual(calls, ["socks5://127.0.0.1:1080"])

    def test_cached_thumbnail_rejects_unsupported_hosts(self):
        with self.assertRaises(ApiError) as ctx:
            cached_thumbnail("https://example.test/thumb.webp")

        self.assertEqual(ctx.exception.status.value, 400)
        self.assertEqual(ctx.exception.message, "unsupported thumbnail host")

    def test_cached_gallery_sample_uses_stored_sample_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/43/a/"
                sample_url = "https://s.exhentai.org/t/sample-1.webp"
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url=gallery_url, gid="43", token="a", title="Sample Index")])
                    store_gallery_samples(conn, gallery_url, 20, ["https://s.exhentai.org/t/sample-0.webp", sample_url])

                with patch("exh_rec.app.cached_thumbnail", return_value=(b"sample", "image/webp")) as cached:
                    result = cached_gallery_sample(gallery_url, 1)

        self.assertEqual(result, (b"sample", "image/webp"))
        cached.assert_called_once_with(sample_url, gallery_url)

    def test_cached_gallery_sample_refreshes_stale_sample_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/44/a/"
                stale_url = "https://old.hath.network/c2/stale.webp"
                fresh_url = "https://new.hath.network/c2/fresh.webp"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="44", token="a", title="Stale Sample")])
                    store_gallery_samples(conn, gallery_url, 20, [stale_url])

                detailed = Gallery(
                    url=gallery_url,
                    gid="44",
                    token="a",
                    title="Stale Sample",
                    page_count=20,
                    sample_thumbs=[
                        fresh_url,
                        "https://new.hath.network/c2/fresh-1.webp",
                        "https://new.hath.network/c2/fresh-2.webp",
                        "https://new.hath.network/c2/fresh-3.webp",
                        "https://new.hath.network/c2/fresh-4.webp",
                    ],
                )
                stale_error = ApiError(HTTPStatus.BAD_GATEWAY, "Thumbnail fetch failed with HTTP 404")
                with patch("exh_rec.app.cached_thumbnail", side_effect=[stale_error, (b"fresh", "image/webp")]) as cached, patch(
                    "exh_rec.app.fetch_gallery_detail",
                    return_value=detailed,
                ) as fetch_detail, patch("exh_rec.app.cache_sample_thumbnails") as cache_samples, patch(
                    "exh_rec.app.fetch_gallery_metadata", return_value={}
                ):
                    result = cached_gallery_sample(gallery_url, 0)

                with db.connect() as conn:
                    row = conn.execute("SELECT samples_json FROM galleries WHERE url = ?", (gallery_url,)).fetchone()

        self.assertEqual(result, (b"fresh", "image/webp"))
        self.assertEqual(cached.call_args_list[-1].args, (fresh_url, gallery_url))
        fetch_detail.assert_called_once()
        cache_samples.assert_called_once()
        self.assertIn(fresh_url, row["samples_json"])

    def test_thumbnail_proxy_allows_hath_sample_hosts(self):
        self.assertTrue(is_allowed_thumbnail_url("https://abc123.hath.network/c2/a/1.webp"))
        self.assertFalse(is_allowed_thumbnail_url("https://hath.network/c2/a/1.webp"))

    def test_save_visual_embedding_payload_stores_gallery_vector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/40/a/"
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url=gallery_url, gid="40", token="a", title="Visual Save")])

                result = save_visual_embedding_payload(
                    {
                        "gallery_url": gallery_url,
                        "version": "canvas-rgb-8x8-v1",
                        "embedding": [1, 0, 0, 0] * 16,
                    }
                )

                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT visual_embedding_json, visual_embedding_version, visual_embedding_at FROM galleries WHERE url = ?",
                        (gallery_url,),
                    ).fetchone()

        self.assertTrue(result["visual_ready"])
        self.assertIsNotNone(row["visual_embedding_json"])
        self.assertEqual(row["visual_embedding_version"], SIMPLE_VISUAL_VERSION)
        self.assertIsNotNone(row["visual_embedding_at"])

    def test_save_visual_embedding_payload_defaults_to_dinov2_for_image_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/41/a/"
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url=gallery_url, gid="41", token="a", title="DINO Save")])

                with patch("exh_rec.app.dinov2_dependency_status", return_value={"available": True}), patch(
                    "exh_rec.app.cached_thumbnail",
                    return_value=(b"image", "image/webp"),
                ) as cached, patch(
                    "exh_rec.app.dinov2_embedding",
                    return_value=[1, 0, 0, 0] * 16,
                ):
                    result = save_visual_embedding_payload(
                        {
                            "gallery_url": gallery_url,
                            "image_urls": ["https://s.exhentai.org/t/1.webp"],
                        }
                    )

                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT visual_embedding_version FROM galleries WHERE url = ?",
                        (gallery_url,),
                    ).fetchone()

        self.assertTrue(result["visual_ready"])
        self.assertEqual(result["encoder"], "dinov2")
        self.assertEqual(row["visual_embedding_version"], DINOV2_VISUAL_VERSION)
        cached.assert_called_once()

    def test_save_visual_embedding_payload_reports_simple_fallback_when_dinov2_unavailable(self):
        with patch(
            "exh_rec.app.dinov2_dependency_status",
            return_value={"available": False, "error": "missing torch"},
        ), patch("exh_rec.app.cached_thumbnail") as cached:
            result = save_visual_embedding_payload(
                {
                    "gallery_url": "https://exhentai.org/g/42/a/",
                    "encoder": "dinov2",
                    "image_urls": ["https://s.exhentai.org/t/1.webp"],
                }
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["fallback_required"])
        self.assertEqual(result["fallback_encoder"], "simple")
        cached.assert_not_called()

    def test_save_visual_embedding_payload_skips_dinov2_when_encoder_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "visual_encoder", "simple")

                with patch("exh_rec.app.dinov2_dependency_status") as status, patch("exh_rec.app.cached_thumbnail") as cached:
                    result = save_visual_embedding_payload(
                        {
                            "gallery_url": "https://exhentai.org/g/42s/a/",
                            "encoder": "dinov2",
                            "image_urls": ["https://s.exhentai.org/t/1.webp"],
                        }
                    )

        self.assertFalse(result["ok"])
        self.assertTrue(result["fallback_required"])
        self.assertEqual(result["fallback_encoder"], "simple")
        self.assertIn("disabled", result["reason"])
        status.assert_not_called()
        cached.assert_not_called()

    def test_save_visual_embedding_payload_rejects_bad_embedding(self):
        with self.assertRaises(ApiError) as ctx:
            save_visual_embedding_payload(
                {
                    "gallery_url": "https://exhentai.org/g/missing/a/",
                    "embedding": ["bad"],
                }
            )

        self.assertEqual(ctx.exception.status.value, 400)

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

    def test_parse_mark_kind_validates_known_bookmark_types(self):
        self.assertEqual(parse_mark_kind("favorite"), "favorite")
        self.assertEqual(parse_mark_kind("ban"), "ban")

        with self.assertRaises(ApiError) as ctx:
            parse_mark_kind("other")
        self.assertEqual(ctx.exception.status.value, 400)
        self.assertEqual(ctx.exception.message, "kind must be favorite or ban")

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

    def test_missing_common_cookie_keys_reports_names_only(self):
        missing = missing_common_cookie_keys("ipb_member_id=123; sk=secret")

        self.assertEqual(missing, ["ipb_pass_hash", "igneous"])

    def test_check_api_returns_failed_access_message_as_json_result(self):
        sent = []
        handler = Handler.__new__(Handler)
        handler.path = "/api/check"
        handler.send_json = lambda payload, status=HTTPStatus.OK: sent.append((payload, status))
        handler.handle_error = lambda exc: (_ for _ in ()).throw(exc)
        result = {"ok": False, "gallery_count": 0, "message": "Cookie did not expose gallery listings"}

        with patch("exh_rec.app.check_saved_access", return_value=result):
            handler.do_POST()

        self.assertEqual(sent, [(result, HTTPStatus.OK)])

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

    def test_finish_interrupted_fetch_runs_marks_running_rows_failed(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        conn.execute(
            """
            INSERT INTO fetch_runs(trigger, status, queries_json)
            VALUES (?, ?, ?)
            """,
            ("background", "running", "[null]"),
        )

        finish_interrupted_fetch_runs(conn)
        row = fetch_runs(conn)[0]

        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["errors"], ["interrupted before completion"])
        self.assertIsNotNone(row["finished_at"])
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

    def test_recommendation_payload_uses_first_sample_as_thumbnail_fallback(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        gallery_url = "https://exhentai.org/g/9s/a/"
        first_sample = "https://s.exhentai.org/t/first.jpg"
        store_galleries(conn, [Gallery(url=gallery_url, gid="9s", token="a", title="Sample Fallback")])
        store_gallery_samples(conn, gallery_url, 40, [first_sample, "https://s.exhentai.org/t/second.jpg"])

        payload = recommendation_payload(conn, limit=1)

        self.assertEqual(payload["items"][0]["thumb_url"], first_sample)
        self.assertEqual(payload["items"][0]["samples"][0], first_sample)
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
        self.assertEqual(payload["latest"]["vote"], 0.75)
        conn.close()

    def test_feedback_update_summary_reports_retrain_effects(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        gallery_url = "https://exhentai.org/g/10u/a/"
        store_galleries(conn, [Gallery(url=gallery_url, gid="10u", token="a", title="Feedback Update", tags=["artist:update"])])
        before_model = model_snapshot(conn)
        before_signature = model_signature(conn)

        record_feedback(conn, gallery_url, vote=1)
        after_model = model_snapshot(conn)
        summary = feedback_update_summary(
            conn,
            action="record",
            gallery_url=gallery_url,
            vote=1,
            score=None,
            before_model=before_model,
            before_signature=before_signature,
            after_model=after_model,
            after_signature=model_signature(conn),
            elapsed_ms=12.34,
        )

        self.assertTrue(summary["retrained"])
        self.assertTrue(summary["model_changed"])
        self.assertEqual(summary["elapsed_ms"], 12.34)
        self.assertEqual(summary["signal"], 1.0)
        self.assertEqual(summary["feedback_events_before"], 0)
        self.assertEqual(summary["feedback_events_after"], 1)
        self.assertEqual(summary["rated_galleries_after"], 1)
        self.assertIsNotNone(summary["latest_feedback_id"])
        conn.close()

    def test_feedback_update_summary_can_report_no_retrain_for_neutral_skip(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        gallery_url = "https://exhentai.org/g/10n/a/"
        store_galleries(conn, [Gallery(url=gallery_url, gid="10n", token="a", title="Neutral Skip")])
        before_model = model_snapshot(conn)
        before_signature = model_signature(conn)

        record_feedback(conn, gallery_url, score=3)
        after_model = model_snapshot(conn)
        after_signature = model_signature(conn)
        summary = feedback_update_summary(
            conn,
            action="record",
            gallery_url=gallery_url,
            vote=None,
            score=3,
            before_model=before_model,
            before_signature=before_signature,
            after_model=after_model,
            after_signature=after_signature,
            retrained=False,
            elapsed_ms=1.23,
        )

        self.assertFalse(summary["retrained"])
        self.assertFalse(summary["model_changed"])
        self.assertEqual(summary["signal"], 0.0)
        self.assertEqual(summary["feedback_events_after"], 1)
        self.assertEqual(summary["rated_galleries_after"], 1)
        conn.close()

    def test_feedback_enrichment_plan_defers_remote_fetch_by_default(self):
        with patch("exh_rec.app.enrich_feedback_gallery", side_effect=AssertionError("must not fetch during feedback")):
            result = feedback_enrichment_plan(1.0, {"gallery_url": "https://exhentai.org/g/10/a/"})

        self.assertEqual(result["status"], "deferred")

    def test_feedback_enrichment_plan_can_opt_in_to_remote_fetch(self):
        with patch("exh_rec.app.enrich_feedback_gallery", return_value={"status": "success"}) as enrich:
            result = feedback_enrichment_plan(
                1.0,
                {"gallery_url": "https://exhentai.org/g/10/a/", "enrich_feedback": True},
            )

        self.assertEqual(result["status"], "success")
        enrich.assert_called_once_with("https://exhentai.org/g/10/a/")

    def test_reaction_history_payload_returns_reacted_gallery_cards(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        old_url = "https://exhentai.org/g/10h/a/"
        new_url = "https://exhentai.org/g/10i/a/"
        sample_url = "https://s.exhentai.org/t/history.jpg"
        store_galleries(
            conn,
            [
                Gallery(url=old_url, gid="10h", token="a", title="Old Feedback"),
                Gallery(url=new_url, gid="10i", token="a", title="New Feedback"),
            ],
        )
        store_gallery_samples(conn, new_url, 12, [sample_url])
        record_feedback(conn, old_url, vote=-1)
        record_feedback(conn, new_url, score=5)

        payload = reaction_history_payload(conn, limit=10)

        self.assertEqual(payload["total"], 2)
        self.assertEqual([item["url"] for item in payload["items"]], [new_url, old_url])
        self.assertEqual(payload["items"][0]["thumb_url"], sample_url)
        self.assertEqual(payload["items"][0]["user_score"], 5)
        self.assertTrue(payload["items"][0]["rated"])
        conn.close()

    def test_short_repeat_payload_returns_related_old_feedback(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        old_url = "https://exhentai.org/g/10pixivold/a/"
        repeat_url = "https://exhentai.org/g/10pixivnew/a/"
        sample_url = "https://s.exhentai.org/t/repeat.jpg"
        store_galleries(
            conn,
            [
                Gallery(
                    url=old_url,
                    gid="10pixivold",
                    token="a",
                    title="[Pixiv] Payload Artist May",
                    tags=["artist:payload artist"],
                ),
                Gallery(
                    url=repeat_url,
                    gid="10pixivnew",
                    token="a",
                    title="[Pixiv] Payload Artist June",
                    tags=["artist:payload artist"],
                ),
            ],
        )
        store_gallery_samples(conn, old_url, 60, [])
        store_gallery_samples(conn, repeat_url, 4, [sample_url])
        record_feedback(conn, old_url, score=2)

        payload = short_repeat_payload(conn, limit=10)

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["url"], repeat_url)
        self.assertEqual(payload["items"][0]["thumb_url"], sample_url)
        self.assertEqual(payload["items"][0]["related_feedback"][0]["url"], old_url)
        self.assertEqual(payload["items"][0]["related_feedback"][0]["user_score"], 2)
        conn.close()

    def test_marked_gallery_payload_returns_bookmark_cards(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        favorite_url = "https://exhentai.org/g/10fav/a/"
        ban_url = "https://exhentai.org/g/10ban/a/"
        store_galleries(
            conn,
            [
                Gallery(url=favorite_url, gid="10fav", token="a", title="Favorite Payload"),
                Gallery(url=ban_url, gid="10ban", token="a", title="Ban Payload"),
            ],
        )
        before_model = model_snapshot(conn)
        before_signature = model_signature(conn)

        record_gallery_mark(conn, favorite_url, "favorite")
        record_gallery_mark(conn, ban_url, "ban")
        after_model = model_snapshot(conn)
        after_signature = model_signature(conn)
        payload = marked_gallery_payload(conn, kind="favorite", limit=10)
        summary = mark_update_summary(
            conn,
            action="record",
            gallery_url=favorite_url,
            kind="favorite",
            before_model=before_model,
            before_signature=before_signature,
            after_model=after_model,
            after_signature=after_signature,
        )

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["url"], favorite_url)
        self.assertEqual(payload["items"][0]["user_mark_kind"], "favorite")
        self.assertTrue(payload["items"][0]["marked"])
        self.assertEqual(summary["favorite_galleries_after"], 1)
        self.assertEqual(summary["banned_galleries_after"], 1)
        self.assertTrue(summary["model_changed"])
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
        enriched = Gallery(
            url="https://exhentai.org/g/1/a/",
            gid="1",
            token="a",
            title="Enriched",
            thumb_url="https://s.exhentai.org/t/1.jpg",
        )
        plain = Gallery(url="https://exhentai.org/g/2/b/", gid="2", token="b", title="Plain")
        second_plain = Gallery(url="https://exhentai.org/g/3/c/", gid="3", token="c", title="Second Plain")
        store_galleries(conn, [enriched], detail_fetched=True)
        store_galleries(conn, [plain, second_plain])

        selected = select_detail_candidates(conn, [enriched, plain, second_plain], remaining_limit=1)

        self.assertEqual([gallery.url for gallery in selected], [plain.url])
        conn.close()

    def test_select_detail_candidates_retries_enriched_gallery_missing_images(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        missing_images = Gallery(url="https://exhentai.org/g/1m/a/", gid="1m", token="a", title="Missing Images")
        plain = Gallery(url="https://exhentai.org/g/2m/b/", gid="2m", token="b", title="Plain")
        store_galleries(conn, [missing_images], detail_fetched=True)
        store_galleries(conn, [plain])

        selected = select_detail_candidates(conn, [missing_images, plain], remaining_limit=1)

        self.assertEqual([gallery.url for gallery in selected], [missing_images.url])
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
        enriched.thumb_url = "https://s.exhentai.org/t/6.jpg"
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

    def test_refresh_thumbnails_backfills_missing_cover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/70/a/"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="70", token="a", title="No Cover")])

                detailed = Gallery(
                    url=gallery_url,
                    gid="70",
                    token="a",
                    title="No Cover",
                    thumb_url="https://s.exhentai.org/t/aa/refreshed-cover.jpg",
                )
                with patch("exh_rec.app.fetch_gallery_metadata", return_value={}), patch(
                    "exh_rec.app.fetch_gallery_detail", return_value=detailed
                ) as fetch_detail:
                    result = refresh_thumbnails([gallery_url])

                with db.connect() as conn:
                    row = conn.execute("SELECT thumb_url, detail_fetched_at FROM galleries WHERE url = ?", (gallery_url,)).fetchone()

                self.assertTrue(result["ok"])
                self.assertEqual(result["updated"], 1)
                fetch_detail.assert_called_once()
                self.assertEqual(fetch_detail.call_args.kwargs["delay"], 0)
                self.assertEqual(row["thumb_url"], "https://s.exhentai.org/t/aa/refreshed-cover.jpg")
                # A thumbnail refresh must not mark the gallery as fully enriched.
                self.assertIsNone(row["detail_fetched_at"])

    def test_refresh_thumbnails_uses_gdata_cover_without_html_scrape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/700/a/"
                ehgt_cover = "https://ehgt.org/aa/bb/700-cover.jpg"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="700", token="a", title="API Cover")])

                metadata = {gallery_url: {"thumb": ehgt_cover}}
                with patch("exh_rec.app.fetch_gallery_metadata", return_value=metadata), patch(
                    "exh_rec.app.fetch_gallery_detail"
                ) as fetch_detail:
                    result = refresh_thumbnails([gallery_url])

                with db.connect() as conn:
                    row = conn.execute("SELECT thumb_url FROM galleries WHERE url = ?", (gallery_url,)).fetchone()

                self.assertTrue(result["ok"])
                self.assertEqual(result["updated"], 1)
                # The gdata cover is authoritative; no HTML scrape should happen.
                fetch_detail.assert_not_called()
                self.assertEqual(row["thumb_url"], ehgt_cover)

    def test_refresh_thumbnails_falls_back_to_html_when_api_lacks_cover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/701/a/"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="701", token="a", title="HTML Cover")])

                detailed = Gallery(url=gallery_url, gid="701", token="a", title="HTML Cover", thumb_url="https://s.exhentai.org/t/aa/html.jpg")
                with patch("exh_rec.app.fetch_gallery_metadata", return_value={}), patch(
                    "exh_rec.app.fetch_gallery_detail", return_value=detailed
                ) as fetch_detail:
                    result = refresh_thumbnails([gallery_url])

                with db.connect() as conn:
                    row = conn.execute("SELECT thumb_url FROM galleries WHERE url = ?", (gallery_url,)).fetchone()

                self.assertTrue(result["ok"])
                fetch_detail.assert_called_once()
                self.assertEqual(row["thumb_url"], "https://s.exhentai.org/t/aa/html.jpg")

    def test_refresh_thumbnails_reports_per_gallery_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/71/a/"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    store_galleries(conn, [Gallery(url=gallery_url, gid="71", token="a", title="Breaks")])

                with patch("exh_rec.app.fetch_gallery_metadata", return_value={}), patch(
                    "exh_rec.app.fetch_gallery_detail", side_effect=RuntimeError("boom")
                ):
                    result = refresh_thumbnails([gallery_url])

                self.assertFalse(result["ok"])
                self.assertEqual(result["updated"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIn("boom", result["errors"][0])

    def test_refresh_thumbnails_requires_cookie(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url="https://exhentai.org/g/72/a/", gid="72", token="a", title="No Cookie")])

                with patch("exh_rec.app.fetch_gallery_detail") as fetch_detail:
                    with self.assertRaises(ApiError) as ctx:
                        refresh_thumbnails(["https://exhentai.org/g/72/a/"])

                self.assertEqual(ctx.exception.status, HTTPStatus.BAD_REQUEST)
                fetch_detail.assert_not_called()
                self.assertFalse(FETCH_LOCK.locked())

    def test_refresh_thumbnails_rejects_empty_request(self):
        with self.assertRaises(ApiError) as ctx:
            refresh_thumbnails([])
        self.assertEqual(ctx.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertFalse(FETCH_LOCK.locked())

    def test_is_allowed_thumbnail_url_accepts_ehgt(self):
        self.assertTrue(is_allowed_thumbnail_url("https://ehgt.org/aa/bb/cover.jpg"))
        self.assertTrue(is_allowed_thumbnail_url("https://s.exhentai.org/t/aa/x.jpg"))
        # Only https is allowed, and unknown hosts are rejected.
        self.assertFalse(is_allowed_thumbnail_url("http://ehgt.org/aa/bb/cover.jpg"))
        self.assertFalse(is_allowed_thumbnail_url("https://evil.test/cover.jpg"))

    def test_gallery_sample_entry_returns_string_or_sprite_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/900/a/"
                sprite = {"url": "https://s.exhentai.org/m/001/sheet.jpg", "x": 100, "y": 0, "w": 100, "h": 142}
                with db.connect() as conn:
                    store_galleries(conn, [Gallery(url=gallery_url, gid="900", token="a", title="Mixed")])
                    store_gallery_samples(conn, gallery_url, 20, ["https://s.exhentai.org/t/page-0.jpg", sprite])

                self.assertEqual(gallery_sample_entry(gallery_url, 0), "https://s.exhentai.org/t/page-0.jpg")
                self.assertEqual(gallery_sample_entry(gallery_url, 1), sprite)

    def test_render_sample_entry_crops_sprite_when_pillow_available(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        sheet = io.BytesIO()
        Image.new("RGB", (300, 142), "red").save(sheet, format="PNG")
        sheet_bytes = sheet.getvalue()
        sprite = {"url": "https://s.exhentai.org/m/001/sheet.jpg", "x": 100, "y": 0, "w": 100, "h": 142}
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with patch("exh_rec.app.cached_thumbnail", return_value=(sheet_bytes, "image/png")):
                    data, content_type = render_sample_entry(sprite, "https://exhentai.org/g/900/a/")

        self.assertEqual(content_type, "image/png")
        cropped = Image.open(io.BytesIO(data))
        self.assertEqual(cropped.size, (100, 142))

    def test_render_sample_entry_falls_back_to_full_sheet_without_pillow(self):
        sprite = {"url": "https://s.exhentai.org/m/001/sheet.jpg", "x": 100, "y": 0, "w": 100, "h": 142}
        with patch("exh_rec.app.cached_thumbnail", return_value=(b"sheet-bytes", "image/jpeg")), patch(
            "exh_rec.app.crop_sprite_bytes", side_effect=SpriteCropUnavailable("no pillow")
        ):
            data, content_type = render_sample_entry(sprite, "https://exhentai.org/g/900/a/")

        self.assertEqual((data, content_type), (b"sheet-bytes", "image/jpeg"))

    def test_sample_count_for_uses_five_plus_one_per_hundred_pages(self):
        self.assertEqual(sample_count_for(None), 5)
        self.assertEqual(sample_count_for(0), 5)
        self.assertEqual(sample_count_for(40), 5)
        self.assertEqual(sample_count_for(99), 5)
        self.assertEqual(sample_count_for(150), 6)
        self.assertEqual(sample_count_for(320), 8)

    def test_collect_gallery_samples_uses_first_page_without_extra_fetch(self):
        detailed = Gallery(
            url="https://exhentai.org/g/40/a/",
            gid="40",
            token="a",
            title="Small Gallery",
            page_count=30,
            sample_thumbs=[f"https://s.exhentai.org/t/{i}.jpg" for i in range(30)],
        )
        with patch("exh_rec.app.fetch_gallery_sample_pages") as extra:
            samples = collect_gallery_samples("cookie", detailed, extra_pages=2)
        extra.assert_not_called()
        self.assertEqual(len(samples), 5)
        self.assertTrue(set(samples).issubset(set(detailed.sample_thumbs)))

    def test_collect_gallery_samples_always_keeps_first_page_preview(self):
        detailed = Gallery(
            url="https://exhentai.org/g/40f/a/",
            gid="40f",
            token="a",
            title="First Page",
            page_count=40,
            sample_thumbs=[f"https://s.exhentai.org/t/{i}.jpg" for i in range(10)],
        )
        with patch("exh_rec.app.random.sample", return_value=detailed.sample_thumbs[5:9]):
            samples = collect_gallery_samples("cookie", detailed, extra_pages=0)

        self.assertEqual(samples[0], detailed.sample_thumbs[0])
        self.assertEqual(len(samples), 5)

    def test_collect_gallery_samples_fetches_extra_pages_for_large_galleries(self):
        detailed = Gallery(
            url="https://exhentai.org/g/41/a/",
            gid="41",
            token="a",
            title="Large Gallery",
            page_count=320,
            sample_thumbs=["https://s.exhentai.org/t/0.jpg", "https://s.exhentai.org/t/1.jpg", "https://s.exhentai.org/t/2.jpg"],
        )
        extra_thumbs = [f"https://s.exhentai.org/t/extra-{i}.jpg" for i in range(20)]
        with patch("exh_rec.app.fetch_gallery_sample_pages", return_value=extra_thumbs) as extra:
            samples = collect_gallery_samples("cookie", detailed, extra_pages=2)
        extra.assert_called_once()
        self.assertEqual(len(samples), 8)
        pool = set(detailed.sample_thumbs) | set(extra_thumbs)
        self.assertTrue(set(samples).issubset(pool))

    def test_fetch_and_store_stores_gallery_samples(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/42/a/"
                gallery = Gallery(url=gallery_url, gid="42", token="a", title="Sampled")
                detailed = Gallery(
                    url=gallery_url,
                    gid="42",
                    token="a",
                    title="Sampled",
                    tags=["artist:sampled"],
                    page_count=40,
                    sample_thumbs=[f"https://s.exhentai.org/t/{i}.jpg" for i in range(40)],
                )
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                with patch("exh_rec.app.fetch_galleries", return_value=[gallery]), patch(
                    "exh_rec.app.fetch_gallery_detail", return_value=detailed
                ), patch("exh_rec.app.fetch_gallery_sample_pages") as extra:
                    fetch_and_store()

                extra.assert_not_called()
                with db.connect() as conn:
                    payload = recommendation_payload(conn, include_rated=True)
                item = next(entry for entry in payload["items"] if entry["url"] == gallery_url)
                self.assertEqual(item["page_count"], 40)
                self.assertEqual(len(item["samples"]), 5)
                self.assertTrue(set(item["samples"]).issubset(set(detailed.sample_thumbs)))

    def test_reset_library_clears_data_but_keeps_cookie_and_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery_url = "https://exhentai.org/g/50/a/"
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    upsert_bootstrap_tags(conn, parse_bootstrap_tags("artist:keepme"))
                    store_galleries(conn, [Gallery(url=gallery_url, gid="50", token="a", title="Legacy")])
                    record_feedback(conn, gallery_url, vote=1)

                payload = reset_library_payload()

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["removed"]["galleries"], 1)
                self.assertEqual(payload["removed"]["feedback"], 1)
                self.assertEqual(payload["items"], [])
                with db.connect() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM feature_weights").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0], 0)
                    self.assertEqual(db.get_setting(conn, "cookie_header", ""), "ipb_member_id=123; ipb_pass_hash=abc")
                    tags = {row["tag"] for row in conn.execute("SELECT tag FROM bootstrap_tags")}
                self.assertIn("artist:keepme", tags)

    def test_reset_library_conflicts_while_fetch_running(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                self.assertTrue(FETCH_LOCK.acquire(blocking=False))
                try:
                    with self.assertRaises(ApiError) as ctx:
                        reset_library_payload()
                    self.assertEqual(ctx.exception.status, HTTPStatus.CONFLICT)
                finally:
                    FETCH_LOCK.release()

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

    def test_fetch_and_store_fetches_deeper_when_max_pages_are_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                old_gallery = Gallery(url="https://exhentai.org/g/80/a/", gid="80", token="a", title="Already Stored")
                new_gallery = Gallery(url="https://exhentai.org/g/81/a/", gid="81", token="a", title="Deep New")
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "fetch_pages", "5")
                    db.set_setting(conn, "stale_fetch_extra_pages", "5")
                    db.set_setting(conn, "detail_fetch_limit", "0")
                    store_galleries(conn, [old_gallery])

                def fake_fetch(_cookie, query=None, pages=1, start_page=0, **_kwargs):
                    if start_page == 0:
                        return [old_gallery]
                    if start_page == 5:
                        return [new_gallery]
                    return []

                with patch("exh_rec.app.fetch_galleries", side_effect=fake_fetch) as fetch:
                    result = fetch_and_store()

                self.assertEqual(result["stored"], 1)
                self.assertIn(new_gallery.url, [item["url"] for item in result["items"]])
                starts = [call.kwargs.get("start_page", 0) for call in fetch.call_args_list]
                self.assertEqual(starts[:2], [0, 5])

    def test_fetch_and_store_logs_detailed_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                gallery = Gallery(url="https://exhentai.org/g/82/a/", gid="82", token="a", title="Progress Item")
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "detail_fetch_limit", "0")

                with patch("exh_rec.app.fetch_galleries", return_value=[gallery]), patch(
                    "exh_rec.app.enrich_covers_via_api",
                    return_value=1,
                ), patch("builtins.print") as printed:
                    result = fetch_and_store()

                lines = [call.args[0] for call in printed.call_args_list]
                self.assertTrue(any(line.startswith("[fetch] fetch started") for line in lines))
                self.assertTrue(any(line.startswith("[fetch] query started") for line in lines))
                self.assertTrue(any(line.startswith("[fetch] page batch fetched") for line in lines))
                self.assertTrue(any(line.startswith("[fetch] fetch finished") for line in lines))
                self.assertEqual(result["status"], "success")
                self.assertFalse(FETCH_STATE["running"])
                self.assertEqual(FETCH_STATE["stage"], "finished")
                self.assertEqual(FETCH_STATE["fetched"], 1)
                self.assertEqual(FETCH_STATE["stored"], 1)

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

    def test_save_settings_stores_proxy_and_dinov2_device(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()

                save_settings(
                    {
                        "network_proxy": "127.0.0.1:7890",
                        "recommend_language_filter": "language:japanese, Chinese",
                        "recommend_model_mode": "visual",
                        "preview_freshness_weight": "14.5",
                        "preview_posted_after": "2026-06-01",
                        "review_require_bootstrap_match": False,
                        "visual_encoder": "simple",
                        "dinov2_device": "CUDA:0",
                    }
                )

                with db.connect() as conn:
                    self.assertEqual(network_proxy(conn), "http://127.0.0.1:7890")
                    self.assertEqual(configured_language_filter(conn), "chinese,japanese")
                    self.assertEqual(configured_model_mode(conn), "visual")
                    self.assertFalse(configured_review_require_bootstrap_match(conn))
                    self.assertEqual(configured_visual_encoder(conn), "simple")
                    self.assertEqual(configured_dinov2_device(conn), "cuda:0")
                settings = get_settings()
                self.assertEqual(settings["network_proxy_preview"], "http://127.0.0.1:7890")
                self.assertEqual(settings["recommend_language_filter"], "chinese,japanese")
                self.assertEqual(settings["recommend_model_mode"], "visual")
                self.assertEqual(settings["preview_freshness_weight"], 14.5)
                self.assertEqual(settings["preview_posted_after"], "2026-06-01")
                self.assertFalse(settings["review_require_bootstrap_match"])
                self.assertEqual(settings["visual_encoder"], "simple")
                self.assertEqual(settings["visual"]["default_encoder"], "simple")
                self.assertEqual(settings["dinov2_device"], "cuda:0")

    def test_save_settings_rejects_invalid_proxy_and_dinov2_device(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()

                with self.assertRaises(ApiError) as proxy_ctx:
                    save_settings({"network_proxy": "ftp://127.0.0.1:21"})
                with self.assertRaises(ApiError) as encoder_ctx:
                    save_settings({"visual_encoder": "vae"})
                with self.assertRaises(ApiError) as device_ctx:
                    save_settings({"dinov2_device": "gpu"})

                self.assertEqual(proxy_ctx.exception.status.value, 400)
                self.assertIn("Proxy must use", proxy_ctx.exception.message)
                self.assertEqual(encoder_ctx.exception.status.value, 400)
                self.assertIn("Visual encoder", encoder_ctx.exception.message)
                self.assertEqual(device_ctx.exception.status.value, 400)
                self.assertIn("DINOv2 device", device_ctx.exception.message)

    def test_check_saved_access_uses_configured_proxy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")
                    db.set_setting(conn, "network_proxy", "https://proxy.local:8443")

                with patch("exh_rec.app.check_access", return_value={"ok": True, "gallery_count": 1, "message": "ok"}) as check:
                    result = check_saved_access()

                self.assertTrue(result["ok"])
                check.assert_called_once_with("ipb_member_id=123; ipb_pass_hash=abc", proxy_url="https://proxy.local:8443")

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
                        "stale_fetch_extra_pages": "bad",
                        "detail_fetch_limit": "bad",
                        "learned_query_limit": None,
                        "request_interval_seconds": "bad",
                        "temporary_ban_pause_seconds": "bad",
                        "recommend_candidate_limit": "bad",
                        "preview_freshness_weight": "bad",
                        "preview_posted_after": "not-a-date",
                    }
                )

                with db.connect() as conn:
                    self.assertEqual(db.get_setting(conn, "refresh_interval_minutes", ""), "30")
                    self.assertEqual(db.get_setting(conn, "fetch_pages", ""), "1")
                    self.assertEqual(db.get_setting(conn, "stale_fetch_extra_pages", ""), "20")
                    self.assertEqual(db.get_setting(conn, "detail_fetch_limit", ""), "8")
                    self.assertEqual(db.get_setting(conn, "learned_query_limit", ""), "6")
                    self.assertEqual(db.get_setting(conn, "request_interval_seconds", ""), "3.0")
                    self.assertEqual(db.get_setting(conn, "temporary_ban_pause_seconds", ""), "90.0")
                    self.assertEqual(recommend_candidate_limit(conn), 2000)
                    self.assertEqual(db.get_setting(conn, "preview_freshness_weight", ""), "8.0")
                    self.assertEqual(db.get_setting(conn, "preview_posted_after", "missing"), "")

    def test_get_settings_defaults_corrupt_numeric_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "refresh_interval_minutes", "bad")
                    db.set_setting(conn, "fetch_pages", "bad")
                    db.set_setting(conn, "stale_fetch_extra_pages", "bad")
                    db.set_setting(conn, "detail_fetch_limit", "bad")
                    db.set_setting(conn, "learned_query_limit", "bad")
                    db.set_setting(conn, "request_interval_seconds", "bad")
                    db.set_setting(conn, "temporary_ban_pause_seconds", "bad")
                    db.set_setting(conn, "recommend_candidate_limit", "bad")
                    db.set_setting(conn, "preview_freshness_weight", "bad")
                    db.set_setting(conn, "preview_posted_after", "not-a-date")

                settings = get_settings()

                self.assertEqual(settings["refresh_interval_minutes"], 30)
                self.assertEqual(settings["fetch_pages"], 1)
                self.assertEqual(settings["stale_fetch_extra_pages"], 20)
                self.assertEqual(settings["detail_fetch_limit"], 8)
                self.assertEqual(settings["learned_query_limit"], 6)
                self.assertEqual(settings["request_interval_seconds"], 3.0)
                self.assertEqual(settings["temporary_ban_pause_seconds"], 90.0)
                self.assertEqual(settings["recommend_candidate_limit"], 2000)
                self.assertEqual(settings["preview_freshness_weight"], 8.0)
                self.assertEqual(settings["preview_posted_after"], "")

    def test_get_settings_reports_missing_common_cookie_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", data_dir / "test.sqlite3"):
                db.init_db()
                with db.connect() as conn:
                    db.set_setting(conn, "cookie_header", "ipb_member_id=123; ipb_pass_hash=abc")

                settings = get_settings()

                self.assertEqual(settings["cookie_missing_keys"], ["igneous"])

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
