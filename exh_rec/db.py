from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("EXH_REC_DATA_DIR", APP_DIR / "data"))
DB_PATH = DATA_DIR / "recommender.sqlite3"


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bootstrap_tags (
    tag TEXT PRIMARY KEY,
    weight REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS galleries (
    url TEXT PRIMARY KEY,
    gid TEXT,
    token TEXT,
    title TEXT NOT NULL,
    title_jpn TEXT,
    category TEXT,
    uploader TEXT,
    posted_at TEXT,
    thumb_url TEXT,
    rating REAL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    tag_weights_json TEXT NOT NULL DEFAULT '{}',
    source_query TEXT,
    parent_url TEXT,
    review_excluded INTEGER NOT NULL DEFAULT 0,
    detail_fetched_at TEXT,
    page_count INTEGER,
    samples_json TEXT NOT NULL DEFAULT '[]',
    samples_fetched_at TEXT,
    visual_embedding_json TEXT,
    visual_embedding_digest TEXT,
    visual_embedding_version TEXT,
    visual_embedding_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    vote REAL NOT NULL,
    score INTEGER,
    note TEXT,
    reason_code TEXT,
    surface TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recommendation_impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    surface TEXT NOT NULL,
    position INTEGER NOT NULL,
    model_version TEXT,
    like_probability REAL,
    feature_snapshot_id INTEGER REFERENCES gallery_feature_snapshots(id) ON DELETE SET NULL,
    audit_id INTEGER,
    ranking_context_json TEXT NOT NULL DEFAULT '{}',
    visibility_protocol TEXT,
    visible_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(request_id, gallery_url, surface)
);

CREATE TABLE IF NOT EXISTS low_interest_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_date TEXT NOT NULL,
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'bottom-20-random',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'completed', 'incomplete')),
    selection_probability REAL NOT NULL,
    sampling_frame TEXT NOT NULL DEFAULT 'served-bottom-20',
    frame_size INTEGER NOT NULL,
    score_band_low REAL,
    score_band_high REAL,
    served_model TEXT,
    served_rank_score REAL,
    served_rank INTEGER,
    legacy_score REAL,
    legacy_rank INTEGER,
    personalized_model_version TEXT,
    personalized_like_probability REAL,
    personalized_rank_score REAL,
    personalized_rank INTEGER,
    feature_snapshot_id INTEGER REFERENCES gallery_feature_snapshots(id) ON DELETE SET NULL,
    selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    exposed_at TEXT,
    visible_at TEXT,
    completed_at TEXT,
    outcome_source TEXT,
    outcome_positive INTEGER CHECK(outcome_positive IN (0, 1)),
    feedback_id INTEGER,
    UNIQUE(audit_date, gallery_url)
);

CREATE TABLE IF NOT EXISTS low_interest_audit_days (
    audit_date TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS low_interest_audit_slots (
    audit_date TEXT NOT NULL REFERENCES low_interest_audit_days(audit_date) ON DELETE CASCADE,
    audit_id INTEGER NOT NULL REFERENCES low_interest_audits(id) ON DELETE CASCADE,
    PRIMARY KEY(audit_date, audit_id)
);

CREATE TABLE IF NOT EXISTS low_interest_memberships (
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    policy_key TEXT NOT NULL,
    cutoff_percent INTEGER NOT NULL,
    assigned_score REAL,
    assigned_model TEXT,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(gallery_url, policy_key)
);

CREATE TABLE IF NOT EXISTS model_training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    selected_c REAL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gallery_visual_images (
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    image_key TEXT NOT NULL,
    image_url TEXT,
    embedding_json TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(gallery_url, image_key, embedding_version)
);

CREATE TABLE IF NOT EXISTS gallery_visual_embeddings (
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    embedding_version TEXT NOT NULL,
    encoder TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    embedding_digest TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(gallery_url, embedding_version)
);

CREATE TABLE IF NOT EXISTS gallery_feature_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL DEFAULT 'metadata',
    title TEXT NOT NULL,
    title_jpn TEXT,
    category TEXT,
    uploader TEXT,
    rating REAL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    tag_weights_json TEXT NOT NULL DEFAULT '{}',
    page_count INTEGER,
    detail_fetched_at TEXT,
    visual_embedding_json TEXT,
    visual_embedding_digest TEXT,
    visual_embedding_version TEXT,
    visual_embedding_at TEXT
);

CREATE TABLE IF NOT EXISTS gallery_marks (
    gallery_url TEXT PRIMARY KEY REFERENCES galleries(url) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('favorite', 'ban')),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gallery_classification_overrides (
    gallery_url TEXT PRIMARY KEY REFERENCES galleries(url) ON DELETE CASCADE,
    classification TEXT NOT NULL CHECK(classification IN ('review', 'updates')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gallery_classification_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    sampling_strategy TEXT NOT NULL CHECK(sampling_strategy IN ('random', 'uncertainty')),
    sampling_frame TEXT NOT NULL DEFAULT 'full-library',
    selection_probability REAL NOT NULL,
    model_probability REAL,
    classification TEXT CHECK(classification IN ('review', 'updates')),
    sampled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    labeled_at TEXT
);

CREATE TABLE IF NOT EXISTS hath_clients (
    client_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',
    hostname TEXT,
    agent_version TEXT,
    active_gid TEXT,
    active_title TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    last_event_at TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hath_downloads (
    client_id TEXT NOT NULL REFERENCES hath_clients(client_id) ON DELETE CASCADE,
    gid TEXT NOT NULL,
    resolution TEXT NOT NULL DEFAULT 'org',
    gallery_url TEXT REFERENCES galleries(url) ON DELETE SET NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'discovered'
        CHECK(status IN ('discovered', 'downloading', 'completed', 'failed')),
    directory_name TEXT,
    total_files INTEGER,
    downloaded_files INTEGER NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    failed_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(client_id, gid, resolution)
);

CREATE TABLE IF NOT EXISTS hath_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    client_id TEXT NOT NULL REFERENCES hath_clients(client_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    gid TEXT,
    resolution TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    queries_json TEXT NOT NULL DEFAULT '[]',
    fetched_count INTEGER NOT NULL DEFAULT 0,
    stored_count INTEGER NOT NULL DEFAULT 0,
    enriched_count INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS fetch_query_state (
    query_key TEXT PRIMARY KEY,
    query_text TEXT,
    source TEXT NOT NULL,
    anchor_urls_json TEXT NOT NULL DEFAULT '[]',
    caught_up INTEGER NOT NULL DEFAULT 1,
    last_page_count INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_weights (
    feature TEXT PRIMARY KEY,
    weight REAL NOT NULL DEFAULT 0.0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_galleries_last_seen ON galleries(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_gallery ON feedback(gallery_url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_gallery_id ON feedback(gallery_url, id DESC);
CREATE INDEX IF NOT EXISTS idx_impressions_created ON recommendation_impressions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_impressions_gallery ON recommendation_impressions(gallery_url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_low_interest_audits_date ON low_interest_audits(audit_date DESC, status);
CREATE INDEX IF NOT EXISTS idx_low_interest_audits_gallery ON low_interest_audits(gallery_url, selected_at DESC);
CREATE INDEX IF NOT EXISTS idx_low_interest_memberships_policy
    ON low_interest_memberships(policy_key, assigned_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_training_created ON model_training_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gallery_visual_embeddings_version
    ON gallery_visual_embeddings(embedding_version, gallery_url);
CREATE INDEX IF NOT EXISTS idx_gallery_marks_kind ON gallery_marks(kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_gallery_snapshots_lookup
    ON gallery_feature_snapshots(gallery_url, captured_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_classification_overrides_kind ON gallery_classification_overrides(classification, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_classification_samples_open
    ON gallery_classification_samples(classification, sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_classification_samples_gallery
    ON gallery_classification_samples(gallery_url, sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_hath_downloads_status ON hath_downloads(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_hath_downloads_gallery ON hath_downloads(gallery_url, status);
CREATE INDEX IF NOT EXISTS idx_hath_events_client ON hath_events(client_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_started ON fetch_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_query_state_updated ON fetch_query_state(updated_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_hath_download_gallery_insert
AFTER INSERT ON galleries
WHEN NEW.gid IS NOT NULL AND NEW.gid != ''
BEGIN
    UPDATE hath_downloads
    SET gallery_url = NEW.url
    WHERE gallery_url IS NULL AND gid = NEW.gid;
END;

CREATE TRIGGER IF NOT EXISTS trg_hath_download_gallery_gid_update
AFTER UPDATE OF gid ON galleries
WHEN NEW.gid IS NOT NULL AND NEW.gid != ''
BEGIN
    UPDATE hath_downloads
    SET gallery_url = NEW.url
    WHERE gallery_url IS NULL AND gid = NEW.gid;
END;
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        ensure_column(conn, "feedback", "score", "INTEGER")
        ensure_column(conn, "feedback", "reason_code", "TEXT")
        ensure_column(conn, "feedback", "surface", "TEXT")
        ensure_column(conn, "recommendation_impressions", "feature_snapshot_id", "INTEGER")
        ensure_column(conn, "recommendation_impressions", "audit_id", "INTEGER")
        ensure_column(conn, "recommendation_impressions", "visibility_protocol", "TEXT")
        ensure_column(conn, "recommendation_impressions", "visible_at", "TEXT")
        ensure_column(conn, "low_interest_audits", "visible_at", "TEXT")
        ensure_column(
            conn,
            "recommendation_impressions",
            "ranking_context_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        ensure_column(conn, "galleries", "title_jpn", "TEXT")
        ensure_column(conn, "galleries", "parent_url", "TEXT")
        ensure_column(conn, "galleries", "review_excluded", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "galleries", "detail_fetched_at", "TEXT")
        ensure_column(conn, "galleries", "tag_weights_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "galleries", "page_count", "INTEGER")
        ensure_column(conn, "galleries", "samples_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(conn, "galleries", "samples_fetched_at", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_json", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_digest", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_version", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_at", "TEXT")
        ensure_column(conn, "gallery_feature_snapshots", "visual_embedding_digest", "TEXT")
        ensure_column(
            conn,
            "gallery_classification_samples",
            "sampling_frame",
            "TEXT NOT NULL DEFAULT 'legacy-unknown'",
        )
        ensure_column(conn, "fetch_runs", "enriched_count", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            UPDATE hath_downloads
            SET status = 'completed'
            WHERE status IN ('discovered', 'downloading')
              AND completed_at IS NOT NULL
              AND total_files IS NOT NULL
              AND downloaded_files >= total_files
            """
        )
        defaults = {
            "auto_refresh": "1",
            "refresh_interval_minutes": "30",
            "fetch_pages": "1",
            "stale_fetch_extra_pages": "20",
            "detail_fetch_limit": "8",
            "learned_query_limit": "6",
            "request_interval_seconds": "3.0",
            "temporary_ban_pause_seconds": "90.0",
            "recommend_candidate_limit": "2000",
            "updates_shortlist_limit": "40",
            "updates_min_new_pages": "50",
            "recommend_language_filter": "japanese,chinese",
            "recommend_model_mode": "hybrid",
            "preview_freshness_weight": "8.0",
            "preview_posted_after": "",
            "review_require_bootstrap_match": "1",
            "sample_extra_pages": "2",
            "hath_download_signal_weight": "1.25",
            "model_retrain_mode": "batched",
            "model_retrain_feedback_threshold": "10",
            "model_retrain_interval_minutes": "10",
            "model_retrain_pending_count": "0",
            "model_retrain_pending_since": "",
            "model_retrain_last_completed_at": "",
            "model_retrain_active_signature": "",
            "review_low_interest_percent": "20",
            "review_low_interest_auto_threshold": "1",
            "review_low_interest_max_percent": "35",
            "review_low_interest_audit_enabled": "1",
            "review_low_interest_audit_daily_count": "6",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        backfill_visual_embedding_digests(conn)
        conn.execute(
            """
            UPDATE hath_downloads
            SET gallery_url = (
                SELECT g.url FROM galleries g
                WHERE g.gid = hath_downloads.gid
                ORDER BY g.detail_fetched_at DESC, g.last_seen_at DESC
                LIMIT 1
            )
            WHERE gallery_url IS NULL
              AND EXISTS (SELECT 1 FROM galleries g WHERE g.gid = hath_downloads.gid)
            """
        )
        conn.execute(
            """
            INSERT INTO gallery_feature_snapshots(
                gallery_url, captured_at, source, title, title_jpn, category, uploader,
                rating, tags_json, tag_weights_json, page_count, detail_fetched_at,
                visual_embedding_json, visual_embedding_digest,
                visual_embedding_version, visual_embedding_at
            )
            SELECT g.url, CURRENT_TIMESTAMP, 'migration-current', g.title, g.title_jpn,
                   g.category, g.uploader, g.rating, g.tags_json, g.tag_weights_json,
                   g.page_count, g.detail_fetched_at, g.visual_embedding_json,
                   g.visual_embedding_digest,
                   g.visual_embedding_version, g.visual_embedding_at
            FROM galleries g
            WHERE NOT EXISTS (
                SELECT 1 FROM gallery_feature_snapshots s WHERE s.gallery_url = g.url
            )
            """
        )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def visual_embedding_digest(raw: object) -> str | None:
    if raw is None:
        return None
    encoded = str(raw).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def backfill_visual_embedding_digests(conn: sqlite3.Connection) -> None:
    for table, key in (("galleries", "url"), ("gallery_feature_snapshots", "id")):
        rows = conn.execute(
            f"""
            SELECT {key}, visual_embedding_json
            FROM {table}
            WHERE visual_embedding_json IS NOT NULL
              AND visual_embedding_digest IS NULL
            """
        ).fetchall()
        conn.executemany(
            f"UPDATE {table} SET visual_embedding_digest = ? WHERE {key} = ?",
            [(visual_embedding_digest(row["visual_embedding_json"]), row[key]) for row in rows],
        )


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    if "tags_json" in data:
        try:
            data["tags"] = json.loads(data.pop("tags_json") or "[]")
        except json.JSONDecodeError:
            data["tags"] = []
    if "samples_json" in data:
        try:
            data["samples"] = json.loads(data.pop("samples_json") or "[]")
        except json.JSONDecodeError:
            data["samples"] = []
    if "tag_weights_json" in data:
        try:
            parsed = json.loads(data.pop("tag_weights_json") or "{}")
            data["tag_weights"] = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            data["tag_weights"] = {}
    return data


def snapshot_gallery_features(
    conn: sqlite3.Connection,
    gallery_url: str,
    source: str,
    captured_at: str | None = None,
    *,
    force: bool = False,
) -> int | None:
    row = conn.execute(
        """
        SELECT title, title_jpn, category, uploader, rating, tags_json, tag_weights_json,
               page_count, detail_fetched_at, visual_embedding_json, visual_embedding_digest,
               visual_embedding_version, visual_embedding_at
        FROM galleries
        WHERE url = ?
        """,
        (gallery_url,),
    ).fetchone()
    if row is None:
        return None
    previous = conn.execute(
        """
        SELECT title, title_jpn, category, uploader, rating, tags_json, tag_weights_json,
               page_count, detail_fetched_at, visual_embedding_json, visual_embedding_digest,
               visual_embedding_version, visual_embedding_at
        FROM gallery_feature_snapshots
        WHERE gallery_url = ?
          AND (? IS NULL OR (
              julianday(captured_at) IS NOT NULL
              AND julianday(captured_at) <= julianday(?)
          ))
        ORDER BY captured_at DESC, id DESC
        LIMIT 1
        """,
        (gallery_url, captured_at, captured_at),
    ).fetchone()
    if not force and previous is not None and tuple(previous) == tuple(row):
        return None
    cursor = conn.execute(
        """
        INSERT INTO gallery_feature_snapshots(
            gallery_url, captured_at, source, title, title_jpn, category, uploader,
            rating, tags_json, tag_weights_json, page_count, detail_fetched_at,
            visual_embedding_json, visual_embedding_digest,
            visual_embedding_version, visual_embedding_at
        ) VALUES (?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gallery_url,
            captured_at,
            str(source or "metadata")[:40],
            row["title"],
            row["title_jpn"],
            row["category"],
            row["uploader"],
            row["rating"],
            row["tags_json"],
            row["tag_weights_json"],
            row["page_count"],
            row["detail_fetched_at"],
            row["visual_embedding_json"],
            row["visual_embedding_digest"],
            row["visual_embedding_version"],
            row["visual_embedding_at"],
        ),
    )
    return int(cursor.lastrowid)
