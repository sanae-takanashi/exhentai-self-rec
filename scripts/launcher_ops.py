"""Launcher-only, bounded operational inspection and non-destructive maintenance."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import time
from urllib.parse import urlsplit
import uuid

from scripts.launcher_core import ROOT, LauncherError, atomic_json


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def local_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (ValueError, TypeError, OverflowError):
        return "Unknown"


def data_directory(config, root=ROOT):
    return config.resolve(config.data_dir or "data", root)


def regular(path):
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def local_target(path):
    path = Path(os.path.abspath(path))
    if str(path).startswith(("\\\\", "//")):
        raise LauncherError("maintenance-path-unavailable")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise LauncherError("maintenance-path-unavailable")
    for parent in (path, *path.parents):
        if parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
            raise LauncherError("maintenance-path-unavailable")
        if parent.is_symlink():
            raise LauncherError("maintenance-path-unavailable")
    return path


def database_path(config, root=ROOT):
    # Check the unresolved path too, before resolve() can hide a linked directory.
    directory = Path(config.data_dir or root / "data").expanduser()
    if not directory.is_absolute():
        directory = root / directory
    return local_target(directory / "recommender.sqlite3")


def read_database(path):
    path = local_target(path)
    try:
        present = regular(path)
    except OSError:
        present = False
    if not present:
        raise LauncherError("database-unavailable")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    conn.execute("PRAGMA query_only=ON")
    return conn


def bounded_usage(path, *, limit=20000, seconds=2):
    total = files = visited = 0
    partial = False
    deadline = time.monotonic() + seconds
    pending = [path]
    while pending and not partial:
        if time.monotonic() > deadline:
            partial = True
            break
        directory = pending.pop()
        try:
            local_target(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > limit or time.monotonic() > deadline:
                        partial = True
                        break
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                        partial = True
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        files += 1
                        total += info.st_size
        except FileNotFoundError:
            continue
        except (OSError, LauncherError):
            partial = True
    return {"bytes": total, "files": files, "partial": partial}


def inspect_storage(config, *, root=ROOT):
    database = database_path(config, root)
    directory = database.parent
    volume = directory
    while not volume.exists() and volume != volume.parent:
        volume = volume.parent
    usage = shutil.disk_usage(volume)
    rows = []
    for label, suffix in (("Database", ""), ("WAL", "-wal"), ("Shared memory", "-shm")):
        path = Path(str(database) + suffix)
        exists = path.exists()
        rows.append({"category": label, "bytes": path.stat().st_size if exists and regular(path) else 0,
                     "files": int(exists), "partial": exists and not regular(path), "missing": not exists})
    for label, folder in (("Thumbnail cache", "thumbs"), ("Backups", "backups"), ("Launcher files", "launcher")):
        rows.append({"category": label, **bounded_usage(directory / folder)})
    latest = None
    history_partial = False
    history_deadline = time.monotonic() + 2
    backup_dir = directory / "backups"
    try:
        local_target(backup_dir)
        with os.scandir(backup_dir) as entries:
            for index, entry in enumerate(entries):
                if index >= 20000 or time.monotonic() > history_deadline:
                    history_partial = True
                    break
                if not re.fullmatch(r"launcher-backup-[A-Za-z0-9-]+\.json", entry.name):
                    continue
                receipt = Path(entry.path)
                if not regular(receipt) or receipt.stat().st_size > 4096:
                    continue
                item = json.loads(receipt.read_text(encoding="utf-8"))
                name = item.get("file", "")
                if (item.get("verified") is True and re.fullmatch(r"launcher-backup-[A-Za-z0-9-]+\.sqlite3", name)
                        and (backup_dir / name).is_file() and regular(backup_dir / name)
                        and item.get("bytes") == (backup_dir / name).stat().st_size
                        and re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
                        and local_time(item.get("at")) != "Unknown"):
                    if latest is None or item["at"] > latest["at"]:
                        latest = {"at": item["at"], "bytes": item["bytes"]}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError, LauncherError):
        history_partial = True
    return {"checked_at": timestamp(), "free_bytes": usage.free, "total_bytes": usage.total,
            "rows": rows, "latest_backup": latest, "backup_history_partial": history_partial}


def check_database(config, *, root=ROOT, seconds=30):
    deadline = time.monotonic() + seconds
    try:
        with closing(read_database(database_path(config, root))) as connection:
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            ok = connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    except sqlite3.DatabaseError:
        raise LauncherError("maintenance-timeout" if time.monotonic() > deadline else "database-check-failed") from None
    if not ok:
        raise LauncherError("database-check-failed")
    return {"ok": True, "checked_at": timestamp()}


def hash_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def backup_database(config, *, root=ROOT, seconds=45):
    return backup_sqlite_database(database_path(config, root), seconds=seconds)


def backup_sqlite_database(database, *, seconds=45):
    database = local_target(database)
    directory = local_target(database.parent / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    name = "launcher-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
    target = directory / (name + ".sqlite3")
    deadline = time.monotonic() + seconds

    def progress(*_):
        if time.monotonic() > deadline:
            raise LauncherError("maintenance-timeout")

    with closing(read_database(database)) as source:
        with target.open("xb"):
            pass
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination, pages=128, progress=progress, sleep=0.05)
    # Verify through a fresh read-only connection, not the writer's return value.
    with closing(read_database(target)) as verification:
        verification.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if verification.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise LauncherError("database-check-failed")
    receipt = {"schema": 1, "file": target.name, "at": timestamp(), "verified": True,
               "sha256": hash_file(target), "bytes": target.stat().st_size}
    atomic_json(target.with_suffix(".json"), receipt)
    if json.loads(target.with_suffix(".json").read_text(encoding="utf-8")) != receipt:
        raise LauncherError("backup-readback-failed")
    return {"path": str(target), "at": receipt["at"], "verified": True}


def inspect_environment(config, *, root=ROOT):
    executable = config.service_python(root)
    if not executable.is_file():
        raise LauncherError("venv-python-missing")
    env = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC", "PATH"}}
    result = subprocess.run([str(executable), "-I", str(root / "scripts/launcher_probe.py")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=root, env=env, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > 65536:
        raise LauncherError("environment-check-failed")
    payload = json.loads(result.stdout)
    packages = ("numpy", "scipy", "scikit-learn", "joblib", "Pillow", "torch", "torchvision", "transformers", "PySocks")
    if (payload.get("schema") != 1 or not re.fullmatch(r"[0-9.]{1,30}", payload.get("python", ""))
            or payload.get("accelerator") not in {"cpu", "cuda", "rocm", "unavailable", "not-installed"}
            or set(payload.get("packages", {})) != set(packages)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", value)
                   for value in payload["packages"].values())):
        raise LauncherError("environment-check-failed")
    required = ("numpy", "scipy", "scikit-learn", "joblib", "PySocks")
    return {"schema": 1, "python": payload["python"], "accelerator": payload["accelerator"],
            "packages": {name: payload["packages"][name] for name in packages},
            "missing_required": [name for name in required if payload["packages"][name] == "missing"],
            "checked_at": timestamp()}


def check_proxy(config):
    if not config.proxy:
        return {"state": "inherited", "checked_at": timestamp()}
    config.validate()
    endpoint = urlsplit(config.proxy)
    try:
        with socket.create_connection((endpoint.hostname, endpoint.port), timeout=1.5):
            pass
        state = "reachable"
    except OSError:
        state = "unreachable"
    return {"state": state, "checked_at": timestamp()}


def observer_status(config, *, root=ROOT):
    with closing(read_database(database_path(config, root))) as connection:
        deadline = time.monotonic() + 2
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        row = connection.execute("SELECT COUNT(*), MAX(last_seen_at) FROM hath_clients").fetchone()
    return {"clients": row[0], "last_seen_at": row[1], "checked_at": timestamp()}


def export_diagnostics(config, status, *, root=ROOT):
    # Do not serialize status wholesale: it can contain local gallery titles and query text.
    config.validate()
    from exh_rec.fetch_activity import STAGES
    state = status.get("state")
    health = status.get("health")
    activity = status.get("fetch_activity", {}).get("activity", {})
    report = {"schema": 1, "at": timestamp(),
              "service": {"state": state if state in {"checking", "stopped", "starting", "stopping", "running", "external", "occupied", "failed", "unresponsive"} else "unknown",
                          "managed": status.get("managed") is True,
                          "health": health if health in {"ready", "legacy", "occupied", "stopped"} else "unknown"},
              "profile": {"hath_enabled": config.hath_enabled, "proxy_explicit": bool(config.proxy),
                          "visual_encoder": config.visual_encoder or "inherited", "device": config.dino_device or "inherited"},
              "fetch": {"running": activity.get("running") is True,
                        "stage": activity.get("stage") if activity.get("stage") in STAGES | {"idle"} else "unknown"},
              "files": {"python_exists": config.service_python(root).is_file(),
                        "database_exists": database_path(config, root).is_file()}}
    entries = status.get("log_snapshot", {}).get("entries", [])
    report["retained_log_counts"] = {level: sum(entry.get("level") == level for entry in entries)
                                     for level in ("INFO", "WARN", "ERROR")}
    for key in ("pages_done", "queries_done", "error_count", "incomplete_count"):
        value = activity.get(key)
        if type(value) is int and 0 <= value <= 10**12:
            report["fetch"][key] = value
    directory = local_target(database_path(config, root).parent / "diagnostics")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("launcher-diagnostics-" + uuid.uuid4().hex + ".json")
    atomic_json(path, report)
    if json.loads(path.read_text(encoding="utf-8")) != report:
        raise LauncherError("diagnostic-readback-failed")
    return {"path": str(path), "at": report["at"]}
