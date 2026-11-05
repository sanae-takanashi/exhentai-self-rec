"""Prospective listing provenance and read-only query yield diagnostics.

An operation is one call to fetch_galleries(pages=1), not a count of HTTP
requests: the transport may retry and enrichment performs separate requests.
"""
from __future__ import annotations

import hashlib
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_listing_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES fetch_runs(id) ON DELETE CASCADE,
    query_key TEXT NOT NULL,
    query_text TEXT,
    source TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'started' CHECK(status IN ('started', 'completed', 'failed')),
    returned_count INTEGER,
    admitted_count INTEGER,
    new_count INTEGER,
    existing_count INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    UNIQUE(run_id, query_key, page_number)
);
CREATE TABLE IF NOT EXISTS gallery_query_provenance (
    gallery_url TEXT NOT NULL REFERENCES galleries(url) ON DELETE CASCADE,
    query_key TEXT NOT NULL,
    query_text TEXT,
    source TEXT NOT NULL,
    first_listing_id INTEGER NOT NULL REFERENCES fetch_listing_observations(id) ON DELETE CASCADE,
    first_was_new INTEGER NOT NULL CHECK(first_was_new IN (0, 1)),
    feedback_id_at_observation INTEGER NOT NULL,
    impression_id_at_observation INTEGER NOT NULL,
    first_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(gallery_url, query_key)
);
CREATE INDEX IF NOT EXISTS idx_fetch_listing_query ON fetch_listing_observations(query_key, id);
CREATE INDEX IF NOT EXISTS idx_gallery_query_key ON gallery_query_provenance(query_key, gallery_url);
"""


def _query(query: object) -> tuple[str, str | None]:
    text = str(query).strip() if query is not None else None
    text = text or None
    identity = "recent" if text is None else "search:" + text
    return hashlib.sha256(identity.encode("utf-8")).hexdigest(), text


def start_listing(conn: sqlite3.Connection, run_id: int, entry: dict, page_number: int) -> int:
    """Record an attempted listing before network I/O; caller commits promptly."""
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 0:
        raise ValueError("invalid listing page")
    key, query = _query(entry.get("query"))
    source = str(entry.get("source") or "unknown")
    if source not in {"recent", "bootstrap", "learned", "manual"}:
        source = "unknown"
    conn.execute(
        """INSERT OR IGNORE INTO fetch_listing_observations(run_id, query_key, query_text, source, page_number)
           VALUES (?, ?, ?, ?, ?)""", (run_id, key, query, source, page_number),
    )
    return int(conn.execute(
        "SELECT id FROM fetch_listing_observations WHERE run_id=? AND query_key=? AND page_number=?",
        (run_id, key, page_number),
    ).fetchone()[0])


def _urls(galleries) -> list[str]:
    urls = [item.get("url") if isinstance(item, dict) else item.url for item in galleries]
    if any(not isinstance(url, str) or not url for url in urls):
        raise ValueError("gallery URL required")
    return list(dict.fromkeys(urls))


def known_gallery_urls(conn: sqlite3.Connection, galleries) -> set[str]:
    """Call before storing a page, in the same write transaction as its store."""
    galleries = list(galleries)
    urls = _urls(galleries)
    known: set[str] = set()
    for offset in range(0, len(urls), 400):
        chunk = urls[offset:offset + 400]
        known.update(row[0] for row in conn.execute(
            "SELECT url FROM galleries WHERE url IN (" + ",".join("?" for _ in chunk) + ")", chunk,
        ))
    return known


def finish_listing(conn: sqlite3.Connection, listing_id: int, galleries=(), *,
                   existing_urls=None, returned_count: int | None = None, failed: bool = False) -> None:
    """Commit admitted-gallery provenance atomically with its normal page store.

    Repeating a finish is a no-op. Failures contain no response bodies or error
    strings. Interrupted starts remain explicitly unfinished, not successful.
    """
    row = conn.execute("SELECT * FROM fetch_listing_observations WHERE id = ?", (listing_id,)).fetchone()
    if row is None:
        raise ValueError("unknown listing observation")
    if row["status"] != "started":
        return
    if failed:
        conn.execute("""UPDATE fetch_listing_observations SET status='failed', finished_at=CURRENT_TIMESTAMP
                        WHERE id=? AND status='started'""", (listing_id,))
        return
    if existing_urls is None:
        raise ValueError("pre-store gallery membership required")
    galleries = list(galleries)
    urls = _urls(galleries)
    returned = len(urls) if returned_count is None else returned_count
    if isinstance(returned, bool) or not isinstance(returned, int) or returned < len(urls):
        raise ValueError("invalid listing count")
    existing = set(existing_urls)
    if not existing.issubset(set(urls)):
        raise ValueError("pre-store membership does not match page")
    if known_gallery_urls(conn, galleries) != set(urls):
        raise ValueError("store admitted galleries before recording provenance")
    feedback_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback").fetchone()[0])
    impression_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM recommendation_impressions").fetchone()[0])
    for url in urls:
        conn.execute(
            """INSERT OR IGNORE INTO gallery_query_provenance
               (gallery_url, query_key, query_text, source, first_listing_id, first_was_new,
                feedback_id_at_observation, impression_id_at_observation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, row["query_key"], row["query_text"], row["source"], listing_id,
             int(url not in existing), feedback_id, impression_id),
        )
    conn.execute(
        """UPDATE fetch_listing_observations SET status='completed', returned_count=?, admitted_count=?,
           new_count=?, existing_count=?, finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='started'""",
        (returned, len(urls), len(urls) - len(existing), len(existing), listing_id),
    )


def _empty(key: str, query: str | None) -> dict:
    return {
        "query_key": key, "query": query, "label": query or "Recent galleries", "sources": [],
        "listing_operations": 0, "completed_operations": 0, "failed_operations": 0,
        "unfinished_operations": 0, "returned_occurrences": 0, "admitted_occurrences": 0,
        "new_occurrences": 0, "existing_occurrences": 0,
        "recorded_galleries": 0, "new_galleries": 0, "existing_galleries": 0,
        "shared_galleries": 0, "visible_galleries": 0, "positive": 0, "negative": 0,
        "neutral": 0, "duplicate_update": 0, "unknown": 0, "new_positive": 0,
        "visible_positive": 0, "inferred_historical_galleries": 0,
        "positive_rate_explicit": None, "new_positive_per_completed_operation": None,
        "existing_occurrence_rate": None, "visible_positive_rate": None,
    }


def fetch_insights(conn: sqlite3.Connection) -> dict:
    """No writes, retraining, external requests, or fabricated exposure events."""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    rows: dict[str, dict] = {}
    for observation in conn.execute(
        """SELECT query_key, query_text, source, status, COUNT(*) AS operations,
           COALESCE(SUM(returned_count),0) AS returned, COALESCE(SUM(admitted_count),0) AS admitted,
           COALESCE(SUM(new_count),0) AS new_count, COALESCE(SUM(existing_count),0) AS existing_count
           FROM fetch_listing_observations GROUP BY query_key, query_text, source, status"""
    ):
        key = observation["query_key"]
        result = rows.setdefault(key, _empty(key, observation["query_text"]))
        if observation["source"] not in result["sources"]:
            result["sources"].append(observation["source"])
        result["listing_operations"] += observation["operations"]
        status = "unfinished" if observation["status"] == "started" else observation["status"]
        result[status + "_operations"] += observation["operations"]
        for field, column in (("returned_occurrences", "returned"), ("admitted_occurrences", "admitted"),
                              ("new_occurrences", "new_count"), ("existing_occurrences", "existing_count")):
            result[field] += observation[column]

    provenance = conn.execute(
        """SELECT p.*, f.id AS feedback_id, f.vote, f.reason_code,
           EXISTS(SELECT 1 FROM recommendation_impressions i WHERE i.gallery_url=p.gallery_url
                  AND i.id > p.impression_id_at_observation AND i.visible_at IS NOT NULL) AS visible,
           (SELECT COUNT(*) FROM gallery_query_provenance p2 WHERE p2.gallery_url=p.gallery_url) AS query_count
           FROM gallery_query_provenance p LEFT JOIN feedback f ON f.id=(
               SELECT MAX(f2.id) FROM feedback f2 WHERE f2.gallery_url=p.gallery_url)"""
    )
    for item in provenance:
        key = item["query_key"]
        result = rows.setdefault(key, _empty(key, item["query_text"]))
        result["recorded_galleries"] += 1
        result["new_galleries" if item["first_was_new"] else "existing_galleries"] += 1
        result["shared_galleries"] += int(item["query_count"] > 1)
        result["visible_galleries"] += int(item["visible"])
        if item["feedback_id"] is None or item["feedback_id"] <= item["feedback_id_at_observation"]:
            outcome = "unknown"
        elif item["reason_code"] == "duplicate_update":
            outcome = "duplicate_update"
        elif item["vote"] > 0:
            outcome = "positive"
        elif item["vote"] < 0:
            outcome = "negative"
        else:
            outcome = "neutral"
        result[outcome] += 1
        result["new_positive"] += int(outcome == "positive" and item["first_was_new"])
        result["visible_positive"] += int(outcome == "positive" and item["visible"])

    # This old column records only one possibly overwritten query. Preserve it
    # as a distinct coverage hint; do not invent old requests or yield cohorts.
    inferred = conn.execute(
        """SELECT g.source_query, COUNT(*) AS count FROM galleries g
           WHERE g.source_query IS NOT NULL AND TRIM(g.source_query) != ''
           AND NOT EXISTS(SELECT 1 FROM gallery_query_provenance p WHERE p.gallery_url=g.url)
           GROUP BY g.source_query"""
    )
    for item in inferred:
        key, query = _query(item["source_query"])
        rows.setdefault(key, _empty(key, query))["inferred_historical_galleries"] += item["count"]

    for result in rows.values():
        explicit = result["positive"] + result["negative"]
        result["explicit_nonneutral"] = explicit
        if explicit:
            result["positive_rate_explicit"] = result["positive"] / explicit
        if result["completed_operations"]:
            result["new_positive_per_completed_operation"] = result["new_positive"] / result["completed_operations"]
        if result["admitted_occurrences"]:
            result["existing_occurrence_rate"] = result["existing_occurrences"] / result["admitted_occurrences"]
        if result["visible_galleries"]:
            result["visible_positive_rate"] = result["visible_positive"] / result["visible_galleries"]
        result["sources"].sort()
    total = conn.execute("SELECT COUNT(*), COUNT(DISTINCT gallery_url) FROM gallery_query_provenance").fetchone()
    first = conn.execute("SELECT MIN(started_at) FROM fetch_listing_observations").fetchone()[0]
    return {
        "schema": "fetch-insights-v1", "read_only": True, "automatic_budget_changes": False,
        "measurement_started_at": first,
        "recorded_query_gallery_pairs": total[0], "recorded_unique_galleries": total[1],
        "cost_unit": "listing_operations_excluding_internal_retries_metadata_and_images",
        "outcome_policy": "latest_feedback_created_after_first_query_observation",
        "attribution": "shared_observational_not_causal",
        "items": sorted(rows.values(), key=lambda item: (-item["new_positive"], -item["recorded_galleries"], item["label"])),
    }
