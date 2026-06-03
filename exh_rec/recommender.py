from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import urllib.parse
from dataclasses import asdict

from .exhentai import Gallery


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_:+.-]{1,}", re.I)
LEARNING_RATE = 0.35
MAX_FEEDBACK_SIGNAL = 1.0
DIVERSITY_PENALTY = 0.45
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
        existing = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery.url,)).fetchone()
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, category, uploader, posted_at, thumb_url,
                rating, tags_json, source_query, detail_fetched_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                source_query = COALESCE(excluded.source_query, galleries.source_query),
                detail_fetched_at = COALESCE(excluded.detail_fetched_at, galleries.detail_fetched_at),
                last_seen_at = CURRENT_TIMESTAMP
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
                gallery.source_query,
                None,
            ),
        )
        if detail_fetched:
            conn.execute("UPDATE galleries SET detail_fetched_at = CURRENT_TIMESTAMP WHERE url = ?", (gallery.url,))
        if not existing:
            count += 1
    return count


def gallery_features(gallery: dict) -> list[str]:
    features: list[str] = []
    category = (gallery.get("category") or "").strip().lower()
    if category:
        features.append(f"category:{category}")
    uploader = (gallery.get("uploader") or "").strip().lower()
    if uploader:
        features.append(f"uploader:{uploader}")
    for tag in gallery.get("tags") or []:
        norm = str(tag).strip().lower()
        if norm:
            features.append(f"tag:{norm}")
    for token in TOKEN_RE.findall(gallery.get("title") or ""):
        token = token.lower().strip("_:+.-")
        if len(token) >= 3 and not token.isdigit():
            features.append(f"title:{token}")
    return sorted(set(features))


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
                       rating, tags_json, source_query, detail_fetched_at, first_seen_at, last_seen_at
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

    galleries = payload.get("galleries") or []
    imported_galleries = 0
    for gallery in galleries:
        gallery_url = str(gallery.get("url") or "").strip()
        if not gallery_url:
            continue
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, category, uploader, posted_at, thumb_url,
                rating, tags_json, source_query, detail_fetched_at, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
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
                gallery.get("rating"),
                gallery.get("tags_json") or "[]",
                gallery.get("source_query"),
                gallery.get("detail_fetched_at"),
                gallery.get("first_seen_at"),
                gallery.get("last_seen_at"),
            ),
        )
        imported_galleries += 1

    imported_tags = 0
    for tag in payload.get("bootstrap_tags") or []:
        value = normalize_bootstrap_value(str(tag.get("tag") or "").strip().lower())
        if not value:
            continue
        conn.execute(
            """
            INSERT INTO bootstrap_tags(tag, weight, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tag) DO UPDATE SET
                weight = excluded.weight,
                updated_at = CURRENT_TIMESTAMP
            """,
            (value, float(tag.get("weight", 1.0))),
        )
        imported_tags += 1

    imported_feedback = 0
    for item in payload.get("feedback") or []:
        gallery_url = item.get("gallery_url")
        if not gallery_url:
            continue
        exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not exists:
            continue
        conn.execute(
            """
            INSERT INTO feedback(gallery_url, vote, score, note, created_at)
            VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                gallery_url,
                float(item.get("vote", 0)),
                item.get("score"),
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


def retrain_model(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM feature_weights")
    rows = conn.execute(
        """
        SELECT g.*, f.vote AS feedback_signal
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
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        apply_feedback_features(conn, gallery, signal)


def apply_feedback_features(conn: sqlite3.Connection, gallery: dict, signal: float) -> None:
    if signal == 0:
        return
    for feature in gallery_features(gallery):
        weighted_signal = signal * LEARNING_RATE * feature_learning_multiplier(feature)
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
) -> list[dict]:
    return recommend_page(
        conn,
        limit=limit,
        include_rated=include_rated,
        offset=offset,
        filter_text=filter_text,
        candidate_limit=candidate_limit,
    )["items"]


def recommend_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    include_rated: bool = False,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
) -> dict:
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    candidate_limit = 10000 if filter_text else min(10000, max(100, int(candidate_limit)))
    candidate_limit = max(limit + offset, candidate_limit)
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
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
        score, reasons = score_gallery(gallery, bootstrap, weights)
        gallery["user_vote"] = round(float(gallery.get("user_vote", 0) or 0), 3)
        gallery["rated"] = gallery.get("feedback_id") is not None
        if gallery["rated"] and not include_rated:
            continue
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        if gallery["user_vote"] < 0:
            score -= 2.0
            reasons.append("previous downvote")
        elif gallery["user_vote"] > 0:
            score += 0.5
            reasons.append("previous upvote")
        score += max(0.0, 1.0 - idx / candidate_limit) * 0.25
        gallery["score"] = round(score, 3)
        gallery["reasons"] = reasons[:5]
        scored.append(gallery)

    scored.sort(key=lambda item: item["score"], reverse=True)
    scored = diversify_ranked_galleries(scored)
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


def score_gallery(gallery: dict, bootstrap: dict[str, float], weights: dict[str, float]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    searchable = bootstrap_search_text(gallery)
    exact_bootstrap_values = bootstrap_exact_values(gallery)

    for tag, weight in bootstrap.items():
        if bootstrap_matches(tag, searchable, exact_bootstrap_values):
            score += weight
            reasons.append(f"bootstrap {tag} {weight:+g}")

    feature_hits = []
    for feature in gallery_features(gallery):
        weight = weights.get(feature, 0.0)
        if weight:
            score += weight
            feature_hits.append((feature, weight))

    feature_hits.sort(key=lambda item: abs(item[1]), reverse=True)
    for feature, weight in feature_hits[:3]:
        reasons.append(f"learned {feature} {weight:+.2f}")

    rating = gallery.get("rating")
    if isinstance(rating, (int, float)) and not math.isnan(rating):
        score += min(max((float(rating) - 3.0) * 0.2, -0.3), 0.4)

    if not reasons:
        reasons.append("recent")
    return score, reasons


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
        "counts": {
            "galleries": conn.execute("SELECT COUNT(*) AS c FROM galleries").fetchone()["c"],
            "feedback_events": conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"],
            "rated_galleries": conn.execute("SELECT COUNT(DISTINCT gallery_url) AS c FROM feedback").fetchone()["c"],
            "model_features": conn.execute("SELECT COUNT(*) AS c FROM feature_weights").fetchone()["c"],
        },
    }


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
