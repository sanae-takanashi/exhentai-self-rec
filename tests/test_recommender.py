import sqlite3
import unittest

from exh_rec import db
from exh_rec.exhentai import Gallery
from exh_rec.recommender import (
    LEARNING_RATE,
    clear_feedback,
    bootstrap_search_text,
    export_preferences,
    feedback_history,
    feature_learning_multiplier,
    feedback_signal,
    get_bootstrap_tags,
    import_preferences,
    learned_query_tags,
    model_snapshot,
    parse_bootstrap_tags,
    parse_bootstrap_weight,
    recommend,
    recommend_page,
    record_feedback,
    retrain_model,
    store_galleries,
    upsert_bootstrap_tags,
)


class RecommenderTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_parse_bootstrap_tags(self):
        parsed = parse_bootstrap_tags("artist:a\n-bad_tag\nlanguage:english:2\nparody:1984\nparody:1984:2")
        self.assertEqual(
            parsed,
            [
                ("artist:a", 1.0),
                ("bad tag", -1.0),
                ("language:english", 2.0),
                ("parody:1984", 1.0),
                ("parody:1984", 2.0),
            ],
        )

    def test_parse_bootstrap_weight_preserves_known_namespace_numeric_values(self):
        self.assertEqual(parse_bootstrap_weight("parody:1984"), ("parody:1984", 1.0))
        self.assertEqual(parse_bootstrap_weight("parody:1984:2"), ("parody:1984", 2.0))
        self.assertEqual(parse_bootstrap_weight("plain term:-2"), ("plain term", -2.0))

    def test_bootstrap_underscore_input_matches_parsed_tag_values(self):
        gallery_url = "https://exhentai.org/g/1u/a/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="1u", token="a", title="Underscore Match", tags=["artist:test artist"])],
        )
        upsert_bootstrap_tags(self.conn, parse_bootstrap_tags("artist:test_artist:2"))

        item = recommend(self.conn, limit=1)[0]

        self.assertEqual(item["url"], gallery_url)
        self.assertIn("bootstrap artist:test artist +2", item["reasons"])

    def test_upsert_bootstrap_tags_normalizes_direct_values(self):
        upsert_bootstrap_tags(self.conn, [("artist:direct_value", 2.0)])

        tags = get_bootstrap_tags(self.conn)

        self.assertEqual(tags, [{"tag": "artist:direct value", "weight": 2.0}])

    def test_feedback_changes_ranking(self):
        store_galleries(
            self.conn,
            [
                Gallery(url="https://exhentai.org/g/1/a/", gid="1", token="a", title="Blue Artist One", tags=["artist:one"]),
                Gallery(url="https://exhentai.org/g/2/b/", gid="2", token="b", title="Red Artist Two", tags=["artist:two"]),
            ],
        )
        upsert_bootstrap_tags(self.conn, [("artist", 0.1)])
        record_feedback(self.conn, "https://exhentai.org/g/2/b/", 1)
        items = recommend(self.conn, include_rated=True)
        self.assertEqual(items[0]["url"], "https://exhentai.org/g/2/b/")

    def test_bootstrap_matches_category_and_uploader_metadata(self):
        category_url = "https://exhentai.org/g/19/a/"
        uploader_url = "https://exhentai.org/g/20/b/"
        plain_url = "https://exhentai.org/g/21/c/"
        store_galleries(
            self.conn,
            [
                Gallery(url=plain_url, gid="21", token="c", title="Plain"),
                Gallery(url=category_url, gid="19", token="a", title="Category Match", category="Manga"),
                Gallery(url=uploader_url, gid="20", token="b", title="Uploader Match", uploader="TrustedUploader"),
            ],
        )

        upsert_bootstrap_tags(self.conn, [("category:manga", 2.0), ("uploader:trusteduploader", 3.0)])
        items = recommend(self.conn)

        self.assertEqual(items[0]["url"], uploader_url)
        self.assertEqual(items[1]["url"], category_url)

    def test_namespaced_bootstrap_requires_exact_metadata_match(self):
        exact_url = "https://exhentai.org/g/22/a/"
        partial_url = "https://exhentai.org/g/23/b/"
        store_galleries(
            self.conn,
            [
                Gallery(url=partial_url, gid="23", token="b", title="Partial", tags=["artist:anna"]),
                Gallery(url=exact_url, gid="22", token="a", title="Exact", tags=["artist:ann"]),
            ],
        )
        upsert_bootstrap_tags(self.conn, [("artist:ann", 2.0)])

        items = recommend(self.conn)
        reasons_by_url = {item["url"]: item["reasons"] for item in items}

        self.assertEqual(items[0]["url"], exact_url)
        self.assertIn("bootstrap artist:ann +2", reasons_by_url[exact_url])
        self.assertNotIn("bootstrap artist:ann +2", reasons_by_url[partial_url])

    def test_plain_bootstrap_terms_still_match_title_text(self):
        store_galleries(
            self.conn,
            [
                Gallery(url="https://exhentai.org/g/24/a/", gid="24", token="a", title="Plain Term Match"),
            ],
        )
        upsert_bootstrap_tags(self.conn, [("term", 1.0)])

        item = recommend(self.conn)[0]

        self.assertIn("bootstrap term +1", item["reasons"])

    def test_plain_bootstrap_terms_do_not_match_inside_words(self):
        exact_url = "https://exhentai.org/g/25/a/"
        partial_url = "https://exhentai.org/g/26/b/"
        store_galleries(
            self.conn,
            [
                Gallery(url=partial_url, gid="26", token="b", title="Banana Archive"),
                Gallery(url=exact_url, gid="25", token="a", title="Ann Archive"),
            ],
        )
        upsert_bootstrap_tags(self.conn, [("ann", 2.0)])

        items = recommend(self.conn)
        reasons_by_url = {item["url"]: item["reasons"] for item in items}

        self.assertEqual(items[0]["url"], exact_url)
        self.assertIn("bootstrap ann +2", reasons_by_url[exact_url])
        self.assertNotIn("bootstrap ann +2", reasons_by_url[partial_url])

    def test_bootstrap_search_text_includes_metadata_forms(self):
        text = bootstrap_search_text(
            {
                "title": "Sample",
                "category": "Manga",
                "uploader": "TrustedUploader",
                "tags": ["artist:name"],
            }
        )

        self.assertIn("category:manga", text)
        self.assertIn("uploader:trusteduploader", text)
        self.assertIn("artist:name", text)

    def test_score_feedback_uses_scaled_signal(self):
        self.assertEqual(feedback_signal(score=1), -1.0)
        self.assertEqual(feedback_signal(score=3), 0.0)
        self.assertEqual(feedback_signal(score=5), 1.0)

        gallery_url = "https://exhentai.org/g/3/c/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="3", token="c", title="Green Artist Three", tags=["artist:three"])],
        )
        record_feedback(self.conn, gallery_url, score=4)
        row = self.conn.execute("SELECT vote, score FROM feedback WHERE gallery_url = ?", (gallery_url,)).fetchone()
        self.assertEqual(row["score"], 4)
        self.assertEqual(row["vote"], 0.5)

    def test_store_galleries_marks_detail_fetch(self):
        gallery_url = "https://exhentai.org/g/4/d/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="4", token="d", title="Detail Marked", tags=["artist:marked"])],
            detail_fetched=True,
        )
        row = self.conn.execute("SELECT detail_fetched_at FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        self.assertIsNotNone(row["detail_fetched_at"])

    def test_store_galleries_counts_only_new_urls(self):
        gallery_url = "https://exhentai.org/g/4b/d/"
        first_count = store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="4b", token="d", title="First Title")],
        )
        second_count = store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="4b", token="d", title="Updated Title", tags=["artist:updated"])],
        )

        row = self.conn.execute("SELECT title, tags_json FROM galleries WHERE url = ?", (gallery_url,)).fetchone()

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(row["title"], "Updated Title")
        self.assertIn("artist:updated", row["tags_json"])

    def test_positive_feedback_creates_learned_query_tags(self):
        gallery_url = "https://exhentai.org/g/5/e/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="5", token="e", title="Learned Tags", tags=["artist:learned", "female:good"])],
        )
        record_feedback(self.conn, gallery_url, vote=1)
        queries = learned_query_tags(self.conn, limit=5)
        self.assertIn("artist:learned", queries)
        self.assertIn("female:good", queries)

    def test_feature_learning_multiplier_prioritizes_specific_identity_features(self):
        self.assertGreater(feature_learning_multiplier("tag:artist:abc"), feature_learning_multiplier("tag:language:english"))
        self.assertGreater(feature_learning_multiplier("uploader:name"), feature_learning_multiplier("category:manga"))
        self.assertLess(feature_learning_multiplier("title:generic"), feature_learning_multiplier("tag:female:tag"))

    def test_feedback_weights_features_by_namespace(self):
        gallery_url = "https://exhentai.org/g/5b/e/"
        store_galleries(
            self.conn,
            [
                Gallery(
                    url=gallery_url,
                    gid="5b",
                    token="e",
                    title="Generic Token",
                    category="Manga",
                    uploader="UploaderName",
                    tags=["artist:strong", "language:english"],
                )
            ],
        )

        record_feedback(self.conn, gallery_url, vote=1)
        weights = {
            row["feature"]: row["weight"]
            for row in self.conn.execute(
                "SELECT feature, weight FROM feature_weights WHERE feature IN (?, ?, ?, ?, ?)",
                (
                    "tag:artist:strong",
                    "tag:language:english",
                    "uploader:uploadername",
                    "category:manga",
                    "title:generic",
                ),
            )
        }

        self.assertGreater(weights["tag:artist:strong"], weights["tag:language:english"])
        self.assertGreater(weights["uploader:uploadername"], weights["category:manga"])
        self.assertGreater(weights["category:manga"], weights["title:generic"])

    def test_latest_feedback_retrain_replaces_old_signal(self):
        gallery_url = "https://exhentai.org/g/6/f/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="6", token="f", title="Changed Mind", tags=["artist:change"])],
        )
        record_feedback(self.conn, gallery_url, vote=1)
        self.assertIn("artist:change", learned_query_tags(self.conn, limit=5))

        record_feedback(self.conn, gallery_url, vote=-1)
        retrain_model(self.conn)

        self.assertNotIn("artist:change", learned_query_tags(self.conn, limit=5))
        item = recommend(self.conn, limit=1, include_rated=True)[0]
        self.assertEqual(item["user_vote"], -1.0)
        snapshot = model_snapshot(self.conn)
        self.assertEqual(snapshot["counts"]["feedback_events"], 2)
        self.assertEqual(snapshot["counts"]["rated_galleries"], 1)

    def test_repeated_consistent_feedback_increases_learning_confidence(self):
        repeated_url = "https://exhentai.org/g/6c/f/"
        single_url = "https://exhentai.org/g/6d/f/"
        store_galleries(
            self.conn,
            [
                Gallery(url=repeated_url, gid="6c", token="f", title="Repeated Like", tags=["artist:repeat"]),
                Gallery(url=single_url, gid="6d", token="f", title="Single Like", tags=["artist:single"]),
            ],
        )

        record_feedback(self.conn, repeated_url, vote=1)
        record_feedback(self.conn, repeated_url, score=5)
        record_feedback(self.conn, single_url, vote=1)
        weights = {
            row["feature"]: row["weight"]
            for row in self.conn.execute(
                "SELECT feature, weight FROM feature_weights WHERE feature IN (?, ?)",
                ("tag:artist:repeat", "tag:artist:single"),
            )
        }

        self.assertGreater(weights["tag:artist:repeat"], weights["tag:artist:single"])

    def test_opposite_latest_feedback_resets_confidence_direction(self):
        changed_url = "https://exhentai.org/g/6e/f/"
        store_galleries(
            self.conn,
            [Gallery(url=changed_url, gid="6e", token="f", title="Confidence Reset", tags=["artist:reset"])],
        )

        record_feedback(self.conn, changed_url, vote=1)
        record_feedback(self.conn, changed_url, score=5)
        record_feedback(self.conn, changed_url, vote=-1)
        row = self.conn.execute(
            "SELECT weight FROM feature_weights WHERE feature = ?",
            ("tag:artist:reset",),
        ).fetchone()

        self.assertLess(row["weight"], 0)
        self.assertAlmostEqual(row["weight"], -LEARNING_RATE * feature_learning_multiplier("tag:artist:reset"))

    def test_model_snapshot_separates_positive_and_negative_weights(self):
        liked_url = "https://exhentai.org/g/6p/f/"
        disliked_url = "https://exhentai.org/g/6n/f/"
        store_galleries(
            self.conn,
            [
                Gallery(url=liked_url, gid="6p", token="f", title="Liked Model", tags=["artist:liked"]),
                Gallery(url=disliked_url, gid="6n", token="f", title="Disliked Model", tags=["artist:disliked"]),
            ],
        )

        record_feedback(self.conn, liked_url, vote=1)
        record_feedback(self.conn, disliked_url, vote=-1)
        snapshot = model_snapshot(self.conn)

        positive_features = {item["feature"] for item in snapshot["positive_weights"]}
        negative_features = {item["feature"] for item in snapshot["negative_weights"]}
        self.assertIn("tag:artist:liked", positive_features)
        self.assertIn("tag:artist:disliked", negative_features)
        self.assertNotIn("tag:artist:disliked", positive_features)

    def test_recommend_hides_rated_by_default(self):
        rated_url = "https://exhentai.org/g/7/a/"
        unrated_url = "https://exhentai.org/g/8/b/"
        store_galleries(
            self.conn,
            [
                Gallery(url=rated_url, gid="7", token="a", title="Rated Item", tags=["artist:rated"]),
                Gallery(url=unrated_url, gid="8", token="b", title="Unrated Item", tags=["artist:unrated"]),
            ],
        )
        record_feedback(self.conn, rated_url, vote=1)

        default_urls = [item["url"] for item in recommend(self.conn)]
        all_urls = [item["url"] for item in recommend(self.conn, include_rated=True)]

        self.assertNotIn(rated_url, default_urls)
        self.assertIn(unrated_url, default_urls)
        self.assertIn(rated_url, all_urls)

    def test_neutral_score_counts_as_rated_without_learning_weight(self):
        neutral_url = "https://exhentai.org/g/9/c/"
        store_galleries(
            self.conn,
            [Gallery(url=neutral_url, gid="9", token="c", title="Neutral Item", tags=["artist:neutral"])],
        )
        record_feedback(self.conn, neutral_url, score=3)

        self.assertEqual(recommend(self.conn), [])
        included = recommend(self.conn, include_rated=True)
        self.assertEqual(included[0]["url"], neutral_url)
        self.assertTrue(included[0]["rated"])
        self.assertEqual(included[0]["user_score"], 3)
        self.assertEqual(included[0]["user_vote"], 0.0)
        self.assertNotIn("artist:neutral", learned_query_tags(self.conn, limit=5))

    def test_clear_feedback_makes_gallery_unrated_again(self):
        gallery_url = "https://exhentai.org/g/10/d/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="10", token="d", title="Clear Me", tags=["artist:clear"])],
        )
        record_feedback(self.conn, gallery_url, vote=1)
        self.assertIn("artist:clear", learned_query_tags(self.conn, limit=5))

        removed = clear_feedback(self.conn, gallery_url)

        self.assertEqual(removed, 1)
        self.assertNotIn("artist:clear", learned_query_tags(self.conn, limit=5))
        item = recommend(self.conn, include_rated=False)[0]
        self.assertEqual(item["url"], gallery_url)
        self.assertFalse(item["rated"])

    def test_feedback_history_returns_latest_first(self):
        gallery_url = "https://exhentai.org/g/10b/d/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="10b", token="d", title="History Item", tags=["artist:history"])],
        )
        record_feedback(self.conn, gallery_url, vote=1)
        record_feedback(self.conn, gallery_url, score=2)

        history = feedback_history(self.conn, gallery_url)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["score"], 2)
        self.assertEqual(history[0]["vote"], -0.5)
        self.assertEqual(history[1]["vote"], 1.0)

    def test_export_import_preferences_round_trip(self):
        gallery_url = "https://exhentai.org/g/11/e/"
        store_galleries(
            self.conn,
            [Gallery(url=gallery_url, gid="11", token="e", title="Portable", tags=["artist:portable"])],
        )
        upsert_bootstrap_tags(self.conn, [("artist:portable", 2.0)])
        record_feedback(self.conn, gallery_url, score=5)
        exported = export_preferences(self.conn)

        target = sqlite3.connect(":memory:")
        target.row_factory = sqlite3.Row
        target.executescript(db.SCHEMA)
        result = import_preferences(target, exported)

        self.assertEqual(result["bootstrap_tags"], 1)
        self.assertEqual(result["galleries"], 1)
        self.assertEqual(result["feedback"], 1)
        self.assertIn("artist:portable", learned_query_tags(target, limit=5))
        self.assertEqual(recommend(target, include_rated=True)[0]["url"], gallery_url)
        target.close()

    def test_import_preferences_normalizes_bootstrap_tags(self):
        payload = {
            "schema": "exh-rec-preferences-v1",
            "bootstrap_tags": [{"tag": "artist:import_value", "weight": 3.0}],
            "galleries": [],
            "feedback": [],
        }

        result = import_preferences(self.conn, payload)

        self.assertEqual(result["bootstrap_tags"], 1)
        self.assertEqual(get_bootstrap_tags(self.conn), [{"tag": "artist:import value", "weight": 3.0}])

    def test_import_preferences_skips_galleries_without_urls(self):
        payload = {
            "schema": "exh-rec-preferences-v1",
            "bootstrap_tags": [],
            "galleries": [{"title": "Broken Import", "tags_json": '["artist:broken"]'}],
            "feedback": [{"gallery_url": "", "vote": 1.0}],
        }

        result = import_preferences(self.conn, payload)
        count = self.conn.execute("SELECT COUNT(*) AS c FROM galleries").fetchone()["c"]

        self.assertEqual(result["galleries"], 0)
        self.assertEqual(result["feedback"], 0)
        self.assertEqual(count, 0)

    def test_import_preferences_skips_non_object_entries(self):
        valid_url = "https://exhentai.org/g/12b/e/"
        payload = {
            "schema": "exh-rec-preferences-v1",
            "bootstrap_tags": ["broken", {"tag": "artist:valid_import", "weight": 2.0}],
            "galleries": [
                "broken",
                {"url": valid_url, "gid": "12b", "token": "e", "title": "Valid Import", "tags_json": '["artist:valid"]'},
            ],
            "feedback": ["broken", {"gallery_url": valid_url, "vote": 1.0}],
        }

        result = import_preferences(self.conn, payload)

        self.assertEqual(result["bootstrap_tags"], 1)
        self.assertEqual(result["galleries"], 1)
        self.assertEqual(result["feedback"], 1)
        self.assertEqual(get_bootstrap_tags(self.conn), [{"tag": "artist:valid import", "weight": 2.0}])
        self.assertIn("artist:valid", learned_query_tags(self.conn, limit=5))

    def test_import_preferences_skips_invalid_weights_and_votes(self):
        valid_url = "https://exhentai.org/g/12c/e/"
        bad_vote_url = "https://exhentai.org/g/12d/e/"
        out_of_range_vote_url = "https://exhentai.org/g/12e/e/"
        payload = {
            "schema": "exh-rec-preferences-v1",
            "bootstrap_tags": [
                {"tag": "artist:bad_weight", "weight": "not-a-number"},
                {"tag": "artist:nan_weight", "weight": "NaN"},
                {"tag": "artist:valid_weight", "weight": "2.5"},
            ],
            "galleries": [
                {"url": valid_url, "title": "Valid Vote", "tags_json": "not-json"},
                {"url": bad_vote_url, "title": "Bad Vote", "tags_json": '{"not": "a list"}'},
                {"url": out_of_range_vote_url, "title": "Out Of Range Vote"},
            ],
            "feedback": [
                {"gallery_url": valid_url, "vote": "1", "score": "5"},
                {"gallery_url": bad_vote_url, "vote": "not-a-number", "score": "bad-score"},
                {"gallery_url": out_of_range_vote_url, "vote": "99", "score": "999"},
                {"gallery_url": valid_url, "vote": "0.5", "score": "999"},
            ],
        }

        result = import_preferences(self.conn, payload)
        tags_json = self.conn.execute("SELECT tags_json FROM galleries WHERE url = ?", (valid_url,)).fetchone()["tags_json"]
        history = feedback_history(self.conn, valid_url)

        self.assertEqual(result["bootstrap_tags"], 1)
        self.assertEqual(result["galleries"], 3)
        self.assertEqual(result["feedback"], 2)
        self.assertEqual(get_bootstrap_tags(self.conn), [{"tag": "artist:valid weight", "weight": 2.5}])
        self.assertEqual(tags_json, "[]")
        self.assertIsNone(history[0]["score"])
        self.assertEqual(history[0]["vote"], 0.5)
        self.assertEqual(history[1]["score"], 5)

    def test_import_preferences_derives_vote_from_score_only_feedback(self):
        liked_url = "https://exhentai.org/g/12f/e/"
        disliked_url = "https://exhentai.org/g/12g/e/"
        payload = {
            "schema": "exh-rec-preferences-v1",
            "bootstrap_tags": [],
            "galleries": [
                {"url": liked_url, "title": "Score Liked", "tags_json": '["artist:scoreliked"]'},
                {"url": disliked_url, "title": "Score Disliked", "tags_json": '["artist:scoredisliked"]'},
            ],
            "feedback": [
                {"gallery_url": liked_url, "score": 5},
                {"gallery_url": disliked_url, "score": 1},
            ],
        }

        result = import_preferences(self.conn, payload)
        liked_history = feedback_history(self.conn, liked_url)
        disliked_history = feedback_history(self.conn, disliked_url)

        self.assertEqual(result["feedback"], 2)
        self.assertEqual(liked_history[0]["vote"], 1.0)
        self.assertEqual(disliked_history[0]["vote"], -1.0)
        self.assertIn("artist:scoreliked", learned_query_tags(self.conn, limit=5))
        self.assertNotIn("artist:scoredisliked", learned_query_tags(self.conn, limit=5))

    def test_import_rejects_unknown_schema(self):
        with self.assertRaises(ValueError):
            import_preferences(self.conn, {"schema": "unknown"})

    def test_recommend_page_supports_offset_and_has_more(self):
        galleries = [
            Gallery(url=f"https://exhentai.org/g/{idx}/a/", gid=str(idx), token="a", title=f"Page Item {idx}")
            for idx in range(5)
        ]
        store_galleries(self.conn, galleries)

        first = recommend_page(self.conn, limit=2)
        second = recommend_page(self.conn, limit=2, offset=2)

        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(len(second["items"]), 2)
        self.assertEqual(second["offset"], 2)
        self.assertEqual(first["total"], 5)
        self.assertEqual(first["candidate_limit"], 2000)

    def test_recommend_candidate_limit_controls_scored_pool(self):
        galleries = [
            Gallery(
                url=f"https://exhentai.org/g/{1000 + idx}/a/",
                gid=str(1000 + idx),
                token="a",
                title=f"Recent Plain {idx}",
            )
            for idx in range(100)
        ]
        galleries.append(
            Gallery(
                url="https://exhentai.org/g/2000/a/",
                gid="2000",
                token="a",
                title="Older Favorite",
                tags=["artist:oldfav"],
            )
        )
        store_galleries(self.conn, galleries)
        for idx in range(100):
            self.conn.execute(
                "UPDATE galleries SET last_seen_at = ? WHERE gid = ?",
                (f"2026-06-02 00:{59 - idx // 2:02d}:{59 - idx % 2:02d}", str(1000 + idx)),
            )
        self.conn.execute("UPDATE galleries SET last_seen_at = '2026-06-01 23:00:00' WHERE gid = '2000'")
        upsert_bootstrap_tags(self.conn, [("artist:oldfav", 5.0)])

        narrow = recommend_page(self.conn, limit=2, candidate_limit=100)
        wide = recommend_page(self.conn, limit=2, candidate_limit=200)

        self.assertNotIn("https://exhentai.org/g/2000/a/", [item["url"] for item in narrow["items"]])
        self.assertEqual(wide["items"][0]["url"], "https://exhentai.org/g/2000/a/")

    def test_recommend_supports_local_filter_text(self):
        store_galleries(
            self.conn,
            [
                Gallery(
                    url="https://exhentai.org/g/12/a/",
                    gid="12",
                    token="a",
                    title="Blue Local Match",
                    category="Manga",
                    tags=["artist:alpha", "language:english"],
                ),
                Gallery(
                    url="https://exhentai.org/g/13/b/",
                    gid="13",
                    token="b",
                    title="Red Other Item",
                    category="Doujinshi",
                    tags=["artist:beta", "language:japanese"],
                ),
            ],
        )

        title_urls = [item["url"] for item in recommend(self.conn, filter_text="blue match")]
        tag_urls = [item["url"] for item in recommend(self.conn, filter_text="artist:beta")]
        category_page = recommend_page(self.conn, filter_text="manga")

        self.assertEqual(title_urls, ["https://exhentai.org/g/12/a/"])
        self.assertEqual(tag_urls, ["https://exhentai.org/g/13/b/"])
        self.assertEqual(category_page["total"], 1)
        self.assertEqual(category_page["items"][0]["url"], "https://exhentai.org/g/12/a/")

    def test_local_filter_searches_beyond_normal_candidate_pool(self):
        recent_galleries = [
            Gallery(
                url=f"https://exhentai.org/g/{3000 + idx}/a/",
                gid=str(3000 + idx),
                token="a",
                title=f"Recent Filter Miss {idx}",
            )
            for idx in range(100)
        ]
        old_match = Gallery(
            url="https://exhentai.org/g/3999/a/",
            gid="3999",
            token="a",
            title="Older Filter Target",
            tags=["artist:deepmatch"],
        )
        store_galleries(self.conn, [old_match, *recent_galleries])
        for idx in range(100):
            self.conn.execute(
                "UPDATE galleries SET last_seen_at = ? WHERE gid = ?",
                (f"2026-06-02 00:{59 - idx // 2:02d}:{59 - idx % 2:02d}", str(3000 + idx)),
            )
        self.conn.execute("UPDATE galleries SET last_seen_at = '2026-06-01 20:00:00' WHERE gid = '3999'")

        unfiltered = recommend_page(self.conn, limit=5, candidate_limit=100)
        filtered = recommend_page(self.conn, limit=5, candidate_limit=100, filter_text="deepmatch")

        self.assertNotIn(old_match.url, [item["url"] for item in unfiltered["items"]])
        self.assertEqual([item["url"] for item in filtered["items"]], [old_match.url])
        self.assertEqual(filtered["candidate_limit"], 10000)

    def test_recommendation_diversity_surfaces_nearby_alternatives(self):
        favored_urls = [f"https://exhentai.org/g/30{idx}/a/" for idx in range(3)]
        alternative_url = "https://exhentai.org/g/309/a/"
        store_galleries(
            self.conn,
            [
                Gallery(url=favored_urls[0], gid="300", token="a", title="Fav One", tags=["artist:favored"]),
                Gallery(url=favored_urls[1], gid="301", token="a", title="Fav Two", tags=["artist:favored"]),
                Gallery(url=favored_urls[2], gid="302", token="a", title="Fav Three", tags=["artist:favored"]),
                Gallery(url=alternative_url, gid="309", token="a", title="Alt One", tags=["artist:alternative"]),
            ],
        )
        for gid, seen_at in [
            ("300", "2026-06-02 00:04:00"),
            ("301", "2026-06-02 00:03:00"),
            ("302", "2026-06-02 00:02:00"),
            ("309", "2026-06-02 00:01:00"),
        ]:
            self.conn.execute("UPDATE galleries SET last_seen_at = ? WHERE gid = ?", (seen_at, gid))
        upsert_bootstrap_tags(self.conn, [("artist:favored", 2.0), ("artist:alternative", 1.85)])

        items = recommend_page(self.conn, limit=4)["items"]

        self.assertEqual(items[0]["url"], favored_urls[0])
        self.assertEqual(items[1]["url"], alternative_url)
        self.assertIn("diversity", " ".join(items[2]["reasons"]))

    def test_feedback_learns_from_uploader(self):
        liked_url = "https://exhentai.org/g/14/a/"
        same_uploader_url = "https://exhentai.org/g/15/b/"
        different_uploader_url = "https://exhentai.org/g/16/c/"
        store_galleries(
            self.conn,
            [
                Gallery(url=liked_url, gid="14", token="a", title="First Sample", uploader="TrustedUploader"),
                Gallery(url=different_uploader_url, gid="16", token="c", title="Different Candidate", uploader="OtherUploader"),
                Gallery(url=same_uploader_url, gid="15", token="b", title="Second Candidate", uploader="TrustedUploader"),
            ],
        )

        record_feedback(self.conn, liked_url, vote=1)
        items = recommend(self.conn)
        feature = self.conn.execute(
            "SELECT weight FROM feature_weights WHERE feature = ?",
            ("uploader:trusteduploader",),
        ).fetchone()

        self.assertEqual(items[0]["url"], same_uploader_url)
        self.assertIsNotNone(feature)
        self.assertGreater(feature["weight"], 0)

    def test_local_filter_matches_uploader(self):
        store_galleries(
            self.conn,
            [
                Gallery(url="https://exhentai.org/g/17/a/", gid="17", token="a", title="Uploader Match", uploader="FindMe"),
                Gallery(url="https://exhentai.org/g/18/b/", gid="18", token="b", title="Other Upload", uploader="NoMatch"),
            ],
        )

        items = recommend(self.conn, filter_text="findme")

        self.assertEqual([item["url"] for item in items], ["https://exhentai.org/g/17/a/"])


if __name__ == "__main__":
    unittest.main()
