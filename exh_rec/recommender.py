from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import time
import urllib.parse
from dataclasses import asdict

from .exhentai import Gallery
from .visual import DINOV2_VISUAL_VERSION, SIMPLE_VISUAL_VERSION, normalize_embedding


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_:+.-]{1,}", re.I)
LEARNING_RATE = 0.35
MAX_FEEDBACK_SIGNAL = 1.0
FEEDBACK_CONFIDENCE_STEP = 0.15
MAX_FEEDBACK_CONFIDENCE_BOOST = 0.45
MAX_FEEDBACK_CONFIDENCE_HISTORY = 5
DIVERSITY_PENALTY = 0.45
VISUAL_EMBEDDING_VERSION = DINOV2_VISUAL_VERSION
FALLBACK_VISUAL_EMBEDDING_VERSION = SIMPLE_VISUAL_VERSION
VISUAL_VERSION_PRIORITY = (DINOV2_VISUAL_VERSION, SIMPLE_VISUAL_VERSION)
VISUAL_SCORE_SCALE = 1.35
VISUAL_MIN_DIMS = 16
VISUAL_MAX_DIMS = 2048
MIN_CORPUS_TAG_STRENGTH_GALLERIES = 10
MIN_TAG_STRENGTH = 0.55
MAX_TAG_STRENGTH = 1.2
MIN_BOOTSTRAP_EXPLORE_SCORE = 1.0
FEATURE_LEARNING_MULTIPLIERS = {
    "tag:artist": 1.45,
    "tag:group": 1.35,
    "tag:parody": 1.25,
    "tag:character": 1.2,
    "tag:female": 1.1,
    "tag:male": 1.1,
    "tag:language": 0.75,
    "uploader": 1.25,
    "category": 0.8,
    "title": 0.35,
}
BOOTSTRAP_NAMESPACES = {
    "artist",
    "character",
    "cosplayer",
    "female",
    "group",
    "language",
    "male",
    "mixed",
    "other",
    "parody",
    "reclass",
    "category",
    "uploader",
}
MODEL_MODE_HYBRID = "hybrid"
MODEL_MODE_VISUAL = "visual"
MODEL_MODES = {MODEL_MODE_HYBRID, MODEL_MODE_VISUAL}


def parse_bootstrap_tags(raw: str) -> list[tuple[str, float]]:
    tags: list[tuple[str, float]] = []
    for part in re.split(r"[\n,]+", raw):
        value = part.strip().lower()
        if not value:
            continue
        weight = 1.0
        if value.startswith("-"):
            value = value[1:].strip()
            weight = -1.0
        value, weight = parse_bootstrap_weight(value, weight)
        value = normalize_bootstrap_value(value)
        if value:
            tags.append((value, weight))
    return tags


def parse_bootstrap_weight(value: str, default_weight: float = 1.0) -> tuple[str, float]:
    if ":" not in value:
        return value, default_weight
    possible_tag, possible_weight = value.rsplit(":", 1)
    try:
        parsed_weight = float(possible_weight.strip())
    except ValueError:
        return value, default_weight
    namespace = possible_tag.split(":", 1)[0].strip().lower()
    has_explicit_weight = ":" in possible_tag or namespace not in BOOTSTRAP_NAMESPACES
    if not has_explicit_weight:
        return value, default_weight
    return possible_tag.strip(), parsed_weight


def normalize_bootstrap_value(value: str) -> str:
    value = urllib.parse.unquote_plus(value).replace("_", " ")
    return " ".join(value.split()).strip()


def upsert_bootstrap_tags(conn: sqlite3.Connection, tags: list[tuple[str, float]]) -> None:
    conn.execute("DELETE FROM bootstrap_tags")
    for tag, weight in tags:
        tag = normalize_bootstrap_value(str(tag).strip().lower())
        if not tag:
            continue
        conn.execute(
            """
            INSERT INTO bootstrap_tags(tag, weight, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tag) DO UPDATE SET
                weight = excluded.weight,
                updated_at = CURRENT_TIMESTAMP
            """,
            (tag, weight),
        )


def get_bootstrap_tags(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT tag, weight FROM bootstrap_tags ORDER BY tag")]


def learned_query_tags(conn: sqlite3.Connection, limit: int = 6) -> list[str]:
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT SUBSTR(feature, 5) AS tag
        FROM feature_weights
        WHERE feature LIKE 'tag:%' AND weight > 0
        ORDER BY weight DESC, positive_count DESC, feature ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row["tag"] for row in rows if row["tag"]]


def store_galleries(conn: sqlite3.Connection, galleries: list[Gallery], detail_fetched: bool = False) -> int:
    count = 0
    for gallery in galleries:
        tag_weights_json = json.dumps(normalize_tag_weights(gallery.tag_weights), ensure_ascii=True)
        existing = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery.url,)).fetchone()
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, category, uploader, posted_at, thumb_url,
                rating, tags_json, tag_weights_json, source_query, detail_fetched_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                category = COALESCE(excluded.category, galleries.category),
                uploader = COALESCE(excluded.uploader, galleries.uploader),
                posted_at = COALESCE(excluded.posted_at, galleries.posted_at),
                thumb_url = CASE
                    WHEN excluded.detail_fetched_at IS NULL AND galleries.detail_fetched_at IS NOT NULL THEN galleries.thumb_url
                    ELSE COALESCE(excluded.thumb_url, galleries.thumb_url)
                END,
                rating = COALESCE(excluded.rating, galleries.rating),
                tags_json = CASE
                    WHEN excluded.tags_json != '[]' THEN excluded.tags_json
                    ELSE galleries.tags_json
                END,
                tag_weights_json = CASE
                    WHEN excluded.tag_weights_json != '{}' THEN excluded.tag_weights_json
                    ELSE galleries.tag_weights_json
                END,
                source_query = COALESCE(excluded.source_query, galleries.source_query),
                detail_fetched_at = COALESCE(excluded.detail_fetched_at, galleries.detail_fetched_at),
                last_seen_at = CASE
                    WHEN ? THEN galleries.last_seen_at
                    ELSE CURRENT_TIMESTAMP
                END
            """,
            (
                gallery.url,
                gallery.gid,
                gallery.token,
                gallery.title,
                gallery.category,
                gallery.uploader,
                gallery.posted_at,
                gallery.thumb_url,
                gallery.rating,
                json.dumps(gallery.tags, ensure_ascii=True),
                tag_weights_json,
                gallery.source_query,
                None,
                1 if detail_fetched else 0,
            ),
        )
        if detail_fetched:
            conn.execute("UPDATE galleries SET detail_fetched_at = CURRENT_TIMESTAMP WHERE url = ?", (gallery.url,))
        if not existing:
            count += 1
    return count


def store_gallery_samples(
    conn: sqlite3.Connection,
    url: str,
    page_count: int | None,
    samples: list,
) -> None:
    samples_json = json.dumps(samples, ensure_ascii=True)
    conn.execute(
        """
        UPDATE galleries
        SET page_count = COALESCE(?, page_count),
            samples_json = CASE
                WHEN ? != '[]' THEN ?
                ELSE samples_json
            END,
            samples_fetched_at = CURRENT_TIMESTAMP
        WHERE url = ?
        """,
        (page_count, samples_json, samples_json, url),
    )


def clear_shared_thumbnail_metadata(conn: sqlite3.Connection) -> int:
    """Clear likely CSS-sprite cover URLs shared by multiple galleries.

    Only the brittle ``s.exhentai.org/w/`` sprite covers are cleared; stable
    per-gallery ehgt.org covers (from the gdata API) are left alone. The visual
    embedding is preserved so a transient bad cover does not throw away learned
    vectors — the next Refresh Thumbs repopulates an ehgt cover.
    """
    cursor = conn.execute(
        """
        UPDATE galleries
        SET thumb_url = NULL
        WHERE thumb_url IN (
            SELECT thumb_url
            FROM galleries
            WHERE thumb_url LIKE 'https://s.exhentai.org/w/%'
              AND thumb_url NOT LIKE 'https://ehgt.org/%'
            GROUP BY thumb_url
            HAVING COUNT(*) > 1
        )
        """
    )
    return int(cursor.rowcount or 0)


def store_visual_embedding(
    conn: sqlite3.Connection,
    gallery_url: str,
    embedding: list[object],
    version: str = VISUAL_EMBEDDING_VERSION,
) -> None:
    normalized = normalize_visual_embedding(embedding)
    exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not exists:
        raise ValueError("Gallery not found")
    conn.execute(
        """
        UPDATE galleries
        SET visual_embedding_json = ?,
            visual_embedding_version = ?,
            visual_embedding_at = CURRENT_TIMESTAMP
        WHERE url = ?
        """,
        (json.dumps(normalized, ensure_ascii=True), version, gallery_url),
    )


def normalize_visual_embedding(embedding: list[object]) -> list[float]:
    if not isinstance(embedding, list):
        raise ValueError("embedding must be a list")
    if len(embedding) < VISUAL_MIN_DIMS or len(embedding) > VISUAL_MAX_DIMS:
        raise ValueError(f"embedding must have {VISUAL_MIN_DIMS} to {VISUAL_MAX_DIMS} values")
    return normalize_embedding(embedding)


def parse_visual_embedding(raw: object) -> list[float] | None:
    if not raw:
        return None
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(str(raw))
        except json.JSONDecodeError:
            return None
    if not isinstance(values, list):
        return None
    try:
        return normalize_visual_embedding(values)
    except ValueError:
        return None


def gallery_features(gallery: dict) -> list[str]:
    return [feature for feature, _strength in gallery_feature_values(gallery)]


def gallery_feature_values(gallery: dict, tag_strengths: dict[str, float] | None = None) -> list[tuple[str, float]]:
    features: dict[str, float] = {}
    tag_weights = normalize_tag_weights(gallery.get("tag_weights") or {})
    tag_strengths = tag_strengths or {}

    def add_feature(feature: str, strength: float = 1.0) -> None:
        feature = feature.strip().lower()
        if not feature:
            return
        features[feature] = max(features.get(feature, 0.0), strength)

    category = (gallery.get("category") or "").strip().lower()
    if category:
        add_feature(f"category:{category}")
    uploader = (gallery.get("uploader") or "").strip().lower()
    if uploader:
        add_feature(f"uploader:{uploader}")
    for tag in gallery.get("tags") or []:
        norm = str(tag).strip().lower()
        if not norm:
            continue
        strength = tag_weights.get(norm)
        if strength is None:
            strength = tag_strengths.get(norm, 1.0)
        add_feature(f"tag:{norm}", strength)
    for token in TOKEN_RE.findall(gallery.get("title") or ""):
        token = token.lower().strip("_:+.-")
        if len(token) >= 3 and not token.isdigit():
            add_feature(f"title:{token}")
    return sorted(features.items())


def tag_namespace(tag: str) -> str:
    return tag.split(":", 1)[0] if ":" in tag else ""


def normalize_tag_weights(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    weights: dict[str, float] = {}
    for tag, value in raw.items():
        tag = normalize_bootstrap_value(str(tag or "").strip().lower())
        if not tag:
            continue
        try:
            strength = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isnan(strength):
            continue
        weights[tag] = max(MIN_TAG_STRENGTH, min(MAX_TAG_STRENGTH, strength))
    return weights


def record_feedback(
    conn: sqlite3.Connection,
    gallery_url: str,
    vote: float | None = None,
    note: str | None = None,
    score: int | None = None,
) -> None:
    signal = feedback_signal(vote=vote, score=score)
    conn.execute(
        "INSERT INTO feedback(gallery_url, vote, score, note) VALUES (?, ?, ?, ?)",
        (gallery_url, signal, score, note),
    )
    retrain_model(conn)


def clear_feedback(conn: sqlite3.Connection, gallery_url: str) -> int:
    cursor = conn.execute("DELETE FROM feedback WHERE gallery_url = ?", (gallery_url,))
    retrain_model(conn)
    return cursor.rowcount


def reset_library(conn: sqlite3.Connection) -> dict[str, int]:
    """Wipe fetched galleries, votes, learned weights, and fetch history.

    Cookies and tuning live in ``settings`` and bootstrap tags in ``bootstrap_tags``,
    so both survive untouched.
    """
    removed: dict[str, int] = {}
    for table in ("feedback", "feature_weights", "fetch_runs", "galleries"):
        cursor = conn.execute(f"DELETE FROM {table}")
        removed[table] = cursor.rowcount
    return removed


def feedback_history(conn: sqlite3.Connection, gallery_url: str, limit: int = 25) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, gallery_url, vote, score, note, created_at
            FROM feedback
            WHERE gallery_url = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (gallery_url, limit),
        )
    ]


def export_preferences(conn: sqlite3.Connection) -> dict:
    feedback_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT gallery_url, vote, score, note, created_at
            FROM feedback
            ORDER BY id
            """
        )
    ]
    feedback_urls = [row["gallery_url"] for row in feedback_rows]
    galleries = []
    if feedback_urls:
        placeholders = ",".join("?" for _ in feedback_urls)
        galleries = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT url, gid, token, title, category, uploader, posted_at, thumb_url,
                       rating, tags_json, tag_weights_json, source_query, detail_fetched_at, first_seen_at, last_seen_at
                FROM galleries
                WHERE url IN ({placeholders})
                ORDER BY url
                """,
                feedback_urls,
            )
        ]
    return {
        "schema": "exh-rec-preferences-v1",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "bootstrap_tags": get_bootstrap_tags(conn),
        "galleries": galleries,
        "feedback": feedback_rows,
    }


def import_preferences(conn: sqlite3.Connection, payload: dict, replace: bool = False) -> dict:
    if payload.get("schema") != "exh-rec-preferences-v1":
        raise ValueError("Unsupported preference export schema")
    if replace:
        conn.execute("DELETE FROM feedback")
        conn.execute("DELETE FROM bootstrap_tags")

    galleries = import_rows(payload, "galleries")
    imported_galleries = 0
    for gallery in galleries:
        gallery_url = str(gallery.get("url") or "").strip()
        if not gallery_url:
            continue
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, category, uploader, posted_at, thumb_url,
                rating, tags_json, tag_weights_json, source_query, detail_fetched_at, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                category = COALESCE(excluded.category, galleries.category),
                uploader = COALESCE(excluded.uploader, galleries.uploader),
                posted_at = COALESCE(excluded.posted_at, galleries.posted_at),
                thumb_url = COALESCE(excluded.thumb_url, galleries.thumb_url),
                rating = COALESCE(excluded.rating, galleries.rating),
                tags_json = CASE
                    WHEN excluded.tags_json != '[]' THEN excluded.tags_json
                    ELSE galleries.tags_json
                END,
                tag_weights_json = CASE
                    WHEN excluded.tag_weights_json != '{}' THEN excluded.tag_weights_json
                    ELSE galleries.tag_weights_json
                END,
                source_query = COALESCE(excluded.source_query, galleries.source_query),
                detail_fetched_at = COALESCE(excluded.detail_fetched_at, galleries.detail_fetched_at),
                last_seen_at = COALESCE(excluded.last_seen_at, galleries.last_seen_at)
            """,
            (
                gallery_url,
                gallery.get("gid"),
                gallery.get("token"),
                gallery.get("title") or "Imported gallery",
                gallery.get("category"),
                gallery.get("uploader"),
                gallery.get("posted_at"),
                gallery.get("thumb_url"),
                import_gallery_rating(gallery.get("rating")),
                import_tags_json(gallery.get("tags_json")),
                import_tag_weights_json(gallery.get("tag_weights_json")),
                gallery.get("source_query"),
                gallery.get("detail_fetched_at"),
                gallery.get("first_seen_at"),
                gallery.get("last_seen_at"),
            ),
        )
        imported_galleries += 1

    imported_tags = 0
    for tag in import_rows(payload, "bootstrap_tags"):
        value = normalize_bootstrap_value(str(tag.get("tag") or "").strip().lower())
        if not value:
            continue
        weight = optional_float(tag.get("weight", 1.0))
        if weight is None:
            continue
        conn.execute(
            """
            INSERT INTO bootstrap_tags(tag, weight, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tag) DO UPDATE SET
                weight = excluded.weight,
                updated_at = CURRENT_TIMESTAMP
            """,
            (value, weight),
        )
        imported_tags += 1

    imported_feedback = 0
    for item in import_rows(payload, "feedback"):
        gallery_url = item.get("gallery_url")
        if not gallery_url:
            continue
        exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not exists:
            continue
        score = import_feedback_score(item.get("score"))
        vote = import_feedback_vote(item.get("vote")) if "vote" in item else None
        if vote is None and score is not None:
            vote = feedback_signal(score=score)
        if vote is None:
            continue
        conn.execute(
            """
            INSERT INTO feedback(gallery_url, vote, score, note, created_at)
            VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                gallery_url,
                vote,
                score,
                item.get("note"),
                item.get("created_at"),
            ),
        )
        imported_feedback += 1

    retrain_model(conn)
    return {
        "bootstrap_tags": imported_tags,
        "galleries": imported_galleries,
        "feedback": imported_feedback,
    }


def import_rows(payload: dict, key: str) -> list[dict]:
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def import_tags_json(raw: object) -> str:
    if not raw:
        return "[]"
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return "[]"
    if not isinstance(parsed, list):
        return "[]"
    return json.dumps(parsed, ensure_ascii=True)


def import_tag_weights_json(raw: object) -> str:
    if not raw:
        return "{}"
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return "{}"
    return json.dumps(normalize_tag_weights(parsed), ensure_ascii=True)


def optional_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def import_gallery_rating(value: object) -> float | None:
    rating = optional_float(value)
    if rating is None or rating < 0 or rating > 5:
        return None
    return rating


def import_feedback_vote(value: object) -> float | None:
    vote = optional_float(value)
    if vote is None or vote < -MAX_FEEDBACK_SIGNAL or vote > MAX_FEEDBACK_SIGNAL:
        return None
    return vote


def import_feedback_score(value: object) -> int | None:
    if value is None:
        return None
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    if score < 1 or score > 5:
        return None
    return score


def retrain_model(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM feature_weights")
    tag_strengths = tag_corpus_strengths(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.id AS feedback_id, f.vote AS feedback_signal
        FROM feedback f
        JOIN galleries g ON g.url = f.gallery_url
        JOIN (
            SELECT gallery_url, MAX(id) AS latest_id
            FROM feedback
            GROUP BY gallery_url
        ) latest ON latest.gallery_url = f.gallery_url AND latest.latest_id = f.id
        WHERE f.vote != 0
        """
    ).fetchall()
    for row in rows:
        gallery = dict(row)
        signal = float(gallery.pop("feedback_signal") or 0)
        feedback_id = int(gallery.pop("feedback_id"))
        signal *= feedback_confidence(conn, gallery["url"], feedback_id, signal)
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
        apply_feedback_features(conn, gallery, signal, tag_strengths=tag_strengths)


def visual_preference_model(conn: sqlite3.Connection) -> dict | None:
    rows = conn.execute(
        """
        SELECT g.url, g.visual_embedding_json, g.visual_embedding_version, f.id AS feedback_id, f.vote AS feedback_signal
        FROM feedback f
        JOIN galleries g ON g.url = f.gallery_url
        JOIN (
            SELECT gallery_url, MAX(id) AS latest_id
            FROM feedback
            GROUP BY gallery_url
        ) latest ON latest.gallery_url = f.gallery_url AND latest.latest_id = f.id
        WHERE f.vote != 0
          AND g.visual_embedding_json IS NOT NULL
          AND g.visual_embedding_json != ''
        """
    ).fetchall()
    version = active_visual_version(rows)
    if not version:
        return None
    vector_sum: list[float] | None = None
    positive_count = 0
    negative_count = 0
    total_weight = 0.0
    for row in rows:
        if row["visual_embedding_version"] != version:
            continue
        embedding = parse_visual_embedding(row["visual_embedding_json"])
        if not embedding:
            continue
        signal = float(row["feedback_signal"] or 0)
        signal *= feedback_confidence(conn, row["url"], int(row["feedback_id"]), signal)
        if signal > 0:
            positive_count += 1
        elif signal < 0:
            negative_count += 1
        if vector_sum is None:
            vector_sum = [0.0] * len(embedding)
        if len(embedding) != len(vector_sum):
            continue
        for index, value in enumerate(embedding):
            vector_sum[index] += value * signal
        total_weight += abs(signal)
    if not vector_sum or total_weight <= 0:
        return None
    norm = math.sqrt(sum(value * value for value in vector_sum))
    if norm <= 0:
        return None
    return {
        "version": version,
        "vector": [value / norm for value in vector_sum],
        "positive_count": positive_count,
        "negative_count": negative_count,
        "rated_count": positive_count + negative_count,
        "total_weight": round(total_weight, 3),
    }


def active_visual_version(rows: list[sqlite3.Row]) -> str | None:
    versions = {
        str(row["visual_embedding_version"] or "")
        for row in rows
        if row["visual_embedding_json"]
    }
    for version in VISUAL_VERSION_PRIORITY:
        if version in versions:
            return version
    return sorted(versions)[0] if versions else None


def feedback_confidence(conn: sqlite3.Connection, gallery_url: str, feedback_id: int, signal: float) -> float:
    if signal == 0:
        return 0.0
    direction = 1 if signal > 0 else -1
    rows = conn.execute(
        """
        SELECT vote
        FROM feedback
        WHERE gallery_url = ? AND id <= ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (gallery_url, feedback_id, MAX_FEEDBACK_CONFIDENCE_HISTORY),
    ).fetchall()
    streak = 0
    for row in rows:
        vote = float(row["vote"] or 0)
        if vote == 0:
            break
        if (1 if vote > 0 else -1) != direction:
            break
        streak += 1
    boost = min(MAX_FEEDBACK_CONFIDENCE_BOOST, max(0, streak - 1) * FEEDBACK_CONFIDENCE_STEP)
    return 1.0 + boost


def apply_feedback_features(
    conn: sqlite3.Connection,
    gallery: dict,
    signal: float,
    tag_strengths: dict[str, float] | None = None,
) -> None:
    if signal == 0:
        return
    for feature, strength in gallery_feature_values(gallery, tag_strengths=tag_strengths):
        weighted_signal = signal * LEARNING_RATE * feature_learning_multiplier(feature) * strength
        conn.execute(
            """
            INSERT INTO feature_weights(feature, weight, positive_count, negative_count, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(feature) DO UPDATE SET
                weight = feature_weights.weight + excluded.weight,
                positive_count = feature_weights.positive_count + excluded.positive_count,
                negative_count = feature_weights.negative_count + excluded.negative_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                feature,
                weighted_signal,
                1 if signal > 0 else 0,
                1 if signal < 0 else 0,
            ),
        )


def tag_corpus_strengths(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute("SELECT tags_json FROM galleries").fetchall()
    total = len(rows)
    if total < MIN_CORPUS_TAG_STRENGTH_GALLERIES:
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except json.JSONDecodeError:
            continue
        seen: set[str] = set()
        for raw in tags if isinstance(tags, list) else []:
            tag = normalize_bootstrap_value(str(raw or "").strip().lower())
            if tag:
                seen.add(tag)
        for tag in seen:
            counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return {}
    max_idf = math.log((total + 1) / 2) + 1
    if max_idf <= 1:
        return {}
    strengths: dict[str, float] = {}
    for tag, count in counts.items():
        idf = math.log((total + 1) / (count + 1)) + 1
        rarity = math.sqrt(max(0.0, min(1.0, (idf - 1) / (max_idf - 1))))
        support = 0.55 + 0.45 * (1.0 - math.exp(-count / 4.0))
        strength = MIN_TAG_STRENGTH + (MAX_TAG_STRENGTH - MIN_TAG_STRENGTH) * rarity * support
        strengths[tag] = round(max(MIN_TAG_STRENGTH, min(MAX_TAG_STRENGTH, strength)), 4)
    return strengths


def feature_learning_multiplier(feature: str) -> float:
    if feature.startswith("tag:"):
        tag = feature[4:]
        namespace = tag.split(":", 1)[0] if ":" in tag else ""
        return FEATURE_LEARNING_MULTIPLIERS.get(f"tag:{namespace}", 1.0)
    namespace = feature.split(":", 1)[0]
    return FEATURE_LEARNING_MULTIPLIERS.get(namespace, 1.0)


def feedback_signal(vote: float | None = None, score: int | None = None) -> float:
    if score is not None:
        bounded = max(1, min(5, int(score)))
        return round((bounded - 3) / 2, 3)
    if vote is None:
        raise ValueError("vote or score is required")
    if vote == 0:
        return 0.0
    return MAX_FEEDBACK_SIGNAL if vote > 0 else -MAX_FEEDBACK_SIGNAL


def recommend(
    conn: sqlite3.Connection,
    limit: int = 40,
    include_rated: bool = False,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
    freshness_weight: float = 1.0,
    bootstrap_explore_count: int = 0,
    explore_seed: str | None = None,
    language_filter: list[str] | str | None = None,
    model_mode: str = MODEL_MODE_HYBRID,
    require_bootstrap_match: bool = False,
) -> list[dict]:
    return recommend_page(
        conn,
        limit=limit,
        include_rated=include_rated,
        offset=offset,
        filter_text=filter_text,
        candidate_limit=candidate_limit,
        freshness_weight=freshness_weight,
        bootstrap_explore_count=bootstrap_explore_count,
        explore_seed=explore_seed,
        language_filter=language_filter,
        model_mode=model_mode,
        require_bootstrap_match=require_bootstrap_match,
    )["items"]


def recommend_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    include_rated: bool = False,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
    freshness_weight: float = 1.0,
    bootstrap_explore_count: int = 0,
    explore_seed: str | None = None,
    language_filter: list[str] | str | None = None,
    model_mode: str = MODEL_MODE_HYBRID,
    require_bootstrap_match: bool = False,
) -> dict:
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    candidate_limit = 10000 if filter_text else min(10000, max(100, int(candidate_limit)))
    candidate_limit = max(limit + offset, candidate_limit)
    freshness_weight = max(0.0, min(10.0, float(freshness_weight)))
    bootstrap_explore_count = max(0, min(limit - 1, int(bootstrap_explore_count)))
    language_filter_values = normalize_language_filter(language_filter)
    model_mode = normalize_model_mode(model_mode)
    if model_mode == MODEL_MODE_VISUAL:
        bootstrap_explore_count = 0
        require_bootstrap_match = False
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote
        FROM galleries g
        LEFT JOIN (
            SELECT feedback.id AS feedback_id, feedback.gallery_url, feedback.vote, feedback.score AS user_score
            FROM feedback
            JOIN (
                SELECT gallery_url, MAX(id) AS latest_id
                FROM feedback
                GROUP BY gallery_url
            ) latest ON latest.gallery_url = feedback.gallery_url AND latest.latest_id = feedback.id
        ) f ON f.gallery_url = g.url
        ORDER BY g.last_seen_at DESC
        LIMIT ?
        """,
        (candidate_limit,),
    ).fetchall()

    scored = []
    for idx, row in enumerate(rows):
        gallery = dict(row)
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
        gallery["samples"] = json.loads(gallery.pop("samples_json", None) or "[]")
        gallery["visual_embedding"] = parse_visual_embedding(gallery.pop("visual_embedding_json", None))
        gallery["visual_embedding_version"] = gallery.get("visual_embedding_version")
        gallery["visual_ready"] = bool(gallery["visual_embedding"])
        if model_mode == MODEL_MODE_VISUAL:
            score, reasons = score_visual_gallery(gallery, visual_model)
            if score is None:
                continue
        else:
            score, reasons = score_gallery(gallery, bootstrap, weights, visual_model=visual_model, tag_strengths=tag_strengths)
        gallery.pop("visual_embedding", None)
        gallery["user_vote"] = round(float(gallery.get("user_vote", 0) or 0), 3)
        gallery["rated"] = gallery.get("feedback_id") is not None
        if gallery["rated"] and not include_rated:
            continue
        if not gallery_matches_language_filter(gallery, language_filter_values):
            continue
        if require_bootstrap_match and not gallery_matches_positive_bootstrap(gallery, bootstrap):
            continue
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        if model_mode != MODEL_MODE_VISUAL:
            if gallery["user_vote"] < 0:
                score -= 2.0
                reasons.append("previous downvote")
            elif gallery["user_vote"] > 0:
                score += 0.5
                reasons.append("previous upvote")
        if model_mode != MODEL_MODE_VISUAL:
            freshness = freshness_bonus(idx, candidate_limit) * freshness_weight
            score += freshness
            if freshness and reasons != ["recent"]:
                freshness_reason = f"fresh {freshness:+.2f}"
                if freshness_weight > 1.0:
                    reasons.insert(0, freshness_reason)
                else:
                    reasons.append(freshness_reason)
        gallery["score"] = round(score, 3)
        gallery["reasons"] = reasons[:5]
        scored.append(gallery)

    scored.sort(key=lambda item: item["score"], reverse=True)
    scored = diversify_ranked_galleries(scored)
    if bootstrap_explore_count and not include_rated and scored:
        scored = mix_bootstrap_exploration(
            scored,
            limit=limit,
            count=bootstrap_explore_count,
            bootstrap_queries=bootstrap_source_queries(bootstrap),
            seed=explore_seed,
        )
    items = scored[offset : offset + limit]
    next_offset = offset + len(items)
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "total": len(scored),
        "has_more": next_offset < len(scored),
        "candidate_limit": candidate_limit,
        "bootstrap_explore_count": bootstrap_explore_count,
        "require_bootstrap_match": bool(require_bootstrap_match),
        "language_filter": sorted(language_filter_values),
        "model_mode": model_mode,
    }


def normalize_model_mode(value: object) -> str:
    mode = str(value or MODEL_MODE_HYBRID).strip().lower()
    if mode in MODEL_MODES:
        return mode
    return MODEL_MODE_HYBRID


def normalize_language_filter(value: list[str] | str | None) -> set[str]:
    if value is None:
        return set()
    parts = value if isinstance(value, list) else re.split(r"[\n,]+", str(value))
    languages = set()
    for part in parts:
        language = str(part or "").strip().lower()
        if not language:
            continue
        if language.startswith("language:"):
            language = language.split(":", 1)[1].strip()
        if language:
            languages.add(language)
    return languages


def gallery_matches_language_filter(gallery: dict, languages: set[str]) -> bool:
    if not languages:
        return True
    gallery_languages = {
        str(tag).split(":", 1)[1].strip().lower()
        for tag in gallery.get("tags") or []
        if str(tag).strip().lower().startswith("language:")
    }
    if not gallery_languages:
        return True
    return bool(gallery_languages & languages)


def gallery_matches_positive_bootstrap(gallery: dict, bootstrap: dict[str, float]) -> bool:
    positive_bootstrap = {tag: weight for tag, weight in bootstrap.items() if float(weight) > 0}
    if not positive_bootstrap:
        return False
    searchable = bootstrap_search_text(gallery)
    exact_values = bootstrap_exact_values(gallery)
    return any(bootstrap_matches(tag, searchable, exact_values) for tag in positive_bootstrap)


def mix_bootstrap_exploration(
    scored: list[dict],
    limit: int,
    count: int,
    bootstrap_queries: set[str],
    seed: str | None = None,
) -> list[dict]:
    if count <= 0 or limit <= 1 or not bootstrap_queries:
        return scored
    keep_count = max(1, limit - count)
    protected = scored[:keep_count]
    pool = [
        item
        for item in scored[keep_count:]
        if normalize_source_query(item.get("source_query")) in bootstrap_queries
        and float(item.get("score") or 0) >= MIN_BOOTSTRAP_EXPLORE_SCORE
        and has_bootstrap_score_reason(item)
    ]
    rng = random.Random(str(seed)) if seed else random.Random()
    rng.shuffle(pool)
    selected = []
    selected_urls = set()
    for item in pool:
        selected_item = dict(item)
        selected_item["reasons"] = ["bootstrap explore", *item.get("reasons", [])][:5]
        selected.append(selected_item)
        selected_urls.add(item["url"])
        if len(selected) >= count:
            break
    if not selected:
        return scored
    remainder = [item for item in scored[keep_count:] if item["url"] not in selected_urls]
    return [*protected, *selected, *remainder]


def has_bootstrap_score_reason(item: dict) -> bool:
    return any(str(reason).startswith("bootstrap ") for reason in item.get("reasons") or [])


def bootstrap_source_queries(bootstrap: dict[str, float]) -> set[str]:
    return {
        format_bootstrap_source_query(tag)
        for tag, weight in bootstrap.items()
        if weight > 0
    }


def format_bootstrap_source_query(tag: str) -> str:
    value = str(tag or "").strip().lower()
    if ":" not in value:
        return value
    namespace, body = value.split(":", 1)
    if " " in body:
        return f'{namespace}:"{body}"'
    return value


def normalize_source_query(query: object) -> str:
    return str(query or "").strip().lower()


def reaction_history_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.id AS feedback_id, f.vote AS user_vote, f.score AS user_score,
               f.note AS feedback_note, f.created_at AS feedback_created_at
        FROM feedback f
        JOIN (
            SELECT gallery_url, MAX(id) AS latest_id
            FROM feedback
            GROUP BY gallery_url
        ) latest ON latest.gallery_url = f.gallery_url AND latest.latest_id = f.id
        JOIN galleries g ON g.url = f.gallery_url
        ORDER BY f.id DESC
        LIMIT 10000
        """
    ).fetchall()

    items = []
    for row in rows:
        gallery = dict(row)
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
        gallery["samples"] = json.loads(gallery.pop("samples_json", None) or "[]")
        gallery["visual_embedding"] = parse_visual_embedding(gallery.pop("visual_embedding_json", None))
        gallery["visual_embedding_version"] = gallery.get("visual_embedding_version")
        gallery["visual_ready"] = bool(gallery["visual_embedding"])
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        score, reasons = score_gallery(gallery, bootstrap, weights, visual_model=visual_model, tag_strengths=tag_strengths)
        gallery.pop("visual_embedding", None)
        gallery["user_vote"] = round(float(gallery.get("user_vote", 0) or 0), 3)
        gallery["rated"] = True
        if gallery["user_vote"] < 0:
            score -= 2.0
            reasons.append("previous downvote")
        elif gallery["user_vote"] > 0:
            score += 0.5
            reasons.append("previous upvote")
        gallery["score"] = round(score, 3)
        gallery["reasons"] = reasons[:5]
        items.append(gallery)

    page_items = items[offset : offset + limit]
    next_offset = offset + len(page_items)
    return {
        "items": page_items,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "total": len(items),
        "has_more": next_offset < len(items),
    }


def diversify_ranked_galleries(scored: list[dict]) -> list[dict]:
    if len(scored) <= 2:
        return scored
    remaining = list(scored)
    selected: list[dict] = []
    seen: dict[str, int] = {}
    while remaining:
        best_index = 0
        best_score = None
        best_penalty = 0.0
        for index, item in enumerate(remaining):
            penalty = diversity_penalty(item, seen)
            adjusted = float(item["score"]) - penalty
            if best_score is None or adjusted > best_score:
                best_index = index
                best_score = adjusted
                best_penalty = penalty
        item = remaining.pop(best_index)
        if best_penalty:
            item = dict(item)
            item["score"] = round(float(item["score"]) - best_penalty, 3)
            item["reasons"] = [*item.get("reasons", []), f"diversity -{best_penalty:.2f}"][:5]
        selected.append(item)
        for key in diversity_keys(item):
            seen[key] = seen.get(key, 0) + 1
    return selected


def diversity_penalty(item: dict, seen: dict[str, int]) -> float:
    repeats = sum(seen.get(key, 0) for key in diversity_keys(item))
    return min(1.5, repeats * DIVERSITY_PENALTY)


def diversity_keys(gallery: dict) -> list[str]:
    keys: list[str] = []
    uploader = str(gallery.get("uploader") or "").strip().lower()
    if uploader:
        keys.append(f"uploader:{uploader}")
    for tag in gallery.get("tags") or []:
        tag = str(tag).strip().lower()
        if ":" not in tag:
            continue
        namespace = tag.split(":", 1)[0]
        if namespace in {"artist", "group", "parody", "character"}:
            keys.append(f"tag:{tag}")
    return sorted(set(keys))


def gallery_matches_filter(gallery: dict, filter_text: str) -> bool:
    haystack = " ".join(
        [
            str(gallery.get("title") or ""),
            str(gallery.get("category") or ""),
            str(gallery.get("uploader") or ""),
            " ".join(str(tag) for tag in gallery.get("tags") or []),
        ]
    ).lower()
    return all(part in haystack for part in filter_text.split())


def freshness_bonus(index: int, candidate_limit: int) -> float:
    return max(0.0, 1.0 - index / candidate_limit) * 0.25


def score_gallery(
    gallery: dict,
    bootstrap: dict[str, float],
    weights: dict[str, float],
    visual_model: dict | None = None,
    tag_strengths: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    searchable = bootstrap_search_text(gallery)
    exact_bootstrap_values = bootstrap_exact_values(gallery)

    for tag, weight in bootstrap.items():
        if bootstrap_matches(tag, searchable, exact_bootstrap_values):
            score += weight
            reasons.append(f"bootstrap {tag} {weight:+g}")

    feature_hits = []
    for feature, strength in gallery_feature_values(gallery, tag_strengths=tag_strengths):
        weight = weights.get(feature, 0.0)
        if weight:
            adjusted_weight = weight * strength
            score += adjusted_weight
            feature_hits.append((feature, adjusted_weight))

    feature_hits.sort(key=lambda item: abs(item[1]), reverse=True)
    for feature, weight in feature_hits[:3]:
        reasons.append(f"learned {feature} {weight:+.2f}")

    visual_score = score_visual_similarity(gallery, visual_model)
    if visual_score:
        score += visual_score
        reasons.append(f"visual {visual_score:+.2f}")

    rating = gallery.get("rating")
    if isinstance(rating, (int, float)) and not math.isnan(rating):
        bonus = min(max((float(rating) - 3.0) * 0.2, -0.3), 0.4)
        score += bonus
        if bonus:
            reasons.append(f"rating {bonus:+.2f}")

    if not reasons:
        reasons.append("recent")
    return score, reasons


def score_visual_gallery(gallery: dict, visual_model: dict | None) -> tuple[float, list[str]] | tuple[None, list[str]]:
    visual_score = score_visual_similarity(gallery, visual_model)
    if visual_score == 0.0:
        return None, []
    return visual_score, [f"visual only {visual_score:+.2f}"]


def score_visual_similarity(gallery: dict, visual_model: dict | None) -> float:
    if not visual_model:
        return 0.0
    if gallery.get("visual_embedding_version") != visual_model.get("version"):
        return 0.0
    embedding = gallery.get("visual_embedding")
    if not embedding:
        embedding = parse_visual_embedding(gallery.get("visual_embedding_json"))
    model_vector = visual_model.get("vector") if isinstance(visual_model, dict) else None
    if not isinstance(embedding, list) or not isinstance(model_vector, list) or len(embedding) != len(model_vector):
        return 0.0
    similarity = sum(float(left) * float(right) for left, right in zip(embedding, model_vector))
    confidence = min(1.0, max(0.35, float(visual_model.get("total_weight") or 0) / 3.0))
    return round(similarity * VISUAL_SCORE_SCALE * confidence, 3)


def bootstrap_matches(tag: str, searchable: str, exact_values: set[str]) -> bool:
    tag = str(tag).strip().lower()
    if ":" in tag and tag.split(":", 1)[0] in BOOTSTRAP_NAMESPACES:
        return tag in exact_values
    return plain_bootstrap_matches(tag, searchable)


def plain_bootstrap_matches(term: str, searchable: str) -> bool:
    if not term:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, searchable) is not None


def bootstrap_exact_values(gallery: dict) -> set[str]:
    values: set[str] = set()
    category = str(gallery.get("category") or "").strip().lower()
    if category:
        values.add(f"category:{category}")
    uploader = str(gallery.get("uploader") or "").strip().lower()
    if uploader:
        values.add(f"uploader:{uploader}")
    for tag in gallery.get("tags") or []:
        tag = str(tag).strip().lower()
        if tag:
            values.add(tag)
    return values


def bootstrap_search_text(gallery: dict) -> str:
    values = [str(gallery.get("title") or "")]
    category = str(gallery.get("category") or "").strip()
    if category:
        values.extend([category, f"category:{category}"])
    uploader = str(gallery.get("uploader") or "").strip()
    if uploader:
        values.extend([uploader, f"uploader:{uploader}"])
    values.extend(str(tag) for tag in gallery.get("tags") or [])
    return " ".join(value.lower() for value in values if value)


def model_snapshot(conn: sqlite3.Connection) -> dict:
    visual_model = visual_preference_model(conn)
    visual_counts = visual_version_counts(conn)
    return {
        "bootstrap_tags": get_bootstrap_tags(conn),
        "learned_queries": learned_query_tags(conn, limit=12),
        "top_weights": [
            dict(row)
            for row in conn.execute(
                """
                SELECT feature, weight, positive_count, negative_count
                FROM feature_weights
                ORDER BY ABS(weight) DESC
                LIMIT 25
                """
            )
        ],
        "positive_weights": model_weight_rows(conn, "weight > 0", "weight DESC"),
        "negative_weights": model_weight_rows(conn, "weight < 0", "weight ASC"),
        "visual": {
            "version": VISUAL_EMBEDDING_VERSION,
            "fallback_version": FALLBACK_VISUAL_EMBEDDING_VERSION,
            "active_version": None if not visual_model else visual_model["version"],
            "embedded_galleries": conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM galleries
                WHERE visual_embedding_json IS NOT NULL AND visual_embedding_json != ''
                """
            ).fetchone()["c"],
            "rated_embedded_galleries": 0 if not visual_model else visual_model["rated_count"],
            "positive_count": 0 if not visual_model else visual_model["positive_count"],
            "negative_count": 0 if not visual_model else visual_model["negative_count"],
            "versions": visual_counts,
            "ready": bool(visual_model),
        },
        "counts": {
            "galleries": conn.execute("SELECT COUNT(*) AS c FROM galleries").fetchone()["c"],
            "feedback_events": conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"],
            "rated_galleries": conn.execute("SELECT COUNT(DISTINCT gallery_url) AS c FROM feedback").fetchone()["c"],
            "model_features": conn.execute("SELECT COUNT(*) AS c FROM feature_weights").fetchone()["c"],
        },
    }


def visual_version_counts(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT visual_embedding_version AS version, COUNT(*) AS count
            FROM galleries
            WHERE visual_embedding_json IS NOT NULL AND visual_embedding_json != ''
            GROUP BY visual_embedding_version
            ORDER BY count DESC, version ASC
            """
        )
    ]


def model_weight_rows(conn: sqlite3.Connection, where_clause: str, order_clause: str, limit: int = 12) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT feature, weight, positive_count, negative_count
            FROM feature_weights
            WHERE {where_clause}
            ORDER BY {order_clause}, feature ASC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def as_gallery_dict(gallery: Gallery) -> dict:
    data = asdict(gallery)
    data["tags"] = list(data["tags"])
    return data
