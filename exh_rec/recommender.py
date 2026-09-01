from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import asdict

from .db import snapshot_gallery_features, visual_embedding_digest
from .exhentai import Gallery
from .visual import DINOV2_VISUAL_VERSION, SIMPLE_VISUAL_VERSION, normalize_embedding
from .personalized import (
    hath_download_signal_weight,
    model_public_status,
    load_personalized_model,
    normalize_reason_code,
    normalize_surface,
    positive_query_tags,
    score_personalized_galleries,
    train_personalized_model,
)
from .classification import (
    continuing_classifier_decisions,
    load_continuing_classifier,
    public_classifier_status,
    train_continuing_classifier,
)


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_:+.-]{1,}", re.I)
LEARNING_RATE = 0.35
THUMB_FEEDBACK_SIGNAL = 1.0
SCORE_FEEDBACK_MULTIPLIER = 1.5
FAVORITE_MARK_SIGNAL = 3.0
BAN_MARK_SIGNAL = -3.0
MARK_KINDS = {"favorite", "ban"}
MAX_FEEDBACK_SIGNAL = FAVORITE_MARK_SIGNAL
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
IDENTITY_TAG_NAMESPACES = {"artist", "group", "cosplayer"}
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
MODEL_MODE_LEGACY = "legacy"
INTEREST_BAND_ALL = "all"
INTEREST_BAND_PRIMARY = "primary"
INTEREST_BAND_LOW = "low"
DEFAULT_LOW_INTEREST_MAX_PERCENT = 35
RETRAIN_LOCK = threading.RLock()
SCAN_CACHE_LOCK = threading.RLock()
SCAN_CACHE_TTL_SECONDS = 30.0
SCAN_CACHE: dict[tuple[str, str], tuple[tuple, float, object]] = {}
MODEL_MODES = {MODEL_MODE_HYBRID, MODEL_MODE_VISUAL, MODEL_MODE_LEGACY}
INTEREST_BANDS = {INTEREST_BAND_ALL, INTEREST_BAND_PRIMARY, INTEREST_BAND_LOW}
SHORT_REPEAT_PAGE_LIMIT = 10
RELATED_FEEDBACK_REFERENCE_LIMIT = 5
SHORT_REPEAT_IDENTITY_NAMESPACES = {"artist"}
CONTINUING_SOURCE_RE = re.compile(
    r"(?:^|[\[\(【「『\s])(?:pixiv|fanbox|patreon|fantia|twitter|x\.com)(?:[\]\)】」』\s]|$)",
    re.I,
)
CONTINUING_EXPLICIT_RE = re.compile(
    r"\b(?:archive|collection|imageset|image\s*set|ongoing|monthly\s+(?:pack|set|archive|collection))\b",
    re.I,
)
CONTINUING_STANDALONE_RE = re.compile(r"\bongoing\b", re.I)
CONTINUING_DATE_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?\s*(?:~|～|—|-|to)\s*(?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?",
    re.I,
)
CONTINUING_TRAILING_DATE_RE = re.compile(r"\s+(?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?\s*$")
SOURCE_PREFIX_RE = re.compile(r"^\s*((?:[\[\(【「『][^\]\)】」』]{1,50}[\]\)】」』]\s*)+)")
SOURCE_LABEL_RE = re.compile(r"[\[\(【「『]\s*([^\]\)】」』]{1,50})\s*[\]\)】」』]")
TITLE_ARTIST_ID_RE = re.compile(r"^\s*(?P<name>.+?)\s*[\(（](?P<artist_id>\d{4,})[\)）]\s*$")
PARENT_GALLERY_RE = re.compile(r"(?:https?:)?(?://(?:exhentai|e-hentai)\.org)?/g/(\d+)/([0-9a-fA-F]+)/?")


def _database_identity(conn: sqlite3.Connection) -> str:
    row = next((row for row in conn.execute("PRAGMA database_list") if row[1] == "main"), None)
    path = str(row[2] or "") if row else ""
    return path or f"memory:{id(conn)}"


def _cached_scan(conn: sqlite3.Connection, name: str, fingerprint: tuple, builder):
    identity = _database_identity(conn)
    if identity.startswith("memory:"):
        return builder()
    key = (name, identity)
    with SCAN_CACHE_LOCK:
        cached = SCAN_CACHE.get(key)
        if cached and cached[0] == fingerprint and time.monotonic() - cached[1] < SCAN_CACHE_TTL_SECONDS:
            return cached[2]
    value = builder()
    with SCAN_CACHE_LOCK:
        SCAN_CACHE[key] = (fingerprint, time.monotonic(), value)
    return value


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
    personalized = load_personalized_model(conn, train_if_needed=False, allow_stale=True)
    model_tags = positive_query_tags(personalized, limit=limit)
    if model_tags:
        negative_bootstrap = {
            row["tag"] for row in conn.execute("SELECT tag FROM bootstrap_tags WHERE weight < 0")
        }
        return [tag for tag in model_tags if tag not in negative_bootstrap][:limit]
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


def store_galleries(
    conn: sqlite3.Connection,
    galleries: list[Gallery],
    detail_fetched: bool = False,
    review_excluded: bool = False,
) -> int:
    count = 0
    for gallery in galleries:
        tag_weights_json = json.dumps(normalize_tag_weights(gallery.tag_weights), ensure_ascii=True)
        existing = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery.url,)).fetchone()
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, title_jpn, category, uploader, posted_at, thumb_url,
                rating, tags_json, tag_weights_json, source_query, parent_url, review_excluded,
                detail_fetched_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                title_jpn = COALESCE(excluded.title_jpn, galleries.title_jpn),
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
                parent_url = COALESCE(excluded.parent_url, galleries.parent_url),
                review_excluded = MIN(galleries.review_excluded, excluded.review_excluded),
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
                gallery.title_jpn,
                gallery.category,
                gallery.uploader,
                gallery.posted_at,
                gallery.thumb_url,
                gallery.rating,
                json.dumps(gallery.tags, ensure_ascii=True),
                tag_weights_json,
                gallery.source_query,
                gallery.parent_url,
                1 if review_excluded else 0,
                None,
                1 if detail_fetched else 0,
            ),
        )
        if detail_fetched:
            conn.execute("UPDATE galleries SET detail_fetched_at = CURRENT_TIMESTAMP WHERE url = ?", (gallery.url,))
        snapshot_gallery_features(
            conn,
            gallery.url,
            "detail" if detail_fetched else "listing",
        )
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
    snapshot_gallery_features(conn, url, "samples")


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
    serialized = json.dumps(normalized, ensure_ascii=True)
    exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not exists:
        raise ValueError("Gallery not found")
    conn.execute(
        """
        UPDATE galleries
        SET visual_embedding_json = ?,
            visual_embedding_digest = ?,
            visual_embedding_version = ?,
            visual_embedding_at = CURRENT_TIMESTAMP
        WHERE url = ?
        """,
        (serialized, visual_embedding_digest(serialized), version, gallery_url),
    )
    snapshot_gallery_features(conn, gallery_url, "visual")


def store_visual_image_embeddings(
    conn: sqlite3.Connection,
    gallery_url: str,
    images: list[dict],
    version: str = VISUAL_EMBEDDING_VERSION,
) -> int:
    exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not exists:
        raise ValueError("Gallery not found")
    normalized_images = []
    for index, image in enumerate(images[:12]):
        if not isinstance(image, dict):
            continue
        image_url = str(image.get("image_url") or "").strip() or None
        image_key = str(image.get("image_key") or "").strip()
        if not image_key:
            identity = image_url or f"image:{index}"
            image_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        normalized_images.append((image_key[:120], image_url, normalize_visual_embedding(image.get("embedding"))))
    if not normalized_images:
        return 0
    conn.execute(
        "DELETE FROM gallery_visual_images WHERE gallery_url = ? AND embedding_version = ?",
        (gallery_url, version),
    )
    conn.executemany(
        """
        INSERT INTO gallery_visual_images(
            gallery_url, image_key, image_url, embedding_json, embedding_version, created_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            (gallery_url, image_key, image_url, json.dumps(embedding, ensure_ascii=True), version)
            for image_key, image_url, embedding in normalized_images
        ],
    )
    return len(normalized_images)


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
    for title_value in gallery_title_values(gallery):
        for token in TOKEN_RE.findall(title_value):
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
    reason_code: str | None = None,
    surface: str | None = None,
    retrain: bool = True,
) -> bool:
    # A vote needs its own baseline even when the gallery has not changed since
    # the previous fetch; later Updates filtering compares growth to this row.
    snapshot_gallery_features(conn, gallery_url, "feedback", force=True)
    previous = conn.execute(
        """
        SELECT vote
        FROM feedback
        WHERE gallery_url = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (gallery_url,),
    ).fetchone()
    previous_signal = float(previous["vote"] or 0) if previous else 0.0
    signal = feedback_signal(vote=vote, score=score)
    normalized_reason = normalize_reason_code(reason_code)
    if normalized_reason and signal >= 0:
        raise ValueError("reason_code is only valid for negative feedback")
    conn.execute(
        "INSERT INTO feedback(gallery_url, vote, score, note, reason_code, surface) VALUES (?, ?, ?, ?, ?, ?)",
        (gallery_url, signal, score, note, normalized_reason, normalize_surface(surface)),
    )
    model_invalidated = signal != 0 or previous_signal != 0
    if model_invalidated and retrain:
        retrain_model(conn)
    return model_invalidated


def clear_feedback(conn: sqlite3.Connection, gallery_url: str, retrain: bool = True) -> int:
    cursor = conn.execute("DELETE FROM feedback WHERE gallery_url = ?", (gallery_url,))
    if cursor.rowcount and retrain:
        retrain_model(conn)
    return cursor.rowcount


def record_gallery_mark(
    conn: sqlite3.Connection,
    gallery_url: str,
    kind: str,
    note: str | None = None,
    retrain: bool = True,
) -> bool:
    snapshot_gallery_features(conn, gallery_url, "mark")
    kind = normalize_mark_kind(kind)
    existing = conn.execute("SELECT kind FROM gallery_marks WHERE gallery_url = ?", (gallery_url,)).fetchone()
    if existing and existing["kind"] == kind:
        conn.execute(
            """
            UPDATE gallery_marks
            SET note = ?, updated_at = CURRENT_TIMESTAMP
            WHERE gallery_url = ?
            """,
            (note, gallery_url),
        )
    else:
        conn.execute(
            """
            INSERT INTO gallery_marks(gallery_url, kind, note, created_at, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(gallery_url) DO UPDATE SET
                kind = excluded.kind,
                note = excluded.note,
                updated_at = CURRENT_TIMESTAMP
            """,
            (gallery_url, kind, note),
        )
    model_invalidated = not existing or existing["kind"] != kind
    if model_invalidated and retrain:
        retrain_model(conn)
    return model_invalidated


def clear_gallery_mark(conn: sqlite3.Connection, gallery_url: str, retrain: bool = True) -> int:
    cursor = conn.execute("DELETE FROM gallery_marks WHERE gallery_url = ?", (gallery_url,))
    if cursor.rowcount and retrain:
        retrain_model(conn)
    return cursor.rowcount


def normalize_mark_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in MARK_KINDS:
        raise ValueError("mark kind must be favorite or ban")
    return value


def gallery_mark_signal(kind: str) -> float:
    kind = normalize_mark_kind(kind)
    return FAVORITE_MARK_SIGNAL if kind == "favorite" else BAN_MARK_SIGNAL


def reset_library(conn: sqlite3.Connection) -> dict[str, int]:
    """Wipe fetched galleries, votes, learned weights, and fetch history.

    Cookies and tuning live in ``settings`` and bootstrap tags in ``bootstrap_tags``,
    so both survive untouched.
    """
    removed: dict[str, int] = {}
    for table in (
        "hath_events", "hath_downloads", "hath_clients",
        "gallery_visual_images", "gallery_feature_snapshots", "recommendation_impressions", "model_training_runs",
        "gallery_classification_samples", "gallery_classification_overrides",
        "gallery_marks", "feedback", "feature_weights", "fetch_runs", "fetch_query_state", "galleries",
    ):
        cursor = conn.execute(f"DELETE FROM {table}")
        removed[table] = cursor.rowcount
    return removed


def set_classification_override(conn: sqlite3.Connection, gallery_url: str, classification: str) -> str:
    gallery_url = normalize_gallery_url(gallery_url)
    if not gallery_url:
        raise ValueError("valid gallery_url is required")
    classification = str(classification or "").strip().lower()
    if classification == "auto":
        conn.execute("DELETE FROM gallery_classification_overrides WHERE gallery_url = ?", (gallery_url,))
        train_continuing_classifier(conn)
        return "auto"
    if classification not in {"review", "updates"}:
        raise ValueError("classification must be review, updates, or auto")
    conn.execute(
        """
        INSERT INTO gallery_classification_overrides(gallery_url, classification, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(gallery_url) DO UPDATE SET
            classification = excluded.classification,
            updated_at = CURRENT_TIMESTAMP
        """,
        (gallery_url, classification),
    )
    train_continuing_classifier(conn)
    return classification


def classification_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        normalize_gallery_url(row["gallery_url"]): str(row["classification"])
        for row in conn.execute("SELECT gallery_url, classification FROM gallery_classification_overrides")
    }


def feedback_history(conn: sqlite3.Connection, gallery_url: str, limit: int = 25) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, gallery_url, vote, score, note, reason_code, surface, created_at
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
            SELECT gallery_url, vote, score, note, reason_code, surface, created_at
            FROM feedback
            ORDER BY id
            """
        )
    ]
    mark_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT gallery_url, kind, note, created_at, updated_at
            FROM gallery_marks
            ORDER BY updated_at, gallery_url
            """
        )
    ]
    classification_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT gallery_url, classification, updated_at
            FROM gallery_classification_overrides
            ORDER BY updated_at, gallery_url
            """
        )
    ]
    feedback_urls = [row["gallery_url"] for row in feedback_rows]
    marked_urls = [row["gallery_url"] for row in mark_rows]
    classified_urls = [row["gallery_url"] for row in classification_rows]
    gallery_urls = sorted(set([*feedback_urls, *marked_urls, *classified_urls]))
    galleries = []
    if gallery_urls:
        placeholders = ",".join("?" for _ in gallery_urls)
        galleries = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT url, gid, token, title, title_jpn, category, uploader, posted_at, thumb_url,
                       rating, tags_json, tag_weights_json, source_query, parent_url, detail_fetched_at, first_seen_at, last_seen_at
                FROM galleries
                WHERE url IN ({placeholders})
                ORDER BY url
                """,
                gallery_urls,
            )
        ]
    return {
        "schema": "exh-rec-preferences-v2",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "bootstrap_tags": get_bootstrap_tags(conn),
        "galleries": galleries,
        "feedback": feedback_rows,
        "gallery_marks": mark_rows,
        "classification_overrides": classification_rows,
    }


def import_preferences(conn: sqlite3.Connection, payload: dict, replace: bool = False) -> dict:
    if payload.get("schema") not in {"exh-rec-preferences-v1", "exh-rec-preferences-v2"}:
        raise ValueError("Unsupported preference export schema")
    if replace:
        conn.execute("DELETE FROM feedback")
        conn.execute("DELETE FROM gallery_marks")
        conn.execute("DELETE FROM bootstrap_tags")
        conn.execute("DELETE FROM gallery_classification_overrides")

    galleries = import_rows(payload, "galleries")
    imported_galleries = 0
    for gallery in galleries:
        gallery_url = str(gallery.get("url") or "").strip()
        if not gallery_url:
            continue
        conn.execute(
            """
            INSERT INTO galleries(
                url, gid, token, title, title_jpn, category, uploader, posted_at, thumb_url,
                rating, tags_json, tag_weights_json, source_query, parent_url, detail_fetched_at, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                title_jpn = COALESCE(excluded.title_jpn, galleries.title_jpn),
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
                parent_url = COALESCE(excluded.parent_url, galleries.parent_url),
                detail_fetched_at = COALESCE(excluded.detail_fetched_at, galleries.detail_fetched_at),
                last_seen_at = COALESCE(excluded.last_seen_at, galleries.last_seen_at)
            """,
            (
                gallery_url,
                gallery.get("gid"),
                gallery.get("token"),
                gallery.get("title") or "Imported gallery",
                gallery.get("title_jpn"),
                gallery.get("category"),
                gallery.get("uploader"),
                gallery.get("posted_at"),
                gallery.get("thumb_url"),
                import_gallery_rating(gallery.get("rating")),
                import_tags_json(gallery.get("tags_json")),
                import_tag_weights_json(gallery.get("tag_weights_json")),
                gallery.get("source_query"),
                gallery.get("parent_url"),
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
        if score is not None:
            vote = feedback_signal(score=score)
        else:
            vote = import_feedback_vote(item.get("vote")) if "vote" in item else None
        if vote is None:
            continue
        reason_code = normalize_reason_code(item.get("reason_code"))
        if vote >= 0:
            reason_code = None
        conn.execute(
            """
            INSERT INTO feedback(gallery_url, vote, score, note, reason_code, surface, created_at)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                gallery_url,
                vote,
                score,
                item.get("note"),
                reason_code,
                normalize_surface(item.get("surface")),
                item.get("created_at"),
            ),
        )
        imported_feedback += 1

    imported_marks = 0
    for item in import_rows(payload, "gallery_marks"):
        gallery_url = item.get("gallery_url")
        if not gallery_url:
            continue
        try:
            kind = normalize_mark_kind(str(item.get("kind") or ""))
        except ValueError:
            continue
        exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not exists:
            continue
        conn.execute(
            """
            INSERT INTO gallery_marks(gallery_url, kind, note, created_at, updated_at)
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(gallery_url) DO UPDATE SET
                kind = excluded.kind,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                gallery_url,
                kind,
                item.get("note"),
                item.get("created_at"),
                item.get("updated_at"),
            ),
        )
        imported_marks += 1

    imported_classifications = 0
    for item in import_rows(payload, "classification_overrides"):
        gallery_url = str(item.get("gallery_url") or "").strip()
        if not gallery_url:
            continue
        exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not exists:
            continue
        classification = str(item.get("classification") or "").strip().lower()
        if classification not in {"review", "updates"}:
            continue
        conn.execute(
            """
            INSERT INTO gallery_classification_overrides(gallery_url, classification, updated_at)
            VALUES (?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(gallery_url) DO UPDATE SET
                classification = excluded.classification,
                updated_at = excluded.updated_at
            """,
            (gallery_url, classification, item.get("updated_at")),
        )
        imported_classifications += 1

    retrain_model(conn)
    train_continuing_classifier(conn)
    return {
        "bootstrap_tags": imported_tags,
        "galleries": imported_galleries,
        "feedback": imported_feedback,
        "gallery_marks": imported_marks,
        "classification_overrides": imported_classifications,
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


def retrain_model(conn: sqlite3.Connection, release_write_lock: bool = False) -> dict:
    with RETRAIN_LOCK:
        return _retrain_model(conn, release_write_lock=release_write_lock)


def _retrain_model(conn: sqlite3.Connection, release_write_lock: bool = False) -> dict:
    conn.execute("DELETE FROM feature_weights")
    tag_strengths = tag_corpus_strengths(conn)
    confidence_by_id = feedback_confidence_index(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.id AS feedback_id, f.vote AS feedback_signal, f.score AS feedback_score
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
        score = gallery.pop("feedback_score")
        signal *= confidence_by_id.get(feedback_id, 1.0)
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
        apply_feedback_features(conn, gallery, signal, score=score, tag_strengths=tag_strengths)
    mark_rows = conn.execute(
        """
        SELECT g.*, m.kind AS mark_kind
        FROM gallery_marks m
        JOIN galleries g ON g.url = m.gallery_url
        """
    ).fetchall()
    for row in mark_rows:
        gallery = dict(row)
        signal = gallery_mark_signal(str(gallery.pop("mark_kind") or ""))
        gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
        apply_feedback_features(conn, gallery, signal, score=5 if signal > 0 else 1, tag_strengths=tag_strengths)
    download_signal = hath_download_signal_weight(conn)
    if download_signal > 0:
        download_rows = conn.execute(
            """
            SELECT g.*
            FROM hath_downloads d
            JOIN galleries g ON g.url = d.gallery_url
            WHERE d.status = 'completed'
              AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.gallery_url = d.gallery_url)
              AND NOT EXISTS (SELECT 1 FROM gallery_marks m WHERE m.gallery_url = d.gallery_url)
            GROUP BY d.gallery_url
            """
        ).fetchall()
        for row in download_rows:
            gallery = dict(row)
            gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
            gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
            apply_feedback_features(conn, gallery, download_signal, tag_strengths=tag_strengths)
    if release_write_lock:
        conn.commit()
    personalized = train_personalized_model(conn)
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES ('model_retrain_active_signature', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(personalized.get("signature") or ""),),
    )
    return personalized


def visual_preference_model(conn: sqlite3.Connection) -> dict | None:
    confidence_by_id = feedback_confidence_index(conn)
    feedback_rows = conn.execute(
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
    rows: list[dict] = []
    for row in feedback_rows:
        signal = float(row["feedback_signal"] or 0)
        signal *= confidence_by_id.get(int(row["feedback_id"]), 1.0)
        rows.append(
            {
                "url": row["url"],
                "visual_embedding_json": row["visual_embedding_json"],
                "visual_embedding_version": row["visual_embedding_version"],
                "signal": signal,
            }
        )
    mark_rows = conn.execute(
        """
        SELECT g.url, g.visual_embedding_json, g.visual_embedding_version, m.kind AS mark_kind
        FROM gallery_marks m
        JOIN galleries g ON g.url = m.gallery_url
        WHERE g.visual_embedding_json IS NOT NULL
          AND g.visual_embedding_json != ''
        """
    ).fetchall()
    for row in mark_rows:
        rows.append(
            {
                "url": row["url"],
                "visual_embedding_json": row["visual_embedding_json"],
                "visual_embedding_version": row["visual_embedding_version"],
                "signal": gallery_mark_signal(str(row["mark_kind"] or "")),
            }
        )
    download_signal = hath_download_signal_weight(conn)
    if download_signal > 0:
        download_rows = conn.execute(
            """
            SELECT g.url, g.visual_embedding_json, g.visual_embedding_version
            FROM hath_downloads d
            JOIN galleries g ON g.url = d.gallery_url
            WHERE d.status = 'completed'
              AND g.visual_embedding_json IS NOT NULL AND g.visual_embedding_json != ''
              AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.gallery_url = d.gallery_url)
              AND NOT EXISTS (SELECT 1 FROM gallery_marks m WHERE m.gallery_url = d.gallery_url)
            GROUP BY d.gallery_url
            """
        ).fetchall()
        for row in download_rows:
            rows.append(
                {
                    "url": row["url"],
                    "visual_embedding_json": row["visual_embedding_json"],
                    "visual_embedding_version": row["visual_embedding_version"],
                    "signal": download_signal,
                }
            )
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
        signal = float(row["signal"] or 0)
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


def active_visual_version(rows: list[sqlite3.Row] | list[dict]) -> str | None:
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


def feedback_confidence_index(conn: sqlite3.Connection) -> dict[int, float]:
    fingerprint_row = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM feedback"
    ).fetchone()
    fingerprint = (int(fingerprint_row["count"]), int(fingerprint_row["max_id"]))

    def build() -> dict[int, float]:
        rows = conn.execute(
            "SELECT id, gallery_url, vote FROM feedback ORDER BY gallery_url, id DESC"
        ).fetchall()
        result: dict[int, float] = {}
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["gallery_url"]), []).append(row)
        for gallery_rows in grouped.values():
            for index, row in enumerate(gallery_rows):
                vote = float(row["vote"] or 0.0)
                if vote == 0:
                    result[int(row["id"])] = 0.0
                    continue
                direction = 1 if vote > 0 else -1
                streak = 0
                for previous in gallery_rows[index : index + MAX_FEEDBACK_CONFIDENCE_HISTORY]:
                    previous_vote = float(previous["vote"] or 0.0)
                    if previous_vote == 0 or (1 if previous_vote > 0 else -1) != direction:
                        break
                    streak += 1
                boost = min(
                    MAX_FEEDBACK_CONFIDENCE_BOOST,
                    max(0, streak - 1) * FEEDBACK_CONFIDENCE_STEP,
                )
                result[int(row["id"])] = 1.0 + boost
        return result

    return _cached_scan(conn, "feedback-confidence", fingerprint, build)


def apply_feedback_features(
    conn: sqlite3.Connection,
    gallery: dict,
    signal: float,
    score: int | None = None,
    tag_strengths: dict[str, float] | None = None,
) -> None:
    if signal == 0:
        return
    for feature, strength in gallery_feature_values(gallery, tag_strengths=tag_strengths):
        if not feedback_updates_feature(feature, signal=signal, score=score):
            continue
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


def feedback_updates_feature(feature: str, signal: float, score: int | None = None) -> bool:
    if signal >= 0 or score is not None:
        return True
    return is_identity_feature(feature) or is_title_feature(feature)


def is_title_feature(feature: str) -> bool:
    return feature.startswith("title:")


def is_identity_feature(feature: str) -> bool:
    if feature.startswith("tag:"):
        tag = feature[4:]
        namespace = tag_namespace(tag)
        return namespace in IDENTITY_TAG_NAMESPACES
    return feature.startswith("uploader:")


def tag_corpus_strengths(conn: sqlite3.Connection) -> dict[str, float]:
    fingerprint_row = conn.execute(
        """
        SELECT COUNT(*) AS count, COALESCE(SUM(LENGTH(tags_json)), 0) AS tag_bytes,
               COALESCE(MAX(last_seen_at), '') AS last_seen
        FROM galleries
        """
    ).fetchone()
    fingerprint = (
        int(fingerprint_row["count"]),
        int(fingerprint_row["tag_bytes"]),
        str(fingerprint_row["last_seen"]),
    )
    return _cached_scan(conn, "tag-corpus-strengths", fingerprint, lambda: _build_tag_corpus_strengths(conn))


def _build_tag_corpus_strengths(conn: sqlite3.Connection) -> dict[str, float]:
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
        return round(((bounded - 3) / 2) * SCORE_FEEDBACK_MULTIPLIER, 3)
    if vote is None:
        raise ValueError("vote or score is required")
    if vote == 0:
        return 0.0
    return THUMB_FEEDBACK_SIGNAL if vote > 0 else -THUMB_FEEDBACK_SIGNAL


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
    posted_after: str | None = None,
    low_interest_percent: int = 20,
    low_interest_auto_threshold: bool = True,
    low_interest_max_percent: int = DEFAULT_LOW_INTEREST_MAX_PERCENT,
    interest_band: str = INTEREST_BAND_ALL,
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
        posted_after=posted_after,
        low_interest_percent=low_interest_percent,
        low_interest_auto_threshold=low_interest_auto_threshold,
        low_interest_max_percent=low_interest_max_percent,
        interest_band=interest_band,
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
    posted_after: str | None = None,
    exclude_short_repeats: bool = True,
    continuing_updates: dict[str, dict] | None = None,
    personalized_artifact: dict | None = None,
    low_interest_percent: int = 20,
    low_interest_auto_threshold: bool = True,
    low_interest_max_percent: int = DEFAULT_LOW_INTEREST_MAX_PERCENT,
    interest_band: str = INTEREST_BAND_ALL,
) -> dict:
    limit = max(1, min(10000, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    candidate_limit = 10000 if filter_text else min(10000, max(100, int(candidate_limit)))
    candidate_limit = max(limit + offset, candidate_limit)
    freshness_weight = max(0.0, min(50.0, float(freshness_weight)))
    bootstrap_explore_count = max(0, min(limit - 1, int(bootstrap_explore_count)))
    language_filter_values = normalize_language_filter(language_filter)
    model_mode = normalize_model_mode(model_mode)
    posted_after = normalize_posted_after(posted_after)
    low_interest_percent = max(0, min(50, int(low_interest_percent)))
    low_interest_max_percent = max(
        low_interest_percent,
        min(90, max(0, int(low_interest_max_percent))),
    )
    interest_band = normalize_interest_band(interest_band)
    if model_mode == MODEL_MODE_VISUAL:
        bootstrap_explore_count = 0
        require_bootstrap_match = False
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    short_repeat_reference_index = (
        related_feedback_reference_index(conn) if exclude_short_repeats and not include_rated else {}
    )
    if continuing_updates is None:
        continuing_updates = continuing_update_series_index(conn) if exclude_short_repeats and not include_rated else {}
    overrides = classification_overrides(conn)
    unrated_where = "AND f.feedback_id IS NULL AND m.kind IS NULL AND hd.gallery_url IS NULL" if not include_rated else ""
    rows = conn.execute(
        f"""
        SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote,
               m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at,
               hd.gallery_url AS downloaded_gallery_url
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
        LEFT JOIN gallery_marks m ON m.gallery_url = g.url
        LEFT JOIN (
            SELECT gallery_url
            FROM hath_downloads
            WHERE status = 'completed' AND gallery_url IS NOT NULL
            GROUP BY gallery_url
        ) hd ON hd.gallery_url = g.url
        WHERE g.review_excluded = 0
        {unrated_where}
        ORDER BY g.last_seen_at DESC
        LIMIT ?
        """,
        (candidate_limit,),
    ).fetchall()

    scored = []
    personalized_inputs: list[dict] = []
    classifier_inputs = [gallery_item_from_row(row) for row in rows]
    classifier_decisions, classifier_status = continuing_classifier_decisions(conn, classifier_inputs)
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
        personalized_gallery = dict(gallery) if model_mode == MODEL_MODE_HYBRID else None
        gallery.pop("visual_embedding", None)
        gallery["user_vote"] = round(float(gallery.get("user_vote", 0) or 0), 3)
        gallery["user_mark_kind"] = gallery.get("user_mark_kind")
        gallery["marked"] = gallery["user_mark_kind"] in MARK_KINDS
        gallery["rated"] = gallery.get("feedback_id") is not None or gallery["marked"]
        continuing = continuing_updates.get(gallery.get("url"))
        manual_classification = overrides.get(normalize_gallery_url(gallery.get("url")))
        gallery["classification_override"] = manual_classification
        learned_classification = classifier_decisions.get(gallery.get("url")) if not manual_classification else None
        gallery["classification_prediction"] = learned_classification
        if not include_rated and (manual_classification == "updates" or (
            learned_classification and learned_classification.get("classification") == "updates"
        )):
            continue
        if exclude_short_repeats and not include_rated and continuing and manual_classification != "review":
            if continuing["reviewed"] or gallery.get("url") != continuing["latest_url"]:
                continue
        if (
            exclude_short_repeats and not include_rated and manual_classification != "review"
            and is_short_repeat_gallery(gallery, short_repeat_reference_index)
        ):
            continue
        if gallery["rated"] and not include_rated:
            continue
        if not gallery_matches_language_filter(gallery, language_filter_values):
            continue
        if require_bootstrap_match and not gallery_matches_positive_bootstrap(gallery, bootstrap):
            continue
        if posted_after and not gallery_posted_on_or_after(gallery, posted_after):
            continue
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        if model_mode != MODEL_MODE_VISUAL:
            if gallery["user_mark_kind"] == "ban":
                score -= 5.0
                reasons.append("banned")
            elif gallery["user_mark_kind"] == "favorite":
                score += 2.5
                reasons.append("favorite")
            elif gallery["user_vote"] < 0:
                score -= 2.0
                reasons.append("previous downvote")
            elif gallery["user_vote"] > 0:
                score += 0.5
                reasons.append("previous upvote")
        if model_mode != MODEL_MODE_VISUAL:
            freshness = freshness_bonus(idx, candidate_limit) * freshness_weight
            score += freshness
            gallery["freshness_bonus"] = round(freshness, 6)
            if freshness and reasons != ["recent"]:
                freshness_reason = f"fresh {freshness:+.2f}"
                if freshness_weight > 1.0:
                    reasons.insert(0, freshness_reason)
                else:
                    reasons.append(freshness_reason)
        gallery["score"] = round(score, 3)
        if model_mode == MODEL_MODE_VISUAL:
            gallery["score_scale"] = "similarity"
        gallery["reasons"] = reasons[:5]
        if personalized_gallery is not None:
            personalized_inputs.append(personalized_gallery)
        scored.append(gallery)

    personalized_status: dict = {"ready": False, "status": "not-requested"}
    active_personalized_artifact = personalized_artifact or {}
    if model_mode == MODEL_MODE_HYBRID and personalized_inputs:
        if personalized_artifact is None:
            predictions, active_personalized_artifact = score_personalized_galleries(conn, personalized_inputs)
        else:
            predictions, active_personalized_artifact = score_personalized_galleries(
                conn, personalized_inputs, artifact=personalized_artifact
            )
        personalized_status = model_public_status(active_personalized_artifact)
        if (
            active_personalized_artifact.get("ready")
            and active_personalized_artifact.get("accepted")
            and len(predictions) == len(scored)
        ):
            for gallery, prediction in zip(scored, predictions):
                probability = float(prediction["like_probability"])
                prior_rank_score = apply_bootstrap_probability_prior(
                    float(prediction["like_probability"]), gallery, bootstrap
                )
                freshness_offset = min(0.5, max(0.0, float(gallery.get("freshness_bonus") or 0.0)) * 0.1)
                prediction["like_probability"] = round(probability, 6)
                prediction["rank_score"] = round(
                    apply_probability_logit_offset(prior_rank_score, freshness_offset),
                    6,
                )
                gallery.update(prediction)
                gallery["score"] = gallery["rank_score"]
                gallery["reasons"] = personalized_reasons(gallery, prediction, bootstrap)
        else:
            for gallery in scored:
                gallery.update(legacy_prediction_fields(gallery, personalized_status))
    elif model_mode != MODEL_MODE_VISUAL:
        for gallery in scored:
            gallery.update(legacy_prediction_fields(gallery, personalized_status))

    scored.sort(key=lambda item: item.get("rank_score", item["score"]), reverse=True)
    low_interest_policy = resolve_low_interest_threshold_policy(
        active_personalized_artifact,
        enabled=bool(low_interest_auto_threshold),
        model_mode=model_mode,
        max_percent=low_interest_max_percent,
    )
    low_interest_policy = annotate_interest_bands(
        scored,
        low_interest_percent,
        threshold_policy=low_interest_policy,
        max_percent=low_interest_max_percent,
    )
    low_interest_total = int(low_interest_policy["low_interest_total"])
    overall_total = len(scored)
    primary_total = overall_total - low_interest_total
    if interest_band == INTEREST_BAND_PRIMARY:
        scored = [item for item in scored if not item["low_interest"]]
    elif interest_band == INTEREST_BAND_LOW:
        scored = [item for item in scored if item["low_interest"]]
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
        "overall_total": overall_total,
        "primary_total": primary_total,
        "low_interest_total": low_interest_total,
        "low_interest_percent": low_interest_percent,
        "low_interest_policy": low_interest_policy,
        "interest_band": interest_band,
        "has_more": next_offset < len(scored),
        "candidate_limit": candidate_limit,
        "bootstrap_explore_count": bootstrap_explore_count,
        "require_bootstrap_match": bool(require_bootstrap_match),
        "language_filter": sorted(language_filter_values),
        "model_mode": model_mode,
        "personalized_model": personalized_status,
        "continuing_classifier": classifier_status,
        "exclude_short_repeats": bool(exclude_short_repeats),
        "short_repeat_page_limit": SHORT_REPEAT_PAGE_LIMIT,
    }


def apply_bootstrap_probability_prior(probability: float, gallery: dict, bootstrap: dict[str, float]) -> float:
    probability = min(1.0 - 1e-6, max(1e-6, probability))
    searchable = bootstrap_search_text(gallery)
    exact_values = bootstrap_exact_values(gallery)
    matched = [weight for tag, weight in bootstrap.items() if bootstrap_matches(tag, searchable, exact_values)]
    prior = max(-0.45, min(0.45, sum(matched) * 0.08))
    return apply_probability_logit_offset(probability, prior)


def apply_probability_logit_offset(probability: float, offset: float) -> float:
    probability = min(1.0 - 1e-6, max(1e-6, probability))
    logit = math.log(probability / (1.0 - probability)) + offset
    return 1.0 / (1.0 + math.exp(-logit))


def legacy_prediction_fields(gallery: dict, model_status: dict | None = None) -> dict:
    raw_score = float(gallery.get("score") or 0.0)
    return {
        "like_probability": None,
        "uncertainty": None,
        "confidence": None,
        "rank_score": round(raw_score, 6),
        "model_version": "legacy-linear-v1",
        "score_scale": "additive",
        "reason_details": {"positive": [], "negative": []},
        "model_fallback": (model_status or {}).get("status"),
        "text_visual_disagreement": 0.0,
    }


def discovery_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
    language_filter: list[str] | str | None = None,
    seed: str | None = None,
) -> dict:
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    pool_limit = min(10000, max(candidate_limit, limit + offset, 100))
    page = recommend_page(
        conn,
        limit=pool_limit,
        offset=0,
        include_rated=False,
        filter_text=filter_text,
        candidate_limit=pool_limit,
        freshness_weight=0.0,
        language_filter=language_filter,
        model_mode=MODEL_MODE_HYBRID,
        require_bootstrap_match=False,
    )
    candidates = page["items"]
    if not (
        (page.get("personalized_model") or {}).get("ready")
        and (page.get("personalized_model") or {}).get("accepted")
    ):
        items = []
        for candidate in candidates[offset : offset + limit]:
            item = dict(candidate)
            item["discovery_reason"] = "legacy fallback"
            items.append(item)
        return {**page, "items": items, "offset": offset, "next_offset": offset + len(items)}

    daily_seed = seed or time.strftime("%Y-%m-%d", time.gmtime())
    rng = random.Random(f"discovery:{daily_seed}")
    tie_break = {item["url"]: rng.random() for item in candidates}
    boundary = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("uncertainty") or 0.0),
            abs(float(item.get("like_probability") or 0.5) - 0.5),
            tie_break[item["url"]],
        ),
    )
    disagreement = sorted(
        candidates,
        key=lambda item: (-float(item.get("text_visual_disagreement") or 0.0), tie_break[item["url"]]),
    )
    interest_frequency: dict[str, int] = {}
    for item in candidates:
        for key in diversity_keys(item):
            interest_frequency[key] = interest_frequency.get(key, 0) + 1
    coverage = sorted(
        candidates,
        key=lambda item: (
            sum(interest_frequency.get(key, 0) for key in diversity_keys(item)),
            tie_break[item["url"]],
        ),
    )
    total_needed = len(candidates)
    quotas = [math.ceil(total_needed * 0.4), math.ceil(total_needed * 0.3), total_needed]
    selected: list[dict] = []
    selected_urls: set[str] = set()
    for source, target, reason in (
        (boundary, quotas[0], "uncertain boundary"),
        (disagreement, quotas[1], "text visual disagreement"),
        (coverage, quotas[2], "new interest coverage"),
    ):
        added = 0
        for item in source:
            if item["url"] in selected_urls:
                continue
            copy = dict(item)
            copy["discovery_reason"] = reason
            copy["reasons"] = [reason, *copy.get("reasons", [])][:5]
            selected.append(copy)
            selected_urls.add(item["url"])
            added += 1
            if added >= target or len(selected) >= total_needed:
                break
    items = selected[offset : offset + limit]
    next_offset = offset + len(items)
    return {
        **page,
        "items": items,
        "offset": offset,
        "next_offset": next_offset,
        "total": len(candidates),
        "has_more": next_offset < len(candidates),
        "surface": "discovery",
        "seed": daily_seed,
    }


def seen_interest_count(left: dict, right: dict) -> int:
    return len(set(diversity_keys(left)) & set(diversity_keys(right)))


def record_impressions(conn: sqlite3.Connection, request_id: str, surface: str, items: list[dict]) -> int:
    request_id = str(request_id or "").strip()[:120]
    if not request_id:
        raise ValueError("request_id is required")
    surface = normalize_surface(surface)
    inserted = 0
    for item in items[:100]:
        gallery_url = str(item.get("gallery_url") or item.get("url") or "").strip()
        position = item.get("position")
        if not gallery_url or not isinstance(position, int) or position < 0:
            continue
        exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not exists:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO recommendation_impressions(
                request_id, gallery_url, surface, position, model_version, like_probability
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                gallery_url,
                surface,
                position,
                str(item.get("model_version") or "")[:120] or None,
                optional_float(item.get("like_probability")),
            ),
        )
        inserted += max(0, cursor.rowcount)
    return inserted


def personalized_reasons(gallery: dict, prediction: dict, bootstrap: dict[str, float]) -> list[str]:
    probability = float(prediction.get("like_probability") or 0.0)
    reasons = [f"match {probability * 100:.0f}%"]
    details = prediction.get("reason_details") or {}
    for item in (details.get("positive") or [])[:2]:
        reasons.append(f"for {item.get('feature')} {float(item.get('contribution') or 0):+.2f}")
    for item in (details.get("negative") or [])[:1]:
        reasons.append(f"against {item.get('feature')} {float(item.get('contribution') or 0):+.2f}")
    matched_weight = sum(
        float(weight)
        for tag, weight in bootstrap.items()
        if bootstrap_matches(tag, bootstrap_search_text(gallery), bootstrap_exact_values(gallery))
    )
    if matched_weight > 0:
        reasons.append("bootstrap boost")
    elif matched_weight < 0:
        reasons.append("bootstrap penalty")
    freshness = float(gallery.get("freshness_bonus") or 0.0)
    if freshness:
        reasons.append(f"fresh {freshness:+.2f}")
    return reasons[:5]


def normalize_posted_after(value: str | None) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def gallery_posted_on_or_after(gallery: dict, posted_after: str) -> bool:
    posted_at = str(gallery.get("posted_at") or "").strip()
    if not posted_at:
        return False
    return posted_at[:10] >= posted_after


def normalize_model_mode(value: object) -> str:
    mode = str(value or MODEL_MODE_HYBRID).strip().lower()
    if mode in MODEL_MODES:
        return mode
    return MODEL_MODE_HYBRID


def normalize_interest_band(value: object) -> str:
    band = str(value or INTEREST_BAND_ALL).strip().lower()
    if band in INTEREST_BANDS:
        return band
    return INTEREST_BAND_ALL


def resolve_low_interest_threshold_policy(
    artifact: dict,
    *,
    enabled: bool,
    model_mode: str,
    max_percent: int,
) -> dict:
    learned = artifact.get("low_interest_threshold") or {}
    status = {
        "mode": "auto" if enabled else "off",
        "active": False,
        "status": "disabled" if not enabled else "unavailable",
        "threshold": learned.get("threshold"),
        "model_version": learned.get("model_version") or artifact.get("model_version"),
        "max_percent": max_percent,
        "validation": learned,
    }
    if not enabled:
        return status
    checks = (
        (model_mode == MODEL_MODE_HYBRID, "requires-hybrid-mode"),
        (bool(artifact.get("ready")), "model-not-ready"),
        (bool(artifact.get("accepted")), "model-not-accepted"),
        (artifact.get("score_scale") == "probability", "score-scale-not-probability"),
        (bool((artifact.get("calibration") or {}).get("ready")), "model-calibration-not-ready"),
        (bool(learned.get("ready")), str(learned.get("status") or "threshold-not-ready")),
        (
            learned.get("model_version") == artifact.get("model_version"),
            "threshold-model-version-mismatch",
        ),
    )
    for passed, reason in checks:
        if not passed:
            status["status"] = reason
            return status
    threshold = optional_float(learned.get("threshold"))
    if threshold is None or threshold < 0.0 or threshold > 1.0:
        status["status"] = "invalid-threshold"
        return status
    status.update({"active": True, "status": "active", "threshold": threshold})
    return status


def annotate_interest_bands(
    scored: list[dict],
    low_interest_percent: int,
    *,
    threshold_policy: dict | None = None,
    max_percent: int = DEFAULT_LOW_INTEREST_MAX_PERCENT,
) -> dict:
    total = len(scored)
    if total <= 1 or low_interest_percent <= 0:
        percentile_count = 0
    else:
        percentile_count = min(total - 1, math.ceil(total * low_interest_percent / 100.0))
    low_start = total - percentile_count
    percentile_indices = set(range(low_start, total))
    selected_indices = set(percentile_indices)
    policy = dict(threshold_policy or {})
    threshold = optional_float(policy.get("threshold")) if policy.get("active") else None
    threshold_indices: set[int] = set()
    if threshold is not None:
        model_version = policy.get("model_version")
        candidates = []
        for index, item in enumerate(scored):
            probability = optional_float(item.get("like_probability"))
            if (
                probability is not None
                and probability <= threshold
                and item.get("score_scale") == "probability"
                and item.get("model_version") == model_version
            ):
                candidates.append((probability, index))
        cap_count = min(
            max(0, total - 1),
            max(percentile_count, math.ceil(total * max_percent / 100.0)),
        )
        for _probability, index in sorted(candidates):
            if index in selected_indices:
                threshold_indices.add(index)
                continue
            if len(selected_indices) >= cap_count:
                continue
            selected_indices.add(index)
            threshold_indices.add(index)
    for index, item in enumerate(scored):
        item["interest_rank"] = index + 1
        item["interest_total"] = total
        item["interest_percentile"] = round((total - index) * 100.0 / total, 1) if total else None
        item["low_interest"] = index in selected_indices
        item["low_interest_cutoff_percent"] = low_interest_percent
        item["very_low_interest"] = index in threshold_indices
        item["low_interest_reason"] = (
            "learned-threshold"
            if index in threshold_indices
            else "bottom-percent"
            if index in percentile_indices
            else None
        )
        item["low_interest_threshold"] = threshold
    policy.update(
        {
            "percentile_count": percentile_count,
            "threshold_count": len(threshold_indices),
            "threshold_added_count": len(threshold_indices - percentile_indices),
            "low_interest_total": len(selected_indices),
            "overall_total": total,
        }
    )
    return policy


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
    score_floor = exploration_score_floor(scored)
    pool = [
        item
        for item in scored[keep_count:]
        if normalize_source_query(item.get("source_query")) in bootstrap_queries
        and float(item.get("rank_score", item.get("score") or 0)) >= score_floor
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


def exploration_score_floor(scored: list[dict]) -> float:
    if not scored:
        return MIN_BOOTSTRAP_EXPLORE_SCORE
    if all(item.get("score_scale") == "probability" for item in scored):
        values = sorted(float(item.get("rank_score", item.get("score") or 0.0)) for item in scored)
        return values[min(len(values) - 1, len(values) // 5)]
    return MIN_BOOTSTRAP_EXPLORE_SCORE


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


def gallery_item_from_row(row: sqlite3.Row | dict) -> dict:
    gallery = dict(row)
    gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
    gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json", None) or "{}")
    gallery["samples"] = json.loads(gallery.pop("samples_json", None) or "[]")
    gallery["visual_embedding"] = parse_visual_embedding(gallery.pop("visual_embedding_json", None))
    gallery["visual_embedding_version"] = gallery.get("visual_embedding_version")
    gallery["visual_ready"] = bool(gallery["visual_embedding"])
    return gallery


def apply_feedback_state(gallery: dict) -> dict:
    gallery["user_vote"] = round(float(gallery.get("user_vote", 0) or 0), 3)
    gallery["user_mark_kind"] = gallery.get("user_mark_kind")
    gallery["marked"] = gallery["user_mark_kind"] in MARK_KINDS
    gallery["rated"] = gallery.get("feedback_id") is not None or gallery["marked"]
    return gallery


def gallery_title_values(gallery: dict) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for field in ("title", "title_jpn"):
        value = str(gallery.get(field) or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def normalize_repeat_text(value: object) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", str(value or "").lower()).split())


def continuing_title_signal(gallery: dict) -> str:
    """Return a conservative title signal for cumulative source galleries."""
    titles = gallery_title_values(gallery)
    for title in titles:
        if CONTINUING_EXPLICIT_RE.search(title):
            return "explicit archive/ongoing title"
        if CONTINUING_DATE_RANGE_RE.search(title) and CONTINUING_SOURCE_RE.search(title):
            return "source archive date range"
    return ""


def continuing_source_label(gallery: dict) -> str:
    for title in gallery_title_values(gallery):
        match = CONTINUING_SOURCE_RE.search(title)
        if match:
            return normalize_repeat_text(match.group(0))
    return ""


def continuing_series_title_key(gallery: dict) -> str:
    """Build a key for source archives whose changing date suffix changes the gid."""
    source = continuing_source_label(gallery)
    has_snapshot_date = any(CONTINUING_TRAILING_DATE_RE.search(title) for title in gallery_title_values(gallery))
    if not source or (not continuing_title_signal(gallery) and not has_snapshot_date):
        return ""
    identities = short_repeat_identity_values(gallery)
    if not identities:
        identities = sorted(
            {identity for title in gallery_title_values(gallery) for identity in short_repeat_title_identity_values(title)}
        )
    if not identities:
        return ""
    for title in gallery_title_values(gallery):
        without_range = CONTINUING_DATE_RANGE_RE.sub(" ", title)
        without_date = CONTINUING_TRAILING_DATE_RE.sub("", without_range)
        normalized = normalize_repeat_text(without_date)
        if repeat_title_key_usable(normalized):
            return f"source-series:{source}|{normalized}|{'|'.join(identities)}"
    return ""


def _relationship_fingerprint(conn: sqlite3.Connection) -> tuple:
    galleries = conn.execute(
        """
        SELECT COUNT(*) AS count, COALESCE(MAX(last_seen_at), '') AS last_seen,
               COALESCE(SUM(LENGTH(COALESCE(parent_url, '')) + LENGTH(title) + LENGTH(tags_json)), 0) AS bytes
        FROM galleries
        """
    ).fetchone()
    feedback = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM feedback"
    ).fetchone()
    marks = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(MAX(updated_at), '') AS updated_at FROM gallery_marks"
    ).fetchone()
    return (
        int(galleries["count"]), str(galleries["last_seen"]), int(galleries["bytes"]),
        int(feedback["count"]), int(feedback["max_id"]),
        int(marks["count"]), str(marks["updated_at"]),
    )


def continuing_update_series_index(conn: sqlite3.Connection) -> dict[str, dict]:
    return _cached_scan(
        conn,
        "continuing-series-index",
        _relationship_fingerprint(conn),
        lambda: _build_continuing_update_series_index(conn),
    )


def _build_continuing_update_series_index(conn: sqlite3.Connection) -> dict[str, dict]:
    """Classify cumulative galleries and return series metadata keyed by gallery URL.

    A three-version parent component is strong evidence by itself. Two-version
    components require a source-platform title, while a lone gallery requires an
    explicit archive/ongoing or source date-range signal. This keeps ordinary
    revisions and translated editions out of the update bucket.
    """
    rows = conn.execute(
        f"""
        SELECT url, gid, title, title_jpn, category, posted_at, page_count,
               tags_json, parent_url, review_excluded, first_seen_at, last_seen_at
        FROM galleries
        """
    ).fetchall()
    galleries: dict[str, dict] = {}
    for row in rows:
        gallery = dict(row)
        try:
            gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        except json.JSONDecodeError:
            gallery["tags"] = []
        url = normalize_gallery_url(gallery.get("url"))
        if url:
            gallery["url"] = url
            galleries[url] = gallery
    if not galleries:
        return {}
    reviewed_urls = {
        normalize_gallery_url(row["gallery_url"])
        for row in conn.execute("SELECT DISTINCT gallery_url FROM feedback")
    }
    reviewed_urls.update(
        normalize_gallery_url(row["gallery_url"])
        for row in conn.execute("SELECT gallery_url FROM gallery_marks")
    )

    parent: dict[str, str] = {url: url for url in galleries}

    def find(url: str) -> str:
        while parent[url] != url:
            parent[url] = parent[parent[url]]
            url = parent[url]
        return url

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    parent_edges: set[tuple[str, str]] = set()
    for url, gallery in galleries.items():
        parent_url = normalize_gallery_url(gallery.get("parent_url"))
        if parent_url in galleries and parent_url != url:
            union(url, parent_url)
            parent_edges.add((url, parent_url))

    title_groups: dict[str, list[str]] = {}
    title_group_key_by_url: dict[str, str] = {}
    for url, gallery in galleries.items():
        key = continuing_series_title_key(gallery)
        if key:
            title_groups.setdefault(key, []).append(url)
            title_group_key_by_url[url] = key
    for urls in title_groups.values():
        if len(urls) >= 2:
            for url in urls[1:]:
                union(urls[0], url)

    components: dict[str, list[dict]] = {}
    for url, gallery in galleries.items():
        components.setdefault(find(url), []).append(gallery)

    result: dict[str, dict] = {}
    for members in components.values():
        urls = {member["url"] for member in members}
        edge_count = sum(1 for child, ancestor in parent_edges if child in urls and ancestor in urls)
        source_labels = sorted({continuing_source_label(member) for member in members if continuing_source_label(member)})
        explicit_signal = next((continuing_title_signal(member) for member in members if continuing_title_signal(member)), "")
        has_translation = any("language:translated" in (member.get("tags") or []) for member in members)
        repeated_source_key = any(
            len(title_groups.get(title_group_key_by_url.get(url, ""), [])) >= 2 for url in urls
        )

        reason = ""
        if edge_count >= 2 and len(members) >= 3 and (source_labels or not has_translation):
            reason = f"{len(members)}-version parent update chain"
        elif edge_count >= 1 and len(members) >= 2 and source_labels:
            reason = f"{source_labels[0]} parent update chain"
        elif repeated_source_key:
            reason = f"repeated {source_labels[0] if source_labels else 'source'} archive"
        elif len(members) == 1 and explicit_signal and (source_labels or CONTINUING_STANDALONE_RE.search(str(members[0].get("title") or ""))):
            reason = explicit_signal
        if not reason:
            continue

        reviewable = [member for member in members if not int(member.get("review_excluded") or 0)]
        if not reviewable:
            continue

        def newest_key(member: dict) -> tuple:
            gid = str(member.get("gid") or "")
            numeric_gid = int(gid) if gid.isdigit() else -1
            return (str(member.get("posted_at") or ""), numeric_gid, str(member.get("last_seen_at") or ""), member["url"])

        latest = max(reviewable, key=newest_key)
        oldest = min(members, key=newest_key)
        series_id = normalize_gallery_url(oldest.get("url")) or continuing_series_title_key(oldest) or oldest["url"]
        metadata = {
            "series_id": series_id,
            "version_count": len(members),
            "latest_url": latest["url"],
            "latest_title": latest.get("title"),
            "reason": reason,
            "source": source_labels[0] if source_labels else "",
            "reviewed": bool(urls & reviewed_urls),
        }
        for member in members:
            result[member["url"]] = metadata
    return result


def normalize_gallery_url(value: object) -> str:
    match = PARENT_GALLERY_RE.search(str(value or ""))
    if not match:
        return ""
    return f"https://exhentai.org/g/{match.group(1)}/{match.group(2).lower()}/"


def parent_repeat_key(gallery_url: str) -> str:
    return f"parent-url:{gallery_url}"


def short_repeat_parent_keys(gallery: dict) -> list[str]:
    parent_url = normalize_gallery_url(gallery.get("parent_url"))
    gallery_url = normalize_gallery_url(gallery.get("url"))
    if not parent_url or parent_url == gallery_url:
        return []
    return [parent_repeat_key(parent_url)]


def repeat_title_key_usable(value: str) -> bool:
    if not value:
        return False
    return len(value) >= 12 or (len(value) >= 6 and len(value.split()) >= 2)


def title_source_labels(title: object) -> list[str]:
    match = SOURCE_PREFIX_RE.match(str(title or ""))
    if not match:
        return []
    labels: list[str] = []
    for label_match in SOURCE_LABEL_RE.finditer(match.group(1)):
        label = normalize_repeat_text(label_match.group(1))
        if label:
            labels.append(label)
    return labels


def short_repeat_identity_values(gallery: dict) -> list[str]:
    values: list[str] = []
    for raw_tag in gallery.get("tags") or []:
        tag = normalize_bootstrap_value(str(raw_tag or "").strip().lower())
        if not tag or ":" not in tag:
            continue
        namespace = tag.split(":", 1)[0]
        if namespace in SHORT_REPEAT_IDENTITY_NAMESPACES:
            values.append(f"tag:{tag}")
    return sorted(set(values))


def short_repeat_title_identity_values(title: object) -> list[str]:
    raw_title = str(title or "")
    prefix = SOURCE_PREFIX_RE.match(raw_title)
    if not prefix:
        return []
    stripped = raw_title[prefix.end() :].strip()
    match = TITLE_ARTIST_ID_RE.match(stripped)
    if not match:
        return []
    name = normalize_repeat_text(match.group("name"))
    artist_id = match.group("artist_id")
    stripped_key = normalize_repeat_text(stripped)
    if not name or not artist_id or not repeat_title_key_usable(stripped_key):
        return []
    return sorted({f"title-artist:{label}|{stripped_key}" for label in title_source_labels(raw_title)})


def short_repeat_keys(gallery: dict) -> list[str]:
    titles = gallery_title_values(gallery)
    keys: list[str] = []
    seen: set[str] = set()
    identities = short_repeat_identity_values(gallery)
    if not identities:
        identities = sorted({identity for title in titles for identity in short_repeat_title_identity_values(title)})
    if not identities:
        return []

    def add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    for title in titles:
        normalized = normalize_repeat_text(title)
        if repeat_title_key_usable(normalized):
            for identity in identities:
                add(f"title-identity:{normalized}|{identity}")

        prefix = SOURCE_PREFIX_RE.match(title)
        if not prefix:
            continue

        labels = title_source_labels(title)
        stripped = normalize_repeat_text(title[prefix.end() :])
        if stripped != normalized and repeat_title_key_usable(stripped):
            for label in labels:
                for identity in identities:
                    add(f"source-title-identity:{label}|{stripped}|{identity}")
    return keys


def short_repeat_page_count(gallery: dict) -> int | None:
    try:
        page_count = int(gallery.get("page_count") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    return page_count if page_count > 0 else None


def is_short_repeat_candidate(gallery: dict, page_limit: int = SHORT_REPEAT_PAGE_LIMIT) -> bool:
    page_count = short_repeat_page_count(gallery)
    return page_count is not None and page_count <= page_limit


def related_feedback_payload(gallery: dict) -> dict:
    return {
        "url": gallery.get("url"),
        "title": gallery.get("title"),
        "title_jpn": gallery.get("title_jpn"),
        "parent_url": gallery.get("parent_url"),
        "category": gallery.get("category"),
        "uploader": gallery.get("uploader"),
        "page_count": gallery.get("page_count"),
        "feedback_id": gallery.get("feedback_id"),
        "user_vote": round(float(gallery.get("user_vote", 0) or 0), 3),
        "user_score": gallery.get("user_score"),
        "feedback_created_at": gallery.get("feedback_created_at"),
        "user_mark_kind": gallery.get("user_mark_kind"),
        "mark_updated_at": gallery.get("mark_updated_at"),
    }


def related_feedback_reference_index(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    return _cached_scan(
        conn,
        "related-feedback-index",
        _relationship_fingerprint(conn),
        lambda: _build_related_feedback_reference_index(conn),
    )


def _build_related_feedback_reference_index(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote,
               f.feedback_created_at,
               m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at
        FROM galleries g
        LEFT JOIN (
            SELECT feedback.id AS feedback_id, feedback.gallery_url, feedback.vote,
                   feedback.score AS user_score, feedback.created_at AS feedback_created_at
            FROM feedback
            JOIN (
                SELECT gallery_url, MAX(id) AS latest_id
                FROM feedback
                GROUP BY gallery_url
            ) latest ON latest.gallery_url = feedback.gallery_url AND latest.latest_id = feedback.id
        ) f ON f.gallery_url = g.url
        LEFT JOIN gallery_marks m ON m.gallery_url = g.url
        WHERE f.feedback_id IS NOT NULL OR m.kind IS NOT NULL
        ORDER BY COALESCE(f.feedback_id, 0) DESC, m.updated_at DESC, g.last_seen_at DESC
        LIMIT 10000
        """
    ).fetchall()
    children_by_parent: dict[str, list[str]] = {}
    for row in conn.execute("SELECT url, parent_url FROM galleries WHERE parent_url IS NOT NULL AND parent_url != ''"):
        url = normalize_gallery_url(row["url"])
        parent_url = normalize_gallery_url(row["parent_url"])
        if url and parent_url and url != parent_url:
            children_by_parent.setdefault(parent_url, []).append(url)

    index: dict[str, list[dict]] = {}
    rated_payloads: list[tuple[str, dict]] = []
    for row in rows:
        gallery = apply_feedback_state(gallery_item_from_row(row))
        payload = related_feedback_payload(gallery)
        gallery_url = normalize_gallery_url(gallery.get("url"))
        if gallery_url:
            rated_payloads.append((gallery_url, payload))
            index.setdefault(parent_repeat_key(gallery_url), []).append(payload)
        for key in short_repeat_keys(gallery):
            index.setdefault(key, []).append(payload)

    for gallery_url, payload in rated_payloads:
        seen = {gallery_url}
        stack = list(children_by_parent.get(gallery_url, []))
        while stack:
            child_url = stack.pop()
            if child_url in seen:
                continue
            seen.add(child_url)
            index.setdefault(parent_repeat_key(child_url), []).append(payload)
            stack.extend(children_by_parent.get(child_url, []))
    return index


def short_repeat_related_references(
    gallery: dict,
    reference_index: dict[str, list[dict]],
    limit: int = RELATED_FEEDBACK_REFERENCE_LIMIT,
    page_limit: int = SHORT_REPEAT_PAGE_LIMIT,
) -> list[dict]:
    references: list[dict] = []
    seen_urls = {gallery.get("url")}
    for key in short_repeat_parent_keys(gallery):
        for reference in reference_index.get(key, []):
            url = reference.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            item = dict(reference)
            item["matched_key"] = key
            references.append(item)
            if len(references) >= limit:
                return references
    if not is_short_repeat_candidate(gallery, page_limit=page_limit):
        return references
    for key in short_repeat_keys(gallery):
        for reference in reference_index.get(key, []):
            url = reference.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            item = dict(reference)
            item["matched_key"] = key
            references.append(item)
            if len(references) >= limit:
                return references
    return references


def is_short_repeat_gallery(gallery: dict, reference_index: dict[str, list[dict]]) -> bool:
    return bool(short_repeat_related_references(gallery, reference_index, limit=1))


def short_repeat_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
    page_limit: int = SHORT_REPEAT_PAGE_LIMIT,
    continuing_updates: dict[str, dict] | None = None,
) -> dict:
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    page_limit = max(1, min(100, int(page_limit)))
    candidate_limit = 10000 if filter_text else min(10000, max(100, int(candidate_limit)))
    candidate_limit = max(limit + offset, candidate_limit)
    reference_index = related_feedback_reference_index(conn)
    if continuing_updates is None:
        continuing_updates = continuing_update_series_index(conn)
    if not reference_index:
        return {
            "items": [],
            "limit": limit,
            "offset": offset,
            "next_offset": offset,
            "total": 0,
            "has_more": False,
            "candidate_limit": candidate_limit,
            "short_repeat_page_limit": page_limit,
        }

    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote,
               m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at
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
        LEFT JOIN gallery_marks m ON m.gallery_url = g.url
        WHERE g.review_excluded = 0
          AND f.feedback_id IS NULL AND m.kind IS NULL
          AND (g.parent_url IS NOT NULL OR (g.page_count IS NOT NULL AND g.page_count <= ?))
        ORDER BY g.last_seen_at DESC
        LIMIT ?
        """,
        (page_limit, candidate_limit),
    ).fetchall()

    items: list[dict] = []
    for idx, row in enumerate(rows):
        gallery = apply_feedback_state(gallery_item_from_row(row))
        if gallery.get("url") in continuing_updates:
            continue
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        related = short_repeat_related_references(gallery, reference_index, page_limit=page_limit)
        if not related:
            continue
        score, reasons = score_gallery(gallery, bootstrap, weights, visual_model=visual_model, tag_strengths=tag_strengths)
        gallery.pop("visual_embedding", None)
        freshness = freshness_bonus(idx, candidate_limit)
        score += freshness
        if freshness:
            reasons.append(f"fresh {freshness:+.2f}")
        gallery["score"] = round(score, 3)
        gallery["reasons"] = ["short repeat", *reasons][:5]
        gallery["related_feedback"] = related
        gallery["short_repeat"] = True
        gallery["short_repeat_page_limit"] = page_limit
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
        "candidate_limit": candidate_limit,
        "short_repeat_page_limit": page_limit,
    }


def continuing_update_page(
    conn: sqlite3.Connection,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
    candidate_limit: int = 2000,
    continuing_updates: dict[str, dict] | None = None,
    shortlist_limit: int = 40,
    min_new_pages: int = 50,
) -> dict:
    """Return a ranked shortlist of meaningful cumulative-gallery updates."""
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    candidate_limit = 10000 if filter_text else min(10000, max(100, int(candidate_limit)))
    candidate_limit = max(limit + offset, candidate_limit)
    shortlist_limit = max(1, min(500, int(shortlist_limit)))
    min_new_pages = max(0, min(5000, int(min_new_pages)))
    series_index = continuing_updates if continuing_updates is not None else continuing_update_series_index(conn)
    overrides = classification_overrides(conn)
    latest = {
        metadata["latest_url"]: metadata
        for metadata in series_index.values()
        if overrides.get(normalize_gallery_url(metadata["latest_url"])) != "review"
    }
    manual_update_urls = {url for url, classification in overrides.items() if classification == "updates"}
    for url in manual_update_urls:
        if url not in latest:
            latest[url] = {
                "series_id": url,
                "version_count": 1,
                "latest_url": url,
                "latest_title": "",
                "reason": "manual classification",
                "source": "",
                "reviewed": False,
            }
    classifier_rows = conn.execute(
        """
        SELECT url, title, title_jpn, category, page_count, parent_url
        FROM galleries
        WHERE review_excluded = 0
        ORDER BY COALESCE(posted_at, last_seen_at) DESC, last_seen_at DESC
        LIMIT ?
        """,
        (candidate_limit,),
    ).fetchall()
    classifier_inputs = [dict(row) for row in classifier_rows]
    classifier_decisions, classifier_status = continuing_classifier_decisions(conn, classifier_inputs)
    for gallery in classifier_inputs:
        url = normalize_gallery_url(gallery.get("url"))
        decision = classifier_decisions.get(url)
        if (
            decision and decision.get("classification") == "updates"
            and url not in latest and overrides.get(url) != "review"
        ):
            latest[url] = {
                "series_id": url,
                "version_count": 1,
                "latest_url": url,
                "latest_title": gallery.get("title") or "",
                "reason": "learned classification",
                "source": "",
                "reviewed": False,
            }
    if not latest:
        return {
            "items": [], "limit": limit, "offset": offset, "next_offset": offset,
            "total": 0, "eligible_total": 0, "has_more": False,
            "candidate_limit": candidate_limit, "shortlist_limit": shortlist_limit,
            "updates_min_new_pages": min_new_pages,
        }

    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    rows = []
    latest_urls = list(latest)
    for chunk_offset in range(0, len(latest_urls), 500):
        chunk = latest_urls[chunk_offset : chunk_offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"""
                SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote,
                       f.feedback_created_at,
                       m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at
                FROM galleries g
                LEFT JOIN (
                    SELECT feedback.id AS feedback_id, feedback.gallery_url, feedback.vote,
                           feedback.score AS user_score, feedback.created_at AS feedback_created_at
                    FROM feedback
                    JOIN (
                        SELECT gallery_url, MAX(id) AS latest_id FROM feedback GROUP BY gallery_url
                    ) latest_feedback
                      ON latest_feedback.gallery_url = feedback.gallery_url AND latest_feedback.latest_id = feedback.id
                ) f ON f.gallery_url = g.url
                LEFT JOIN gallery_marks m ON m.gallery_url = g.url
                WHERE g.url IN ({placeholders}) AND g.review_excluded = 0
                """,
                chunk,
            ).fetchall()
        )

    items: list[dict] = []
    feedback_context = continuing_feedback_context(conn, series_index)
    for idx, row in enumerate(rows):
        gallery = apply_feedback_state(gallery_item_from_row(row))
        metadata = latest.get(gallery.get("url"))
        if not metadata:
            continue
        previous_feedback = continuing_previous_feedback(
            conn,
            gallery["url"],
            metadata,
            series_index,
            include_current=True,
            context=feedback_context,
        )
        if gallery["rated"] and previous_feedback is None:
            continue
        new_pages = None
        if previous_feedback is not None:
            current_pages = positive_page_count(gallery.get("page_count"))
            previous_pages = positive_page_count(previous_feedback.get("page_count_at_feedback"))
            if current_pages is not None and previous_pages is not None:
                new_pages = max(0, current_pages - previous_pages)
            if min_new_pages and (new_pages is None or new_pages < min_new_pages):
                continue
        if filter_text and not gallery_matches_filter(gallery, filter_text):
            continue
        score, reasons = score_gallery(gallery, bootstrap, weights, visual_model=visual_model, tag_strengths=tag_strengths)
        gallery.pop("visual_embedding", None)
        freshness = freshness_bonus(idx, candidate_limit)
        gallery["score"] = round(score + freshness, 3)
        growth_reason = f"expanded +{new_pages} pages" if new_pages is not None else None
        gallery["reasons"] = [
            reason
            for reason in ["continuing update", growth_reason, metadata["reason"], *reasons]
            if reason
        ][:5]
        gallery["continuing_update"] = True
        gallery["continuing_series"] = metadata
        gallery["new_pages_since_feedback"] = new_pages
        gallery["updates_min_new_pages"] = min_new_pages
        gallery["classification_override"] = overrides.get(normalize_gallery_url(gallery["url"]))
        gallery["classification_prediction"] = classifier_decisions.get(normalize_gallery_url(gallery["url"]))
        gallery["previous_feedback"] = previous_feedback
        items.append(gallery)

    items.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            str(item.get("posted_at") or item.get("last_seen_at") or ""),
        ),
        reverse=True,
    )
    eligible_total = len(items)
    if not filter_text:
        items = items[:shortlist_limit]
    page_items = items[offset : offset + limit]
    next_offset = offset + len(page_items)
    return {
        "items": page_items,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "total": len(items),
        "eligible_total": eligible_total,
        "has_more": next_offset < len(items),
        "candidate_limit": candidate_limit,
        "shortlist_limit": shortlist_limit,
        "updates_min_new_pages": min_new_pages,
        "continuing_classifier": classifier_status,
    }


def positive_page_count(value: object) -> int | None:
    try:
        page_count = int(value or 0)
    except (TypeError, ValueError):
        return None
    return page_count if page_count > 0 else None


def continuing_feedback_context(
    conn: sqlite3.Connection,
    series_index: dict[str, dict],
) -> dict[str, object]:
    members_by_series: dict[str, set[str]] = {}
    for url, metadata in series_index.items():
        series_id = str(metadata.get("series_id") or "")
        normalized_url = normalize_gallery_url(url)
        if series_id and normalized_url:
            members_by_series.setdefault(series_id, set()).add(normalized_url)
    parent_by_url = {
        normalize_gallery_url(row["url"]): normalize_gallery_url(row["parent_url"])
        for row in conn.execute("SELECT url, parent_url FROM galleries WHERE parent_url IS NOT NULL")
    }
    feedback_rows = conn.execute(
        """
        SELECT f.id AS feedback_id, f.gallery_url AS url, f.vote AS user_vote,
               f.score AS user_score, f.reason_code, f.created_at AS feedback_created_at,
               g.title,
               COALESCE(
                   (
                       SELECT s.page_count
                       FROM gallery_feature_snapshots s
                       WHERE s.gallery_url = f.gallery_url
                         AND julianday(s.captured_at) IS NOT NULL
                         AND julianday(s.captured_at) <= julianday(f.created_at)
                       ORDER BY (s.source = 'feedback') DESC, s.captured_at DESC, s.id DESC
                       LIMIT 1
                   ),
                   g.page_count
               ) AS page_count_at_feedback
        FROM feedback f
        JOIN galleries g ON g.url = f.gallery_url
        JOIN (
            SELECT gallery_url, MAX(id) AS latest_id
            FROM feedback
            GROUP BY gallery_url
        ) latest ON latest.latest_id = f.id
        """
    ).fetchall()
    return {
        "members_by_series": members_by_series,
        "parent_by_url": parent_by_url,
        "feedback_by_url": {
            normalize_gallery_url(row["url"]): dict(row)
            for row in feedback_rows
        },
    }


def continuing_previous_feedback(
    conn: sqlite3.Connection,
    gallery_url: str,
    metadata: dict,
    series_index: dict[str, dict],
    *,
    include_current: bool = False,
    context: dict[str, object] | None = None,
) -> dict | None:
    gallery_url = normalize_gallery_url(gallery_url)
    series_id = metadata.get("series_id")
    context = context or continuing_feedback_context(conn, series_index)
    members_by_series: dict[str, set[str]] = context["members_by_series"]  # type: ignore[assignment]
    parent_by_url: dict[str, str] = context["parent_by_url"]  # type: ignore[assignment]
    feedback_by_url: dict[str, dict] = context["feedback_by_url"]  # type: ignore[assignment]
    member_urls = set(members_by_series.get(str(series_id or ""), set()))
    if include_current:
        member_urls.add(gallery_url)
    else:
        member_urls.discard(gallery_url)
    if not member_urls:
        return None

    ancestors: list[str] = []
    seen = {gallery_url}
    current = gallery_url
    while current and len(ancestors) < 12:
        parent_url = parent_by_url.get(current, "")
        if not parent_url or parent_url in seen:
            break
        seen.add(parent_url)
        if parent_url in member_urls:
            ancestors.append(parent_url)
        current = parent_url

    source = feedback_by_url.get(gallery_url) if include_current else None
    if source is None:
        source = next((feedback_by_url[url] for url in ancestors if url in feedback_by_url), None)
    if source is None:
        candidates = [feedback_by_url[url] for url in member_urls if url in feedback_by_url]
        source = max(candidates, key=lambda row: int(row["feedback_id"])) if candidates else None
    if source is None:
        return None
    source = dict(source)
    source["user_vote"] = round(float(source.get("user_vote") or 0.0), 3)
    source["is_parent"] = normalize_gallery_url(source["url"]) in ancestors
    source["is_current"] = normalize_gallery_url(source["url"]) == gallery_url
    return source


def copy_continuing_feedback(conn: sqlite3.Connection, gallery_url: str) -> dict:
    gallery_url = normalize_gallery_url(gallery_url)
    series_index = continuing_update_series_index(conn)
    metadata = series_index.get(gallery_url)
    if not metadata or metadata.get("latest_url") != gallery_url:
        raise ValueError("gallery is not the latest continuing update")
    source = continuing_previous_feedback(conn, gallery_url, metadata, series_index)
    if source is None:
        raise ValueError("no previous feedback is available for this continuing series")
    score = source.get("user_score")
    vote = None if score is not None else float(source.get("user_vote") or 0.0)
    if score is None and vote == 0:
        raise ValueError("previous feedback has no reusable preference signal")
    record_feedback(
        conn,
        gallery_url,
        vote=vote,
        score=score,
        reason_code=source.get("reason_code"),
        surface="updates",
    )
    return source


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
               f.note AS feedback_note, f.created_at AS feedback_created_at,
               m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at
        FROM feedback f
        JOIN (
            SELECT gallery_url, MAX(id) AS latest_id
            FROM feedback
            GROUP BY gallery_url
        ) latest ON latest.gallery_url = f.gallery_url AND latest.latest_id = f.id
        JOIN galleries g ON g.url = f.gallery_url
        LEFT JOIN gallery_marks m ON m.gallery_url = g.url
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
        gallery["user_mark_kind"] = gallery.get("user_mark_kind")
        gallery["marked"] = gallery["user_mark_kind"] in MARK_KINDS
        gallery["rated"] = True
        if gallery["user_mark_kind"] == "ban":
            score -= 5.0
            reasons.append("banned")
        elif gallery["user_mark_kind"] == "favorite":
            score += 2.5
            reasons.append("favorite")
        elif gallery["user_vote"] < 0:
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


def marked_gallery_page(
    conn: sqlite3.Connection,
    kind: str,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    kind = normalize_mark_kind(kind)
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    filter_text = (filter_text or "").strip().lower()
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    visual_model = visual_preference_model(conn)
    tag_strengths = tag_corpus_strengths(conn)
    rows = conn.execute(
        """
        SELECT g.*, f.feedback_id, f.user_score, COALESCE(f.vote, 0) AS user_vote,
               m.kind AS user_mark_kind, m.created_at AS mark_created_at, m.updated_at AS mark_updated_at
        FROM gallery_marks m
        JOIN galleries g ON g.url = m.gallery_url
        LEFT JOIN (
            SELECT feedback.id AS feedback_id, feedback.gallery_url, feedback.vote, feedback.score AS user_score
            FROM feedback
            JOIN (
                SELECT gallery_url, MAX(id) AS latest_id
                FROM feedback
                GROUP BY gallery_url
            ) latest ON latest.gallery_url = feedback.gallery_url AND latest.latest_id = feedback.id
        ) f ON f.gallery_url = g.url
        WHERE m.kind = ?
        ORDER BY m.updated_at DESC, m.created_at DESC
        LIMIT 10000
        """,
        (kind,),
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
        gallery["marked"] = True
        gallery["rated"] = True
        if kind == "ban":
            score -= 5.0
            reasons.append("banned")
        else:
            score += 2.5
            reasons.append("favorite")
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
        "mark_kind": kind,
    }


def diversify_ranked_galleries(scored: list[dict]) -> list[dict]:
    if len(scored) <= 2:
        return scored
    if all(item.get("score_scale") in {"probability", "similarity"} for item in scored):
        return diversify_probability_ranked_galleries(scored)
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


def diversify_probability_ranked_galleries(scored: list[dict], probability_window: float = 0.08) -> list[dict]:
    remaining = list(scored)
    selected: list[dict] = []
    seen: dict[str, int] = {}
    while remaining:
        highest = max(diversity_rank_score(item) for item in remaining)
        eligible = [
            (index, item)
            for index, item in enumerate(remaining)
            if diversity_rank_score(item) >= highest - probability_window
        ]
        best_index, best_item = max(
            eligible,
            key=lambda pair: (
                diversity_rank_score(pair[1]) - diversity_penalty(pair[1], seen) * 0.03,
                float(pair[1].get("confidence") or 0.0),
                -pair[0],
            ),
        )
        item = remaining.pop(best_index)
        penalty = diversity_penalty(item, seen)
        if penalty:
            item = dict(item)
            item["reasons"] = [*item.get("reasons", []), "near-score diversity"][:5]
        selected.append(item)
        for key in diversity_keys(item):
            seen[key] = seen.get(key, 0) + 1
    return selected


def diversity_rank_score(item: dict) -> float:
    if item.get("rank_score") is not None:
        return float(item["rank_score"])
    if item.get("like_probability") is not None:
        return float(item["like_probability"])
    return float(item.get("score") or 0.0)


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
            *gallery_title_values(gallery),
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
    values = gallery_title_values(gallery)
    category = str(gallery.get("category") or "").strip()
    if category:
        values.extend([category, f"category:{category}"])
    uploader = str(gallery.get("uploader") or "").strip()
    if uploader:
        values.extend([uploader, f"uploader:{uploader}"])
    values.extend(str(tag) for tag in gallery.get("tags") or [])
    return " ".join(value.lower() for value in values if value)


def model_snapshot(conn: sqlite3.Connection, train_if_needed: bool = True) -> dict:
    visual_model = visual_preference_model(conn)
    visual_counts = visual_version_counts(conn)
    personalized_model = model_public_status(
        load_personalized_model(
            conn,
            train_if_needed=train_if_needed,
            allow_stale=not train_if_needed,
        )
    )
    continuing_classifier = (
        public_classifier_status(load_continuing_classifier(conn))
        if train_if_needed
        else {"ready": False, "status": "not-loaded"}
    )
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
        "personalized": personalized_model,
        "continuing_classifier": continuing_classifier,
        "counts": {
            "galleries": conn.execute("SELECT COUNT(*) AS c FROM galleries").fetchone()["c"],
            "feedback_events": conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"],
            "rated_galleries": conn.execute("SELECT COUNT(DISTINCT gallery_url) AS c FROM feedback").fetchone()["c"],
            "marked_galleries": conn.execute("SELECT COUNT(*) AS c FROM gallery_marks").fetchone()["c"],
            "favorite_galleries": conn.execute("SELECT COUNT(*) AS c FROM gallery_marks WHERE kind = 'favorite'").fetchone()["c"],
            "banned_galleries": conn.execute("SELECT COUNT(*) AS c FROM gallery_marks WHERE kind = 'ban'").fetchone()["c"],
            "downloaded_galleries": conn.execute(
                "SELECT COUNT(DISTINCT gallery_url) AS c FROM hath_downloads WHERE status = 'completed' AND gallery_url IS NOT NULL"
            ).fetchone()["c"],
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
