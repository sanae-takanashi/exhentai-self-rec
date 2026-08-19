import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__


EVENT_SCHEMA = "hath-observer-events-v1"
DOWNLOAD_DIR_RE = re.compile(r"^(?P<title>.+) \[(?P<gid>\d+)(?:-(?P<resolution>org|\d+)x)?\]$")
FALLBACK_DIR_RE = re.compile(r"^(?P<gid>\d+)(?:-(?P<resolution>org|\d+)x)?$")
SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,200}$")
PAGE_LOG_RE = re.compile(r"Finished downloading gid=(?P<gid>\d+) page=(?P<page>\d+):")
START_LOG_PREFIX = "GalleryDownloader: Starting download of gallery: "
FAILED_LOG_PREFIX = "GalleryDownloader: Permanently failed downloading gallery: "
LOW_SPACE_TEXT = "GalleryDownloader: Download suspended; there is less than the minimum allowed space left"
IDLE_TEXT = "GalleryDownloader: Download thread finished."
LOG_TIMESTAMP_RE = r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
REQUEST_LOG_RE = re.compile(
    r"^" + LOG_TIMESTAMP_RE
    + r" .*?Code=(?P<code>\d{3})\s+Bytes=(?P<bytes>\d+)\s+GET /(?P<kind>[ht])(?:/|\s)"
)
CACHE_LOG_RE = re.compile(
    r"^" + LOG_TIMESTAMP_RE
    + r" .*?CacheHandler: Checked cache space \(cacheSize=(?P<size>\d+), "
    + r"cacheSizeWithOverhead=(?P<overhead>\d+)[, ]+cacheLimit=(?P<limit>\d+), "
    + r"cacheFree=(?P<free>\d+)\)"
)
JVM_LOG_RE = re.compile(
    r"^" + LOG_TIMESTAMP_RE
    + r" .*?memory total=(?P<total>\d+)KiB free=(?P<free>\d+)KiB max=(?P<max>\d+)KiB"
)
HANDSHAKE_INTERRUPTED_TEXT = "Remote host terminated the handshake"
METRIC_BUCKET_SECONDS = 60


class ObserverConfig:
    __slots__ = (
        "client_id",
        "download_dir",
        "state_db",
        "log_file",
        "server_url",
        "token",
        "backfill",
        "pid",
        "pid_file",
        "systemd_unit",
        "request_timeout",
        "heartbeat_interval",
    )

    def __init__(
        self,
        client_id: str,
        download_dir: Path,
        state_db: Path,
        log_file: Optional[Path] = None,
        server_url: str = "",
        token: str = "",
        backfill: bool = False,
        pid: Optional[int] = None,
        pid_file: Optional[Path] = None,
        systemd_unit: str = "",
        request_timeout: float = 15.0,
        heartbeat_interval: float = 60.0,
    ) -> None:
        self.client_id = client_id
        self.download_dir = download_dir
        self.state_db = state_db
        self.log_file = log_file
        self.server_url = server_url
        self.token = token
        self.backfill = backfill
        self.pid = pid
        self.pid_file = pid_file
        self.systemd_unit = systemd_unit
        self.request_timeout = request_timeout
        self.heartbeat_interval = heartbeat_interval


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                download_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS p2p_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                serve_requests INTEGER NOT NULL DEFAULT 0,
                serve_successes INTEGER NOT NULL DEFAULT 0,
                serve_bytes INTEGER NOT NULL DEFAULT 0,
                proxy_requests INTEGER NOT NULL DEFAULT 0,
                proxy_successes INTEGER NOT NULL DEFAULT 0,
                proxy_bytes INTEGER NOT NULL DEFAULT 0,
                handshake_interrupts INTEGER NOT NULL DEFAULT 0,
                metrics_started_at TEXT,
                last_serve_at TEXT,
                cache_size_bytes INTEGER,
                cache_size_with_overhead_bytes INTEGER,
                cache_limit_bytes INTEGER,
                cache_free_bytes INTEGER,
                jvm_memory_total_bytes INTEGER,
                jvm_memory_free_bytes INTEGER,
                jvm_memory_max_bytes INTEGER
            );
            INSERT OR IGNORE INTO p2p_state(singleton) VALUES (1);
            CREATE TABLE IF NOT EXISTS p2p_buckets (
                bucket_start INTEGER PRIMARY KEY,
                serve_requests INTEGER NOT NULL DEFAULT 0,
                serve_successes INTEGER NOT NULL DEFAULT 0,
                serve_bytes INTEGER NOT NULL DEFAULT 0,
                proxy_requests INTEGER NOT NULL DEFAULT 0,
                proxy_successes INTEGER NOT NULL DEFAULT 0,
                proxy_bytes INTEGER NOT NULL DEFAULT 0,
                handshake_interrupts INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: object) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        self.conn.commit()

    def snapshots(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for row in self.conn.execute("SELECT download_key, payload_json FROM snapshots"):
            try:
                result[str(row["download_key"])] = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
        return result

    def save_snapshot(self, key: str, payload: Dict[str, Any], updated_at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots(download_key, payload_json, updated_at) VALUES (?, ?, ?)",
            (key, compact_json(payload), updated_at),
        )

    def enqueue(self, event: Dict[str, Any]) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO outbox(event_id, payload_json, created_at) VALUES (?, ?, ?)",
            (event["event_id"], compact_json(event), utc_now()),
        )
        return cursor.rowcount > 0

    def pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event_id, payload_json FROM outbox WHERE sent_at IS NULL ORDER BY created_at, rowid LIMIT ?",
            (max(1, min(100, limit)),),
        ).fetchall()
        events = []
        for row in rows:
            try:
                events.append(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                self.mark_failed([str(row["event_id"])], "invalid locally stored JSON")
        return events

    def mark_sent(self, event_ids: Iterable[str]) -> None:
        self.conn.executemany(
            "UPDATE outbox SET sent_at = ?, attempts = attempts + 1, last_error = NULL WHERE event_id = ?",
            [(utc_now(), event_id) for event_id in event_ids],
        )
        self.conn.execute(
            """
            DELETE FROM outbox
            WHERE sent_at IS NOT NULL
              AND rowid NOT IN (
                  SELECT rowid FROM outbox WHERE sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1000
              )
            """
        )
        self.conn.commit()

    def mark_failed(self, event_ids: Iterable[str], error: str) -> None:
        self.conn.executemany(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE event_id = ?",
            [(error[:1000], event_id) for event_id in event_ids],
        )
        self.conn.commit()

    def outbox_counts(self) -> Dict[str, int]:
        row = self.conn.execute(
            """
            SELECT SUM(CASE WHEN sent_at IS NULL THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent
            FROM outbox
            """
        ).fetchone()
        return {"pending": int(row["pending"] or 0), "sent": int(row["sent"] or 0)}

    def record_request(self, kind: str, code: int, byte_count: int, timestamp: str, epoch: int) -> None:
        success = 200 <= code < 300
        bucket_start = epoch - (epoch % METRIC_BUCKET_SECONDS)
        if kind == "h":
            self.conn.execute(
                """
                UPDATE p2p_state
                SET serve_requests = serve_requests + 1,
                    serve_successes = serve_successes + ?,
                    serve_bytes = serve_bytes + ?,
                    metrics_started_at = COALESCE(metrics_started_at, ?),
                    last_serve_at = CASE WHEN ? THEN ? ELSE last_serve_at END
                WHERE singleton = 1
                """,
                (int(success), byte_count if success else 0, timestamp, success, timestamp),
            )
            self.conn.execute("INSERT OR IGNORE INTO p2p_buckets(bucket_start) VALUES (?)", (bucket_start,))
            self.conn.execute(
                """
                UPDATE p2p_buckets
                SET serve_requests = serve_requests + 1,
                    serve_successes = serve_successes + ?,
                    serve_bytes = serve_bytes + ?
                WHERE bucket_start = ?
                """,
                (int(success), byte_count if success else 0, bucket_start),
            )
        elif kind == "t":
            self.conn.execute(
                """
                UPDATE p2p_state
                SET proxy_requests = proxy_requests + 1,
                    proxy_successes = proxy_successes + ?,
                    proxy_bytes = proxy_bytes + ?,
                    metrics_started_at = COALESCE(metrics_started_at, ?)
                WHERE singleton = 1
                """,
                (int(success), byte_count if success else 0, timestamp),
            )
            self.conn.execute("INSERT OR IGNORE INTO p2p_buckets(bucket_start) VALUES (?)", (bucket_start,))
            self.conn.execute(
                """
                UPDATE p2p_buckets
                SET proxy_requests = proxy_requests + 1,
                    proxy_successes = proxy_successes + ?,
                    proxy_bytes = proxy_bytes + ?
                WHERE bucket_start = ?
                """,
                (int(success), byte_count if success else 0, bucket_start),
            )

    def record_handshake_interruption(self, timestamp: str, epoch: int) -> None:
        bucket_start = epoch - (epoch % METRIC_BUCKET_SECONDS)
        self.conn.execute(
            """
            UPDATE p2p_state
            SET handshake_interrupts = handshake_interrupts + 1,
                metrics_started_at = COALESCE(metrics_started_at, ?)
            WHERE singleton = 1
            """,
            (timestamp,),
        )
        self.conn.execute("INSERT OR IGNORE INTO p2p_buckets(bucket_start) VALUES (?)", (bucket_start,))
        self.conn.execute(
            "UPDATE p2p_buckets SET handshake_interrupts = handshake_interrupts + 1 WHERE bucket_start = ?",
            (bucket_start,),
        )

    def record_cache_metrics(self, size: int, overhead: int, limit: int, free: int) -> None:
        self.conn.execute(
            """
            UPDATE p2p_state
            SET cache_size_bytes = ?, cache_size_with_overhead_bytes = ?,
                cache_limit_bytes = ?, cache_free_bytes = ?
            WHERE singleton = 1
            """,
            (size, overhead, limit, free),
        )

    def record_jvm_metrics(self, total: int, free: int, maximum: int) -> None:
        self.conn.execute(
            """
            UPDATE p2p_state
            SET jvm_memory_total_bytes = ?, jvm_memory_free_bytes = ?,
                jvm_memory_max_bytes = ?
            WHERE singleton = 1
            """,
            (total, free, maximum),
        )

    def p2p_metrics(self, now_epoch: Optional[int] = None) -> Dict[str, Any]:
        now = int(time.time()) if now_epoch is None else int(now_epoch)
        state = self.conn.execute("SELECT * FROM p2p_state WHERE singleton = 1").fetchone()
        if not state:
            return {}

        def recent(seconds: int) -> Dict[str, int]:
            cutoff = now - seconds
            row = self.conn.execute(
                """
                SELECT COALESCE(SUM(serve_requests), 0) AS serve_requests,
                       COALESCE(SUM(serve_successes), 0) AS serve_successes,
                       COALESCE(SUM(serve_bytes), 0) AS serve_bytes,
                       COALESCE(SUM(proxy_requests), 0) AS proxy_requests,
                       COALESCE(SUM(handshake_interrupts), 0) AS handshake_interrupts
                FROM p2p_buckets
                WHERE bucket_start >= ?
                """,
                (cutoff - (cutoff % METRIC_BUCKET_SECONDS),),
            ).fetchone()
            return {key: int(row[key] or 0) for key in row.keys()}

        five_minutes = recent(5 * 60)
        one_hour = recent(60 * 60)
        self.conn.execute("DELETE FROM p2p_buckets WHERE bucket_start < ?", (now - 7 * 24 * 60 * 60,))
        result: Dict[str, Any] = {
            "serve_requests_total": int(state["serve_requests"] or 0),
            "serve_successes_total": int(state["serve_successes"] or 0),
            "serve_bytes_total": int(state["serve_bytes"] or 0),
            "serve_requests_5m": five_minutes["serve_requests"],
            "serve_successes_5m": five_minutes["serve_successes"],
            "serve_bytes_5m": five_minutes["serve_bytes"],
            "serve_requests_1h": one_hour["serve_requests"],
            "serve_successes_1h": one_hour["serve_successes"],
            "serve_bytes_1h": one_hour["serve_bytes"],
            "proxy_tests_total": int(state["proxy_requests"] or 0),
            "proxy_test_bytes_total": int(state["proxy_bytes"] or 0),
            "proxy_tests_1h": one_hour["proxy_requests"],
            "handshake_interrupts_total": int(state["handshake_interrupts"] or 0),
            "handshake_interrupts_1h": one_hour["handshake_interrupts"],
        }
        for key in (
            "metrics_started_at",
            "last_serve_at",
            "cache_size_bytes",
            "cache_size_with_overhead_bytes",
            "cache_limit_bytes",
            "cache_free_bytes",
            "jvm_memory_total_bytes",
            "jvm_memory_free_bytes",
            "jvm_memory_max_bytes",
        ):
            if state[key] is not None:
                result[key] = state[key]
        return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def parse_download_directory_name(name: str) -> Optional[Dict[str, str]]:
    match = DOWNLOAD_DIR_RE.fullmatch(name)
    if match:
        return {
            "gid": match.group("gid"),
            "resolution": match.group("resolution") or "org",
            "title": match.group("title").strip(),
        }
    match = FALLBACK_DIR_RE.fullmatch(name)
    if match:
        return {
            "gid": match.group("gid"),
            "resolution": match.group("resolution") or "org",
            "title": "",
        }
    return None


def download_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    parsed = parse_download_directory_name(path.name)
    if not parsed or not path.is_dir():
        return None
    downloaded_files = 0
    downloaded_bytes = 0
    complete = False
    try:
        for child in path.iterdir():
            if not child.is_file():
                continue
            if child.name.casefold() == "galleryinfo.txt":
                complete = True
                continue
            downloaded_files += 1
            try:
                downloaded_bytes += child.stat().st_size
            except OSError:
                pass
    except OSError:
        return None
    return {
        **parsed,
        "directory_name": path.name,
        "downloaded_files": downloaded_files,
        "downloaded_bytes": downloaded_bytes,
        "complete": complete,
    }


def scan_downloads(download_dir: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not download_dir.is_dir():
        return result
    try:
        children = sorted(download_dir.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return result
    for path in children:
        snapshot = download_snapshot(path)
        if not snapshot:
            continue
        key = f"{snapshot['gid']}:{snapshot['resolution']}"
        result[key] = snapshot
    return result


def make_event(
    client_id: str,
    event_type: str,
    occurred_at: Optional[str] = None,
    **fields: Any
) -> Dict[str, Any]:
    timestamp = occurred_at or utc_now()
    identity = compact_json([client_id, event_type, timestamp, fields])
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {
        "event_id": f"{client_id}:{digest}",
        "type": event_type,
        "occurred_at": timestamp,
        **{key: value for key, value in fields.items() if value is not None},
    }


def scan_and_enqueue(store: StateStore, config: ObserverConfig) -> Dict[str, Any]:
    now = utc_now()
    initialized = store.get_meta("scan_initialized") == "1"
    previous = store.snapshots()
    current = scan_downloads(config.download_dir)
    emitted = 0
    for key, snapshot in current.items():
        old = previous.get(key)
        should_report_new = initialized or config.backfill
        event_type = None
        if old is None and should_report_new:
            event_type = "download.completed" if snapshot["complete"] else "download.discovered"
        elif old is not None and not old.get("complete") and snapshot["complete"]:
            event_type = "download.completed"
        elif old is not None and not snapshot["complete"] and (
            snapshot["downloaded_files"] != old.get("downloaded_files")
            or snapshot["downloaded_bytes"] != old.get("downloaded_bytes")
        ):
            event_type = "download.progress"
        if event_type:
            fields = {key: value for key, value in snapshot.items() if key != "complete"}
            if snapshot["complete"]:
                fields["total_files"] = snapshot["downloaded_files"]
            emitted += int(store.enqueue(make_event(config.client_id, event_type, occurred_at=now, **fields)))
        store.save_snapshot(key, snapshot, now)
    store.set_meta("scan_initialized", "1")

    log_events = consume_log(store, config, current)
    for event in log_events:
        emitted += int(store.enqueue(event))

    disk_free = None
    try:
        disk_free = shutil.disk_usage(config.download_dir).free
    except OSError:
        pass
    running = configured_process_running(config)
    heartbeat_now = time.time()
    last_heartbeat = float(store.get_meta("last_heartbeat", "0") or 0)
    if heartbeat_now - last_heartbeat >= max(5.0, config.heartbeat_interval):
        metrics = {
            "downloads_seen": len(current),
            "active_downloads": sum(not item["complete"] for item in current.values()),
            "log_available": bool(config.log_file and config.log_file.is_file()),
        }
        metrics.update(store.p2p_metrics(int(heartbeat_now)))
        process_uptime = configured_process_uptime(config) if running else None
        if process_uptime is not None:
            metrics["process_uptime_seconds"] = process_uptime
        heartbeat_fields: Dict[str, Any] = {
            "hostname": socket.gethostname(),
            "agent_version": __version__,
            "disk_free_bytes": disk_free,
            "metrics": metrics,
        }
        if running is not None:
            heartbeat_fields["process_running"] = running
        emitted += int(store.enqueue(make_event(config.client_id, "agent.heartbeat", occurred_at=now, **heartbeat_fields)))
        store.set_meta("last_heartbeat", time.time())
    store.conn.commit()
    return {"downloads": len(current), "events": emitted, "process_running": running, **store.outbox_counts()}


def consume_log(
    store: StateStore,
    config: ObserverConfig,
    snapshots: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    path = config.log_file
    if not path or not path.is_file():
        return []
    try:
        stat = path.stat()
    except OSError:
        return []
    identity = f"{stat.st_dev}:{stat.st_ino}"
    previous_identity = store.get_meta("log_identity")
    raw_offset = store.get_meta("log_offset", "")
    if not raw_offset:
        offset = stat.st_size
    else:
        try:
            offset = int(raw_offset)
        except ValueError:
            offset = 0
    if (previous_identity and previous_identity != identity) or stat.st_size < offset:
        offset = 0
    if store.get_meta("p2p_initialized") != "1":
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                baseline_lines = handle.read(offset).splitlines(True)
        except OSError:
            baseline_lines = []
        observe_log_lines(store, baseline_lines)
        store.set_meta("p2p_initialized", "1")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            lines = handle.readlines()
            offset = handle.tell()
    except OSError:
        return []
    store.set_meta("log_identity", identity)
    store.set_meta("log_offset", offset)
    observe_log_lines(store, lines)
    return parse_log_lines(config.client_id, lines, snapshots)


def parse_log_epoch(timestamp: str) -> Optional[int]:
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp())


def observe_log_lines(store: StateStore, lines: Iterable[str]) -> None:
    for raw in lines:
        line = raw.strip()
        request = REQUEST_LOG_RE.search(line)
        if request:
            timestamp = request.group("timestamp")
            epoch = parse_log_epoch(timestamp)
            if epoch is not None:
                store.record_request(
                    request.group("kind"),
                    int(request.group("code")),
                    int(request.group("bytes")),
                    timestamp,
                    epoch,
                )
            continue
        cache = CACHE_LOG_RE.search(line)
        if cache:
            store.record_cache_metrics(
                int(cache.group("size")),
                int(cache.group("overhead")),
                int(cache.group("limit")),
                int(cache.group("free")),
            )
            continue
        jvm = JVM_LOG_RE.search(line)
        if jvm:
            store.record_jvm_metrics(
                int(jvm.group("total")) * 1024,
                int(jvm.group("free")) * 1024,
                int(jvm.group("max")) * 1024,
            )
            continue
        if HANDSHAKE_INTERRUPTED_TEXT in line:
            timestamp_match = re.match(r"^" + LOG_TIMESTAMP_RE, line)
            if timestamp_match:
                timestamp = timestamp_match.group("timestamp")
                epoch = parse_log_epoch(timestamp)
                if epoch is not None:
                    store.record_handshake_interruption(timestamp, epoch)


def parse_log_lines(
    client_id: str,
    lines: Iterable[str],
    snapshots: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    now = utc_now()
    by_title = {item.get("title"): item for item in snapshots.values() if item.get("title")}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if START_LOG_PREFIX in line:
            title = line.split(START_LOG_PREFIX, 1)[1].strip()
            item = by_title.get(title)
            if item:
                fields = {key: value for key, value in item.items() if key != "complete"}
                events.append(make_event(client_id, "download.started", occurred_at=now, **fields))
            continue
        page = PAGE_LOG_RE.search(line)
        if page:
            gid = page.group("gid")
            item = next((value for value in snapshots.values() if value["gid"] == gid), None)
            if item:
                events.append(
                    make_event(
                        client_id,
                        "download.progress",
                        occurred_at=now,
                        gid=gid,
                        resolution=item["resolution"],
                        title=item.get("title"),
                        directory_name=item.get("directory_name"),
                        downloaded_files=max(int(page.group("page")), int(item["downloaded_files"])),
                        downloaded_bytes=item["downloaded_bytes"],
                    )
                )
            continue
        if FAILED_LOG_PREFIX in line:
            title = line.split(FAILED_LOG_PREFIX, 1)[1].strip()
            item = by_title.get(title)
            if item:
                fields = {key: value for key, value in item.items() if key != "complete"}
                events.append(make_event(client_id, "download.failed", occurred_at=now, message=line, **fields))
            continue
        if LOW_SPACE_TEXT in line:
            events.append(
                make_event(
                    client_id,
                    "client.status",
                    occurred_at=now,
                    message="H@H download suspended because the download disk is low on space",
                    metrics={"status": "suspended-low-space"},
                )
            )
        elif IDLE_TEXT in line:
            events.append(
                make_event(client_id, "client.status", occurred_at=now, metrics={"status": "idle"})
            )
    return events


def configured_process_running(config: ObserverConfig) -> Optional[bool]:
    if config.systemd_unit:
        if not SYSTEMD_UNIT_RE.fullmatch(config.systemd_unit):
            return False
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", config.systemd_unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.returncode == 0
    pid = config.pid
    if config.pid_file:
        try:
            pid = int(config.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
    if pid is None:
        return None
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def configured_process_uptime(config: ObserverConfig) -> Optional[int]:
    if not config.systemd_unit or not SYSTEMD_UNIT_RE.fullmatch(config.systemd_unit):
        return None
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                config.systemd_unit,
                "--property=ActiveEnterTimestampMonotonic",
                "--value",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            universal_newlines=True,
        )
        boot_uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        active_at = int(result.stdout.strip()) / 1000000.0
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or active_at <= 0 or active_at > boot_uptime:
        return None
    return max(0, int(boot_uptime - active_at))


def send_pending(store: StateStore, config: ObserverConfig) -> Dict[str, Any]:
    events = store.pending()
    if not events or not config.server_url:
        return {"sent": 0, "pending": len(events), "error": None}
    endpoint = config.server_url.rstrip("/") + "/api/integrations/hath/events"
    body = compact_json(
        {
            "schema": EVENT_SCHEMA,
            "client_id": config.client_id,
            "sent_at": utc_now(),
            "events": events,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": f"hath-observer/{__version__}"}
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    event_ids = [str(event["event_id"]) for event in events]
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError("receiver returned a response without ok=true")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        store.mark_failed(event_ids, str(exc))
        return {"sent": 0, "pending": len(events), "error": str(exc)}
    store.mark_sent(event_ids)
    return {"sent": len(events), "pending": store.outbox_counts()["pending"], "error": None}
