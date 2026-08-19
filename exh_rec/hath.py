from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


EVENT_SCHEMA = "hath-observer-events-v1"
EVENT_TYPES = frozenset(
    {
        "agent.heartbeat",
        "client.status",
        "download.discovered",
        "download.started",
        "download.progress",
        "download.completed",
        "download.failed",
    }
)
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
RESOLUTION_RE = re.compile(r"^(?:org|\d{2,5})$")
MAX_EVENTS_PER_BATCH = 100
MAX_EVENT_BYTES = 64 * 1024
SAFE_METRIC_KEYS = frozenset(
    {
        "status",
        "downloads_seen",
        "active_downloads",
        "log_available",
        "serve_requests_total",
        "serve_successes_total",
        "serve_bytes_total",
        "serve_requests_5m",
        "serve_successes_5m",
        "serve_bytes_5m",
        "serve_requests_1h",
        "serve_successes_1h",
        "serve_bytes_1h",
        "proxy_tests_total",
        "proxy_test_bytes_total",
        "proxy_tests_1h",
        "handshake_interrupts_total",
        "handshake_interrupts_1h",
        "metrics_started_at",
        "last_serve_at",
        "cache_size_bytes",
        "cache_size_with_overhead_bytes",
        "cache_limit_bytes",
        "cache_free_bytes",
        "jvm_memory_total_bytes",
        "jvm_memory_free_bytes",
        "jvm_memory_max_bytes",
        "process_uptime_seconds",
    }
)
DOWNLOAD_EVENT_STATUS = {
    "download.discovered": "discovered",
    "download.started": "downloading",
    "download.progress": "downloading",
    "download.completed": "completed",
    "download.failed": "failed",
}


class HathPayloadError(ValueError):
    pass


def ingest_event_batch(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != EVENT_SCHEMA:
        raise HathPayloadError(f"schema must be {EVENT_SCHEMA}")
    client_id = normalize_client_id(payload.get("client_id"))
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise HathPayloadError("events must be a non-empty list")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HathPayloadError(f"events cannot contain more than {MAX_EVENTS_PER_BATCH} items")

    accepted = 0
    duplicates = 0
    completed_changed = False
    _upsert_client(conn, client_id, {})
    for raw_event in events:
        event = normalize_event(raw_event)
        encoded = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            raise HathPayloadError("event payload is too large")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO hath_events(
                event_id, client_id, event_type, gid, resolution, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                client_id,
                event["type"],
                event.get("gid"),
                event.get("resolution"),
                encoded,
                event["occurred_at"],
            ),
        )
        if cursor.rowcount == 0:
            duplicates += 1
            continue
        accepted += 1
        _apply_event(conn, client_id, event)
        completed_changed = completed_changed or event["type"] == "download.completed"

    return {
        "ok": True,
        "client_id": client_id,
        "accepted": accepted,
        "duplicates": duplicates,
        "completed_changed": completed_changed,
    }


def normalize_client_id(value: object) -> str:
    client_id = str(value or "").strip()
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise HathPayloadError("client_id must contain only letters, digits, dot, colon, underscore, or hyphen")
    return client_id


def normalize_event(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HathPayloadError("each event must be an object")
    event_type = str(value.get("type") or "").strip().lower()
    if event_type not in EVENT_TYPES:
        raise HathPayloadError(f"unsupported event type: {event_type or '<empty>'}")
    event_id = str(value.get("event_id") or "").strip()
    if not event_id or len(event_id) > 200:
        raise HathPayloadError("event_id must be between 1 and 200 characters")
    occurred_at = normalize_timestamp(value.get("occurred_at"))
    event: dict[str, Any] = {
        "event_id": event_id,
        "type": event_type,
        "occurred_at": occurred_at,
    }

    if event_type.startswith("download."):
        gid = str(value.get("gid") or "").strip()
        if not gid.isdigit() or len(gid) > 20:
            raise HathPayloadError("download events require a numeric gid")
        resolution = str(value.get("resolution") or "org").strip().lower()
        if not RESOLUTION_RE.fullmatch(resolution):
            raise HathPayloadError("resolution must be org or a numeric width")
        event.update({"gid": gid, "resolution": resolution})

    for key, limit in (
        ("title", 500),
        ("directory_name", 500),
        ("hostname", 255),
        ("agent_version", 80),
        ("message", 1000),
        ("active_gid", 20),
        ("active_title", 500),
    ):
        if value.get(key) is not None:
            event[key] = str(value[key]).strip()[:limit]
    for key in ("total_files", "downloaded_files", "downloaded_bytes", "disk_free_bytes"):
        if value.get(key) is not None:
            event[key] = nonnegative_int(value[key], key)
    if value.get("process_running") is not None:
        if not isinstance(value["process_running"], bool):
            raise HathPayloadError("process_running must be a boolean")
        event["process_running"] = value["process_running"]
    if isinstance(value.get("metrics"), dict):
        event["metrics"] = json_safe_metrics(value["metrics"])
    return event


def normalize_timestamp(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise HathPayloadError("occurred_at is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HathPayloadError("occurred_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise HathPayloadError(f"{name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HathPayloadError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise HathPayloadError(f"{name} must be a non-negative integer")
    return parsed


def json_safe_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in list(metrics.items())[:40]:
        key = str(raw_key)[:80]
        if key not in SAFE_METRIC_KEYS:
            continue
        if isinstance(value, bool) or value is None or isinstance(value, str):
            result[key] = value if not isinstance(value, str) else value[:500]
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            result[key] = value
    return result


def _apply_event(conn: sqlite3.Connection, client_id: str, event: dict[str, Any]) -> None:
    _upsert_client(conn, client_id, event)
    if not event["type"].startswith("download."):
        return
    gid = event["gid"]
    resolution = event["resolution"]
    existing = conn.execute(
        "SELECT * FROM hath_downloads WHERE client_id = ? AND gid = ? AND resolution = ?",
        (client_id, gid, resolution),
    ).fetchone()
    if existing and str(existing["updated_at"]) > event["occurred_at"]:
        return
    gallery = conn.execute(
        "SELECT url FROM galleries WHERE gid = ? ORDER BY detail_fetched_at DESC, last_seen_at DESC LIMIT 1",
        (gid,),
    ).fetchone()
    status = DOWNLOAD_EVENT_STATUS[event["type"]]
    started_at = event["occurred_at"] if status == "downloading" else None
    completed_at = event["occurred_at"] if status == "completed" else None
    failed_at = event["occurred_at"] if status == "failed" else None
    conn.execute(
        """
        INSERT INTO hath_downloads(
            client_id, gid, resolution, gallery_url, title, status, directory_name,
            total_files, downloaded_files, downloaded_bytes, started_at, completed_at,
            failed_at, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(client_id, gid, resolution) DO UPDATE SET
            gallery_url = COALESCE(excluded.gallery_url, hath_downloads.gallery_url),
            title = COALESCE(excluded.title, hath_downloads.title),
            status = excluded.status,
            directory_name = COALESCE(excluded.directory_name, hath_downloads.directory_name),
            total_files = COALESCE(excluded.total_files, hath_downloads.total_files),
            downloaded_files = MAX(hath_downloads.downloaded_files, excluded.downloaded_files),
            downloaded_bytes = MAX(hath_downloads.downloaded_bytes, excluded.downloaded_bytes),
            started_at = COALESCE(hath_downloads.started_at, excluded.started_at),
            completed_at = COALESCE(excluded.completed_at, hath_downloads.completed_at),
            failed_at = COALESCE(excluded.failed_at, hath_downloads.failed_at),
            last_error = COALESCE(excluded.last_error, hath_downloads.last_error),
            updated_at = excluded.updated_at
        """,
        (
            client_id,
            gid,
            resolution,
            gallery["url"] if gallery else None,
            event.get("title") or None,
            status,
            event.get("directory_name") or None,
            event.get("total_files"),
            event.get("downloaded_files", 0),
            event.get("downloaded_bytes", 0),
            started_at,
            completed_at,
            failed_at,
            event.get("message") or None,
            event["occurred_at"],
        ),
    )


def _upsert_client(conn: sqlite3.Connection, client_id: str, event: dict[str, Any]) -> None:
    metrics = dict(event.get("metrics") or {})
    for key in ("disk_free_bytes", "process_running"):
        if key in event:
            metrics[key] = event[key]
    status = None
    event_type = str(event.get("type") or "")
    if event_type == "agent.heartbeat" and "process_running" in event:
        status = "running" if event["process_running"] else "offline"
    elif event_type == "client.status":
        status = str(metrics.get("status") or "unknown")[:40]
    clear_active = (
        (event_type == "client.status" and status == "idle")
        or (event_type == "agent.heartbeat" and metrics.get("active_downloads") == 0)
    )
    conn.execute(
        """
        INSERT INTO hath_clients(
            client_id, status, hostname, agent_version, active_gid, active_title,
            metrics_json, last_error, last_event_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(client_id) DO UPDATE SET
            status = CASE WHEN excluded.status = 'unknown' THEN hath_clients.status ELSE excluded.status END,
            hostname = COALESCE(excluded.hostname, hath_clients.hostname),
            agent_version = COALESCE(excluded.agent_version, hath_clients.agent_version),
            active_gid = CASE WHEN ? THEN NULL ELSE COALESCE(excluded.active_gid, hath_clients.active_gid) END,
            active_title = CASE WHEN ? THEN NULL ELSE COALESCE(excluded.active_title, hath_clients.active_title) END,
            metrics_json = CASE WHEN excluded.metrics_json = '{}' THEN hath_clients.metrics_json ELSE excluded.metrics_json END,
            last_error = COALESCE(excluded.last_error, hath_clients.last_error),
            last_event_at = COALESCE(excluded.last_event_at, hath_clients.last_event_at),
            last_seen_at = CURRENT_TIMESTAMP
        """,
        (
            client_id,
            status or "unknown",
            event.get("hostname") or None,
            event.get("agent_version") or None,
            event.get("active_gid") or event.get("gid") or None,
            event.get("active_title") or event.get("title") or None,
            json.dumps(metrics, ensure_ascii=True, separators=(",", ":")),
            event.get("message") if event_type in {"client.status", "download.failed"} else None,
            event.get("occurred_at"),
            clear_active,
            clear_active,
        ),
    )


def hath_status(conn: sqlite3.Connection, recent_limit: int = 100) -> dict[str, Any]:
    download_stats = {
        str(row["client_id"]): dict(row)
        for row in conn.execute(
            """
            SELECT client_id,
                   COUNT(*) AS tracked_downloads,
                   SUM(status = 'completed') AS completed_downloads,
                   SUM(status = 'failed') AS failed_downloads,
                   SUM(status IN ('discovered', 'downloading')) AS active_downloads,
                   COALESCE(SUM(downloaded_files), 0) AS downloaded_files,
                   COALESCE(SUM(downloaded_bytes), 0) AS downloaded_bytes,
                   MIN(COALESCE(started_at, completed_at, updated_at)) AS first_download_at,
                   MAX(updated_at) AS last_download_at
            FROM hath_downloads
            GROUP BY client_id
            """
        )
    }
    event_stats = {
        str(row["client_id"]): dict(row)
        for row in conn.execute(
            """
            SELECT e.client_id, COUNT(*) AS event_count,
                   MAX(e.received_at) AS last_event_received_at,
                   (
                       SELECT latest.event_type
                       FROM hath_events latest
                       WHERE latest.client_id = e.client_id
                       ORDER BY latest.id DESC
                       LIMIT 1
                   ) AS last_event_type
            FROM hath_events e
            GROUP BY e.client_id
            """
        )
    }
    clients = []
    for row in conn.execute("SELECT * FROM hath_clients ORDER BY last_seen_at DESC"):
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {}
        item["statistics"] = {
            "tracked_downloads": 0,
            "completed_downloads": 0,
            "failed_downloads": 0,
            "active_downloads": 0,
            "downloaded_files": 0,
            "downloaded_bytes": 0,
            "first_download_at": None,
            "last_download_at": None,
            "event_count": 0,
            "last_event_received_at": None,
            "last_event_type": None,
            **download_stats.get(str(item["client_id"]), {}),
            **event_stats.get(str(item["client_id"]), {}),
        }
        clients.append(item)
    downloads = [
        dict(row)
        for row in conn.execute(
            """
            WITH latest_feedback AS (
                SELECT f.gallery_url, f.vote, f.score
                FROM feedback f
                JOIN (
                    SELECT gallery_url, MAX(id) AS id
                    FROM feedback
                    GROUP BY gallery_url
                ) latest ON latest.id = f.id
            )
            SELECT d.*, g.token, COALESCE(g.title, d.title) AS gallery_title,
                   g.category, g.uploader, g.page_count, g.rating,
                   latest_feedback.vote AS feedback_vote,
                   latest_feedback.score AS feedback_score,
                   marks.kind AS mark_kind,
                   CASE
                       WHEN marks.kind = 'favorite' THEN 'favorite'
                       WHEN marks.kind = 'ban' THEN 'ban'
                       WHEN latest_feedback.vote > 0 THEN 'positive-vote'
                       WHEN latest_feedback.vote < 0 THEN 'negative-vote'
                       WHEN d.status = 'completed' AND d.gallery_url IS NOT NULL THEN 'hath-download'
                       ELSE NULL
                   END AS recommendation_signal
            FROM hath_downloads d
            LEFT JOIN galleries g ON g.url = d.gallery_url
            LEFT JOIN latest_feedback ON latest_feedback.gallery_url = d.gallery_url
            LEFT JOIN gallery_marks marks ON marks.gallery_url = d.gallery_url
            ORDER BY d.updated_at DESC
            LIMIT ?
            """,
            (max(1, min(100, int(recent_limit))),),
        )
    ]
    counts = {
        str(row["status"]): int(row["count"])
        for row in conn.execute("SELECT status, COUNT(*) AS count FROM hath_downloads GROUP BY status")
    }
    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS tracked_downloads,
               COALESCE(SUM(downloaded_files), 0) AS downloaded_files,
               COALESCE(SUM(downloaded_bytes), 0) AS downloaded_bytes,
               SUM(gallery_url IS NOT NULL) AS linked_downloads,
               SUM(gallery_url IS NULL) AS unlinked_downloads,
               MIN(COALESCE(started_at, completed_at, updated_at)) AS first_download_at,
               MAX(updated_at) AS last_download_at
        FROM hath_downloads
        """
    ).fetchone()
    signal_counts = {
        str(row["signal"]): int(row["count"])
        for row in conn.execute(
            """
            WITH latest_feedback AS (
                SELECT f.gallery_url, f.vote
                FROM feedback f
                JOIN (
                    SELECT gallery_url, MAX(id) AS id
                    FROM feedback
                    GROUP BY gallery_url
                ) latest ON latest.id = f.id
            ), resolved AS (
                SELECT CASE
                           WHEN marks.kind = 'favorite' THEN 'favorite'
                           WHEN marks.kind = 'ban' THEN 'ban'
                           WHEN latest_feedback.vote > 0 THEN 'positive-vote'
                           WHEN latest_feedback.vote < 0 THEN 'negative-vote'
                           WHEN d.status = 'completed' AND d.gallery_url IS NOT NULL THEN 'hath-download'
                           ELSE NULL
                       END AS signal
                FROM hath_downloads d
                LEFT JOIN latest_feedback ON latest_feedback.gallery_url = d.gallery_url
                LEFT JOIN gallery_marks marks ON marks.gallery_url = d.gallery_url
            )
            SELECT signal, COUNT(*) AS count
            FROM resolved
            WHERE signal IS NOT NULL
            GROUP BY signal
            """
        )
    }
    event_types = {
        str(row["event_type"]): int(row["count"])
        for row in conn.execute(
            "SELECT event_type, COUNT(*) AS count FROM hath_events GROUP BY event_type ORDER BY event_type"
        )
    }
    event_row = conn.execute(
        "SELECT COUNT(*) AS event_count, MAX(received_at) AS last_event_received_at FROM hath_events"
    ).fetchone()
    summary = dict(summary_row) if summary_row else {}
    summary.update(
        {
            "event_count": int(event_row["event_count"] or 0) if event_row else 0,
            "last_event_received_at": event_row["last_event_received_at"] if event_row else None,
            "signals": signal_counts,
            "event_types": event_types,
        }
    )
    return {"clients": clients, "downloads": downloads, "counts": counts, "summary": summary}
