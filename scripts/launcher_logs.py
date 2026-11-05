"""Bounded, allowlisted launcher diagnostics. Raw process output is never saved."""
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

LOG_LIMIT = 500
MAX_LOG_BYTES = 256 * 1024
MAX_OUTPUT_LINE = 8192
EVENTS = {
    "launcher.starting": ("INFO", "launcher", "Starting service."),
    "launcher.running": ("INFO", "launcher", "Service is ready."),
    "launcher.stopping": ("INFO", "launcher", "Stopping managed service."),
    "launcher.stopped": ("INFO", "launcher", "Managed service stopped."),
    "launcher.failed": ("ERROR", "launcher", "Service failed ({reason})."),
    "service.ready": ("INFO", "service", "HTTP server started."),
    "service.feedback": ("INFO", "service", "Feedback received."),
    "service.feedback-done": ("INFO", "service", "Feedback processed."),
    "service.fetch": ("INFO", "service", "Fetch progress updated."),
    "service.parent-update": ("INFO", "service", "Parent update progress updated."),
    "service.gallery-refresh": ("INFO", "service", "Gallery metadata refresh updated."),
    "service.refresh-error": ("ERROR", "service", "Background refresh failed; private details omitted."),
    "service.model-error": ("ERROR", "service", "Background model retraining failed; private details omitted."),
    "service.import-error": ("ERROR", "service", "A required Python module could not be imported."),
    "service.exception": ("ERROR", "service", "Python exception reported; private details omitted."),
    "hath.ready": ("INFO", "H@H", "Receiver configured; reverse tunnel enabled."),
    "hath.starting": ("INFO", "H@H", "Starting SSH tunnel."),
    "hath.retry": ("WARN", "H@H", "SSH tunnel exited; reconnect scheduled."),
    "hath.error": ("ERROR", "H@H", "H@H launcher failed; private details omitted."),
    "ssh.auth-error": ("ERROR", "SSH", "SSH authentication failed."),
    "ssh.connection-error": ("ERROR", "SSH", "SSH connection failed."),
    "http.request": ("INFO", "HTTP", "{method} {route} -> {status}"),
    "process.omitted": ("INFO", "service", "Additional unrecognized process output omitted."),
    "process.read-error": ("WARN", "launcher", "Process diagnostics could not be read."),
}
FAILURES = {
    "invalid-config", "config-read-failed", "venv-python-missing", "hath-token-file-missing",
    "hath-ssh-config-missing", "ssh-executable-missing", "port-occupied", "startup-timeout",
    "service-exited", "launch-failed", "cleanup-failed", "containment-unavailable", "stop-failed",
    "stop-timeout",
}
HTTP_ROUTES = {
    "/", "/api/health", "/api/status", "/api/settings", "/api/recommendations",
    "/api/feedback", "/api/feedback/undo", "/api/classification", "/api/fetch",
    "/api/refresh", "/api/integrations/hath/events", "/api/integrations/hath/status",
}
HTTP_LINE = re.compile(r'^.+ - "(GET|POST|HEAD|PUT|DELETE|OPTIONS) ([^ ]{1,4096}) HTTP/\d\.\d" ([1-5]\d\d) -$')


def log_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.stem + ".log.json")


def event_message(code, fields):
    if code not in EVENTS or not isinstance(fields, dict):
        raise ValueError("Invalid log event")
    if code == "launcher.failed":
        if set(fields) != {"reason"} or fields["reason"] not in FAILURES:
            raise ValueError("Invalid failure")
    elif code == "http.request":
        if (set(fields) != {"method", "route", "status"}
                or fields["method"] not in {"GET", "POST", "HEAD", "PUT", "DELETE", "OPTIONS"}
                or fields["route"] not in HTTP_ROUTES | {"other"}
                or type(fields["status"]) is not int or not 100 <= fields["status"] <= 599):
            raise ValueError("Invalid request")
    elif fields:
        raise ValueError("Unexpected log fields")
    level, source, message = EVENTS[code]
    if code == "http.request" and fields["status"] >= 400:
        level = "WARN"
    return level, source, message.format(**fields)


def classify_output(line: str):
    """Map known output to fixed events; never interpolate arbitrary text."""
    match = HTTP_LINE.fullmatch(line)
    if match:
        method, target, status = match.groups()
        try:
            route = urlsplit(target).path
        except ValueError:
            route = "other"
        if route in {"/api/health", "/api/fetch-progress"}:
            return None
        return "http.request", {"method": method, "route": route if route in HTTP_ROUTES else "other", "status": int(status)}
    prefixes = {
        "Serving ExHentai recommender at ": "service.ready",
        "[feedback] received ": "service.feedback",
        "[feedback] action=": "service.feedback-done",
        "[fetch] ": "service.fetch",
        "[parent-update] ": "service.parent-update",
        "[gallery-refresh] ": "service.gallery-refresh",
        "background refresh failed:": "service.refresh-error",
        "background model retrain failed:": "service.model-error",
        "ModuleNotFoundError:": "service.import-error",
        "ImportError:": "service.import-error",
        "Traceback (most recent call last):": "service.exception",
        "H@H receiver token loaded from ": "hath.ready",
        "Starting H@H SSH tunnel through ": "hath.starting",
        "H@H SSH tunnel exited with code ": "hath.retry",
        "H@H launcher error:": "hath.error",
    }
    for prefix, code in prefixes.items():
        if line.startswith(prefix):
            return code, {}
    if "Permission denied (" in line:
        return "ssh.auth-error", {}
    if line.startswith("ssh:") and any(term in line for term in ("Connection refused", "Connection timed out", "Could not resolve hostname")):
        return "ssh.connection-error", {}
    if not line or line.startswith(("SQLite data:", "Remote clients can use ")):
        return None
    return "process.omitted", {}


class RecentLog:
    def __init__(self, config_path: Path, run_id: str):
        self.path = log_path(config_path)
        self.run_id = run_id
        self.entries = deque(maxlen=LOG_LIMIT)
        self.lock = threading.Lock()
        self.version = 0
        self.saved_version = -1
        self.last_flush = 0.0

    def add(self, code: str, **fields):
        event_message(code, fields)
        with self.lock:
            self.entries.append({"at": datetime.now(timezone.utc).isoformat(), "code": code, "fields": fields})
            self.version += 1

    def flush(self, force=False):
        if not force and time.monotonic() - self.last_flush < 1:
            return
        with self.lock:
            version = self.version
            if version == self.saved_version:
                return
            payload = {"schema": 1, "run_id": self.run_id, "entries": list(self.entries)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            os.replace(temporary, self.path)
            self.saved_version = version
        except OSError:
            pass  # Diagnostic storage must not interrupt the managed service.
        self.last_flush = time.monotonic()

    def capture(self, stream):
        omitted = False
        try:
            while raw := stream.readline(MAX_OUTPUT_LINE + 1):
                if len(raw) > MAX_OUTPUT_LINE:
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(MAX_OUTPUT_LINE + 1)
                    event = ("process.omitted", {})
                else:
                    event = classify_output(raw.decode("utf-8", errors="replace").strip())
                if event:
                    code, fields = event
                    if code == "process.omitted":
                        if omitted:
                            continue
                        omitted = True
                    self.add(code, **fields)
        except Exception:
            self.add("process.read-error")
        finally:
            stream.close()


def read_log_snapshot(config_path: Path) -> dict:
    try:
        with log_path(config_path).open("rb") as file:
            raw = file.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            raise ValueError("Log too large")
        payload = json.loads(raw)
        if (payload.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{32}", payload.get("run_id", ""))
                or not isinstance(payload.get("entries"), list) or len(payload["entries"]) > LOG_LIMIT):
            raise ValueError("Invalid log snapshot")
        entries = []
        for entry in payload["entries"]:
            timestamp = datetime.fromisoformat(entry["at"])
            if timestamp.tzinfo is None:
                raise ValueError("Timestamp has no timezone")
            level, source, message = event_message(entry["code"], entry["fields"])
            entries.append({"at": timestamp.isoformat(), "code": entry["code"], "level": level,
                            "source": source, "message": message,
                            "line": f"{timestamp.astimezone():%m-%d %H:%M:%S}  {level:<5}  [{source}] {message}"})
        return {"availability": "available", "run_id": payload["run_id"], "entries": entries}
    except FileNotFoundError:
        return {"availability": "missing", "entries": []}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return {"availability": "unavailable", "entries": []}


def read_recent_logs(config_path: Path) -> list[str]:
    snapshot = read_log_snapshot(config_path)
    if snapshot["availability"] == "unavailable":
        return ["Recent logs unavailable."]
    return [entry["line"] for entry in snapshot["entries"]]
