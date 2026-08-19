"""Configurable remote archive helper for H@H downloads.

This module intentionally supports Python 3.6 for older H@H hosts. Commands
read JSON from stdin and write JSON/NDJSON to
stdout so the recommendation server never has to interpolate gallery names
into a shell command.
"""

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - archive execution is Linux-only
    fcntl = None


SCHEMA = "hath-archive-v1"
DEFAULT_DOWNLOAD_DIR = "/srv/hath/download"
DEFAULT_ARCHIVE_DIR = "/srv/hath/archive"
DEFAULT_MEGA_DESTINATION = "/H@H Archives"
LOCK_FILE = "/tmp/exh-rec-hath-archive.lock"
MAX_SELECTIONS = 500
MAX_NAME_BYTES = 240
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ArchiveError(RuntimeError):
    pass


def emit(event, **values):
    payload = {"schema": SCHEMA, "event": event}
    payload.update(values)
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


def read_request():
    try:
        value = json.load(sys.stdin)
    except (TypeError, ValueError) as exc:
        raise ArchiveError("Request body must be valid JSON: {0}".format(exc))
    if not isinstance(value, dict):
        raise ArchiveError("Request body must be a JSON object")
    return value


def command_output(command):
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return "", output or "command exited with code {0}".format(completed.returncode)
    return output, ""


def command_path(name):
    path = shutil.which(name)
    if not path:
        raise ArchiveError("Required executable is unavailable: {0}".format(name))
    return path


def mega_identity():
    try:
        whoami = command_path("mega-whoami")
        pwd = command_path("mega-pwd")
    except ArchiveError as exc:
        return {"available": False, "account": "", "remote_cwd": "", "error": str(exc)}
    output, error = command_output([whoami])
    account = ""
    if output:
        first_line = output.splitlines()[0].strip()
        account = first_line.split(":", 1)[-1].strip()
    remote_cwd, cwd_error = command_output([pwd])
    return {
        "available": bool(account) and not error,
        "account": account,
        "remote_cwd": remote_cwd.strip(),
        "error": error or cwd_error or None,
    }


def directory_stats(path):
    total_bytes = 0
    file_count = 0
    newest = os.path.getmtime(path)
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [
            name for name in directories if not os.path.islink(os.path.join(root, name))
        ]
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.islink(file_path):
                continue
            try:
                stat = os.stat(file_path)
            except OSError:
                continue
            total_bytes += stat.st_size
            file_count += 1
            newest = max(newest, stat.st_mtime)
    return total_bytes, file_count, newest


def scan_candidates(download_dir):
    if not os.path.isdir(download_dir):
        raise ArchiveError("Download directory does not exist: {0}".format(download_dir))
    candidates = []
    with os.scandir(download_dir) as entries:
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    size, file_count, modified_at = directory_stats(entry.path)
                    kind = "gallery"
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".zip"):
                    stat = entry.stat(follow_symlinks=False)
                    size, file_count, modified_at = stat.st_size, 1, stat.st_mtime
                    kind = "zip"
                else:
                    continue
            except OSError:
                continue
            candidates.append(
                {
                    "name": entry.name,
                    "kind": kind,
                    "size_bytes": size,
                    "file_count": file_count,
                    "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(modified_at)),
                }
            )
    candidates.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    return candidates


def safe_leaf_name(value, field):
    text = str(value or "").strip()
    if (
        not text
        or text in (".", "..")
        or os.path.basename(text) != text
        or "/" in text
        or "\\" in text
        or CONTROL_RE.search(text)
    ):
        raise ArchiveError("{0} must be a safe file name".format(field))
    if len(text.encode("utf-8")) > MAX_NAME_BYTES:
        raise ArchiveError("{0} is too long".format(field))
    return text


def normalize_request(value):
    raw_selected = value.get("selected")
    if not isinstance(raw_selected, list) or not raw_selected:
        raise ArchiveError("Select at least one gallery or ZIP")
    if len(raw_selected) > MAX_SELECTIONS:
        raise ArchiveError("Cannot select more than {0} items".format(MAX_SELECTIONS))
    selected = []
    seen = set()
    for raw_name in raw_selected:
        name = safe_leaf_name(raw_name, "selected item")
        if name not in seen:
            selected.append(name)
            seen.add(name)

    archive_name = str(value.get("archive_name") or "").strip()
    if not archive_name:
        archive_name = time.strftime("%Y%m%d.zip", time.localtime())
    archive_name = safe_leaf_name(archive_name, "archive_name")
    if not archive_name.lower().endswith(".zip"):
        archive_name += ".zip"

    upload = value.get("upload") is not False
    destination = str(value.get("mega_destination") or DEFAULT_MEGA_DESTINATION).strip()
    if upload and (
        not destination
        or not destination.startswith("/")
        or CONTROL_RE.search(destination)
        or len(destination) > 500
    ):
        raise ArchiveError("mega_destination must be an absolute MEGA path")
    trash_sources = value.get("trash_sources") is True
    trash_archive = value.get("trash_archive_after_upload") is True
    if trash_archive and not upload:
        raise ArchiveError("trash_archive_after_upload requires upload")
    return {
        "selected": selected,
        "archive_name": archive_name,
        "upload": upload,
        "mega_destination": destination,
        "trash_sources": trash_sources,
        "trash_archive_after_upload": trash_archive,
    }


def build_plan(request, download_dir, archive_dir):
    normalized = normalize_request(request)
    candidate_map = {item["name"]: item for item in scan_candidates(download_dir)}
    missing = [name for name in normalized["selected"] if name not in candidate_map]
    if missing:
        raise ArchiveError("Selected items are missing or unsupported: {0}".format(", ".join(missing)))
    selected = [candidate_map[name] for name in normalized["selected"]]
    archive_path = os.path.join(archive_dir, normalized["archive_name"])
    if os.path.exists(archive_path):
        raise ArchiveError("Archive already exists: {0}".format(archive_path))
    actions = [
        {
            "kind": "archive",
            "message": "Create {0} from {1} selected item(s)".format(archive_path, len(selected)),
        }
    ]
    if normalized["upload"]:
        actions.append(
            {
                "kind": "upload",
                "message": "Upload to MEGA destination {0}".format(normalized["mega_destination"]),
            }
        )
    if normalized["trash_sources"]:
        actions.append(
            {
                "kind": "trash-sources",
                "message": "Move selected source items to the recoverable archive trash",
            }
        )
    if normalized["trash_archive_after_upload"]:
        actions.append(
            {
                "kind": "trash-archive",
                "message": "Move the local archive to recoverable trash after upload",
            }
        )
    return {
        "schema": SCHEMA,
        "dry_run": True,
        "request": normalized,
        "selected": selected,
        "selected_count": len(selected),
        "input_bytes": sum(item["size_bytes"] for item in selected),
        "input_files": sum(item["file_count"] for item in selected),
        "archive_path": archive_path,
        "actions": actions,
    }


def job_identifier(request):
    encoded = json.dumps(request, ensure_ascii=True, sort_keys=True).encode("utf-8")
    suffix = hashlib.sha256(encoded).hexdigest()[:10]
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + suffix


def move_without_clobber(source, destination_dir):
    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)
    destination = os.path.join(destination_dir, os.path.basename(source))
    if os.path.exists(destination):
        raise ArchiveError("Trash destination already exists: {0}".format(destination))
    shutil.move(source, destination)
    return destination


def run_checked(command, cwd=None):
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise ArchiveError(
            "Command failed with code {0}: {1}".format(completed.returncode, output or command[0])
        )
    return output


def execute_plan(plan, download_dir, archive_dir):
    request = plan["request"]
    job_id = job_identifier(request)
    trash_root = os.path.join(archive_dir, ".trash", job_id)
    archive_path = plan["archive_path"]
    partial_path = os.path.join(archive_dir, ".{0}.{1}.partial".format(request["archive_name"], job_id))
    if not os.path.isdir(archive_dir):
        os.makedirs(archive_dir)
    if os.path.exists(partial_path):
        raise ArchiveError("Partial archive already exists: {0}".format(partial_path))

    emit("progress", stage="packing", completed=0, total=plan["selected_count"], message="Creating archive")
    zip_command = [command_path("zip"), "-r", partial_path, "--"] + request["selected"]
    try:
        run_checked(zip_command, cwd=download_dir)
        os.rename(partial_path, archive_path)
    except Exception:
        if os.path.exists(partial_path):
            move_without_clobber(partial_path, os.path.join(trash_root, "failed"))
        raise
    emit(
        "progress",
        stage="packed",
        completed=plan["selected_count"],
        total=plan["selected_count"],
        message="Archive created",
    )

    if request["upload"]:
        emit("progress", stage="uploading", message="Uploading archive to MEGA")
        output = run_checked(
            [command_path("mega-put"), "-c", archive_path, request["mega_destination"]]
        )
        if output:
            emit("log", message=output[-2000:])
        emit("progress", stage="uploaded", message="MEGA upload completed")

    trashed_sources = []
    if request["trash_sources"]:
        emit("progress", stage="trashing-sources", message="Moving selected sources to recoverable trash")
        source_trash = os.path.join(trash_root, "sources")
        for name in request["selected"]:
            trashed_sources.append(move_without_clobber(os.path.join(download_dir, name), source_trash))

    retained_archive = archive_path
    trashed_archive = None
    if request["trash_archive_after_upload"]:
        emit("progress", stage="trashing-archive", message="Moving local archive to recoverable trash")
        trashed_archive = move_without_clobber(archive_path, os.path.join(trash_root, "archives"))
        retained_archive = None

    result = {
        "job_id": job_id,
        "archive_path": retained_archive,
        "archive_size_bytes": os.path.getsize(retained_archive) if retained_archive else None,
        "uploaded": request["upload"],
        "mega_destination": request["mega_destination"] if request["upload"] else None,
        "trashed_sources": trashed_sources,
        "trashed_archive": trashed_archive,
        "trash_root": trash_root if trashed_sources or trashed_archive else None,
    }
    emit("result", state="succeeded", result=result, message="Archive job completed")


def lock_for_run():
    if fcntl is None:
        raise ArchiveError("Archive execution requires a platform with fcntl file locking")
    handle = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise ArchiveError("Another archive job is already running")
        raise
    return handle


def parser():
    result = argparse.ArgumentParser(description="Plan and run configurable H@H archives")
    result.add_argument("operation", choices=("inspect", "plan", "run"))
    result.add_argument("--download-dir", default=os.environ.get("HATH_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR))
    result.add_argument("--archive-dir", default=os.environ.get("HATH_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR))
    return result


def main():
    args = parser().parse_args()
    try:
        if args.operation == "inspect":
            candidates = scan_candidates(args.download_dir)
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "download_dir": args.download_dir,
                        "archive_dir": args.archive_dir,
                        "mega": mega_identity(),
                        "candidates": candidates,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            return 0
        request = read_request()
        plan = build_plan(request, args.download_dir, args.archive_dir)
        if args.operation == "plan":
            print(json.dumps(plan, ensure_ascii=True, sort_keys=True))
            return 0
        lock = lock_for_run()
        try:
            execute_plan(plan, args.download_dir, args.archive_dir)
        finally:
            lock.close()
        return 0
    except ArchiveError as exc:
        if args.operation == "run":
            emit("error", state="failed", message=str(exc))
        else:
            print(json.dumps({"schema": SCHEMA, "error": str(exc)}, ensure_ascii=True))
        return 1
    except Exception as exc:
        message = "Unexpected archive failure: {0}".format(exc)
        if args.operation == "run":
            emit("error", state="failed", message=message)
        else:
            print(json.dumps({"schema": SCHEMA, "error": message}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
