from __future__ import annotations

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
    category TEXT,
    uploader TEXT,
    posted_at TEXT,
    thumb_url TEXT,
    rating REAL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_query TEXT,
    detail_fetched_at TEXT,
    page_count INTEGER,
    samples_json TEXT NOT NULL DEFAULT '[]',
    samples_fetched_at TEXT,
    visual_embedding_json TEXT,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS feature_weights (
    feature TEXT PRIMARY KEY,
    weight REAL NOT NULL DEFAULT 0.0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_galleries_last_seen ON galleries(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_gallery ON feedback(gallery_url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_started ON fetch_runs(started_at DESC);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        ensure_column(conn, "feedback", "score", "INTEGER")
        ensure_column(conn, "galleries", "detail_fetched_at", "TEXT")
        ensure_column(conn, "galleries", "page_count", "INTEGER")
        ensure_column(conn, "galleries", "samples_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(conn, "galleries", "samples_fetched_at", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_json", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_version", "TEXT")
        ensure_column(conn, "galleries", "visual_embedding_at", "TEXT")
        ensure_column(conn, "fetch_runs", "enriched_count", "INTEGER NOT NULL DEFAULT 0")
        defaults = {
            "auto_refresh": "1",
            "refresh_interval_minutes": "30",
            "fetch_pages": "1",
            "detail_fetch_limit": "8",
            "learned_query_limit": "6",
            "recommend_candidate_limit": "2000",
            "recommend_language_filter": "japanese,chinese",
            "sample_extra_pages": "2",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
    return data
