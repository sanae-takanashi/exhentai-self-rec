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
PURGE_TRASH_CONFIRMATION = "PURGE-ARCHIVE-TRASH"
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


def scan_trash_jobs(archive_dir):
    trash_root = os.path.join(archive_dir, ".trash")
    if not os.path.exists(trash_root):
        return []
    if os.path.islink(trash_root) or not os.path.isdir(trash_root):
        raise ArchiveError("Archive trash must be a real directory: {0}".format(trash_root))
    jobs = []
    with os.scandir(trash_root) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    size_bytes, file_count, modified_at = directory_stats(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    size_bytes, file_count, modified_at = stat.st_size, 1, stat.st_mtime
                else:
                    continue
            except OSError:
                continue
            jobs.append(
                {
                    "name": entry.name,
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                    "modified_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(modified_at)
                    ),
                }
            )
    jobs.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    return jobs


def trash_stats(archive_dir):
    trash_root = os.path.join(archive_dir, ".trash")
    jobs = scan_trash_jobs(archive_dir)
    return {
        "path": trash_root,
        "size_bytes": sum(job["size_bytes"] for job in jobs),
        "file_count": sum(job["file_count"] for job in jobs),
        "job_count": len(jobs),
        "jobs": jobs,
    }


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
    cleanup_mode = str(value.get("cleanup_mode") or "trash").strip().lower()
    if cleanup_mode not in ("trash", "delete"):
        raise ArchiveError("cleanup_mode must be trash or delete")
    if trash_archive and not upload:
        raise ArchiveError("trash_archive_after_upload requires upload")
    return {
        "selected": selected,
        "archive_name": archive_name,
        "upload": upload,
        "mega_destination": destination,
        "trash_sources": trash_sources,
        "trash_archive_after_upload": trash_archive,
        "cleanup_mode": cleanup_mode,
    }


def mega_remote_file(destination, archive_name):
    folder = destination.rstrip("/") or "/"
    if folder == "/":
        return "/" + archive_name
    return folder + "/" + archive_name


def archive_member_name(candidate):
    if candidate["kind"] == "gallery":
        return candidate["name"] + ".zip"
    return candidate["name"]


def build_plan(request, download_dir, archive_dir):
    normalized = normalize_request(request)
    candidate_map = {item["name"]: item for item in scan_candidates(download_dir)}
    missing = [name for name in normalized["selected"] if name not in candidate_map]
    if missing:
        raise ArchiveError("Selected items are missing or unsupported: {0}".format(", ".join(missing)))
    selected = []
    member_names = set()
    for name in normalized["selected"]:
        candidate = dict(candidate_map[name])
        candidate["archive_member"] = archive_member_name(candidate)
        if candidate["archive_member"] in member_names:
            raise ArchiveError(
                "Selected items produce the same inner ZIP name: {0}".format(
                    candidate["archive_member"]
                )
            )
        member_names.add(candidate["archive_member"])
        selected.append(candidate)
    archive_path = os.path.join(archive_dir, normalized["archive_name"])
    if os.path.exists(archive_path):
        raise ArchiveError("Archive already exists: {0}".format(archive_path))
    gallery_count = sum(1 for item in selected if item["kind"] == "gallery")
    actions = []
    if gallery_count:
        actions.append(
            {
                "kind": "pack-galleries",
                "message": "Create {0} independent gallery ZIP(s)".format(gallery_count),
            }
        )
    actions.append(
        {
            "kind": "archive",
            "message": "Store {0} inner ZIP(s) in {1}".format(len(selected), archive_path),
        }
    )
    if normalized["upload"]:
        remote_file = mega_remote_file(
            normalized["mega_destination"], normalized["archive_name"]
        )
        actions.append(
            {
                "kind": "upload",
                "message": "Create the MEGA destination directory and upload to {0}".format(
                    remote_file
                ),
            }
        )
    if normalized["trash_sources"]:
        cleanup_action = "Permanently delete" if normalized["cleanup_mode"] == "delete" else "Move"
        cleanup_destination = "" if normalized["cleanup_mode"] == "delete" else " to the recoverable archive trash"
        actions.append(
            {
                "kind": "delete-sources" if normalized["cleanup_mode"] == "delete" else "trash-sources",
                "message": "{0} selected source items{1}".format(cleanup_action, cleanup_destination),
            }
        )
    if normalized["trash_archive_after_upload"]:
        cleanup_action = "Permanently delete" if normalized["cleanup_mode"] == "delete" else "Move"
        cleanup_destination = "" if normalized["cleanup_mode"] == "delete" else " to recoverable trash"
        actions.append(
            {
                "kind": "delete-archive" if normalized["cleanup_mode"] == "delete" else "trash-archive",
                "message": "{0} the local archive{1} after upload".format(cleanup_action, cleanup_destination),
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
        "mega_upload_path": (
            mega_remote_file(normalized["mega_destination"], normalized["archive_name"])
            if normalized["upload"]
            else None
        ),
        "actions": actions,
    }


def normalize_trash_request(value):
    raw_selected = value.get("selected")
    if not isinstance(raw_selected, list) or not raw_selected:
        raise ArchiveError("Select at least one trash job")
    if len(raw_selected) > MAX_SELECTIONS:
        raise ArchiveError("Cannot select more than {0} trash jobs".format(MAX_SELECTIONS))
    selected = []
    seen = set()
    for raw_name in raw_selected:
        name = safe_leaf_name(raw_name, "trash job")
        if name not in seen:
            selected.append(name)
            seen.add(name)
    return {"selected": selected}


def build_trash_plan(request, archive_dir):
    normalized = normalize_trash_request(request)
    job_map = {job["name"]: job for job in scan_trash_jobs(archive_dir)}
    missing = [name for name in normalized["selected"] if name not in job_map]
    if missing:
        raise ArchiveError("Trash jobs are missing or unsupported: {0}".format(", ".join(missing)))
    selected = [job_map[name] for name in normalized["selected"]]
    return {
        "schema": SCHEMA,
        "dry_run": True,
        "kind": "trash-delete",
        "request": normalized,
        "selected": selected,
        "selected_count": len(selected),
        "input_bytes": sum(job["size_bytes"] for job in selected),
        "input_files": sum(job["file_count"] for job in selected),
        "actions": [
            {
                "kind": "delete-trash-jobs",
                "message": "Permanently delete {0} selected trash job(s)".format(len(selected)),
            }
        ],
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


def permanently_delete(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        raise ArchiveError("Cleanup item is missing or unsupported: {0}".format(path))


def purge_archive_trash(archive_dir, confirmation):
    if confirmation != PURGE_TRASH_CONFIRMATION:
        raise ArchiveError("purge-trash requires --confirm {0}".format(PURGE_TRASH_CONFIRMATION))
    stats = trash_stats(archive_dir)
    trash_root = stats["path"]
    result = dict(stats)
    result["purged"] = True
    if not os.path.isdir(trash_root):
        return result
    with os.scandir(trash_root) as entries:
        paths = [entry.path for entry in entries]
    for path in paths:
        permanently_delete(path)
    return result


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


def stage_existing_zip(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def execute_plan(plan, download_dir, archive_dir):
    request = plan["request"]
    job_id = job_identifier(request)
    trash_root = os.path.join(archive_dir, ".trash", job_id)
    archive_path = plan["archive_path"]
    partial_path = os.path.join(archive_dir, ".{0}.{1}.partial".format(request["archive_name"], job_id))
    staging_dir = os.path.join(archive_dir, ".staging", job_id)
    if not os.path.isdir(archive_dir):
        os.makedirs(archive_dir)
    if os.path.exists(partial_path):
        raise ArchiveError("Partial archive already exists: {0}".format(partial_path))
    if os.path.exists(staging_dir):
        raise ArchiveError("Archive staging directory already exists: {0}".format(staging_dir))

    os.makedirs(staging_dir)
    zip_path = command_path("zip")
    inner_archives = []
    try:
        for index, item in enumerate(plan["selected"], 1):
            member_name = item["archive_member"]
            staged_path = os.path.join(staging_dir, member_name)
            if item["kind"] == "gallery":
                emit(
                    "progress",
                    stage="packing-gallery",
                    completed=index - 1,
                    total=plan["selected_count"],
                    message="Creating inner ZIP {0}".format(member_name),
                )
                run_checked(
                    [zip_path, "-r", staged_path, "--", item["name"]],
                    cwd=download_dir,
                )
            else:
                emit(
                    "progress",
                    stage="staging-zip",
                    completed=index - 1,
                    total=plan["selected_count"],
                    message="Staging existing ZIP {0}".format(member_name),
                )
                stage_existing_zip(os.path.join(download_dir, item["name"]), staged_path)
            inner_archives.append(member_name)

        emit(
            "progress",
            stage="assembling-archive",
            completed=plan["selected_count"],
            total=plan["selected_count"],
            message="Storing inner ZIPs in the outer archive",
        )
        run_checked(
            [zip_path, "-0", "-m", partial_path, "--"] + inner_archives,
            cwd=staging_dir,
        )
        os.rmdir(staging_dir)
        os.rename(partial_path, archive_path)
    except Exception:
        failed_root = os.path.join(trash_root, "failed")
        if os.path.exists(staging_dir):
            move_without_clobber(staging_dir, failed_root)
        if os.path.exists(partial_path):
            move_without_clobber(partial_path, failed_root)
        raise
    emit(
        "progress",
        stage="packed",
        completed=plan["selected_count"],
        total=plan["selected_count"],
        message="Archive created",
    )

    if request["upload"]:
        destination = request["mega_destination"].rstrip("/") or "/"
        remote_file = mega_remote_file(destination, request["archive_name"])
        emit(
            "progress",
            stage="preparing-upload",
            message="Preparing MEGA destination {0}".format(destination),
        )
        if destination != "/":
            run_checked([command_path("mega-mkdir"), "-p", destination])
        emit("progress", stage="uploading", message="Uploading archive to {0}".format(remote_file))
        output = run_checked(
            [command_path("mega-put"), archive_path, remote_file]
        )
        if output:
            emit("log", message=output[-2000:])
        emit("progress", stage="uploaded", message="MEGA upload completed")

    trashed_sources = []
    deleted_sources = []
    if request["trash_sources"]:
        if request["cleanup_mode"] == "delete":
            emit("progress", stage="deleting-sources", message="Permanently deleting selected sources")
            for name in request["selected"]:
                source_path = os.path.join(download_dir, name)
                permanently_delete(source_path)
                deleted_sources.append(source_path)
        else:
            emit("progress", stage="trashing-sources", message="Moving selected sources to recoverable trash")
            source_trash = os.path.join(trash_root, "sources")
            for name in request["selected"]:
                trashed_sources.append(move_without_clobber(os.path.join(download_dir, name), source_trash))

    retained_archive = archive_path
    trashed_archive = None
    deleted_archive = None
    if request["trash_archive_after_upload"]:
        if request["cleanup_mode"] == "delete":
            emit("progress", stage="deleting-archive", message="Permanently deleting local archive")
            permanently_delete(archive_path)
            deleted_archive = archive_path
        else:
            emit("progress", stage="trashing-archive", message="Moving local archive to recoverable trash")
            trashed_archive = move_without_clobber(archive_path, os.path.join(trash_root, "archives"))
        retained_archive = None

    result = {
        "job_id": job_id,
        "archive_path": retained_archive,
        "archive_size_bytes": os.path.getsize(retained_archive) if retained_archive else None,
        "uploaded": request["upload"],
        "mega_destination": request["mega_destination"] if request["upload"] else None,
        "mega_upload_path": (
            mega_remote_file(request["mega_destination"], request["archive_name"])
            if request["upload"]
            else None
        ),
        "trashed_sources": trashed_sources,
        "trashed_archive": trashed_archive,
        "deleted_sources": deleted_sources,
        "deleted_archive": deleted_archive,
        "trash_root": trash_root if trashed_sources or trashed_archive else None,
    }
    emit("result", state="succeeded", result=result, message="Archive job completed")


def execute_trash_plan(plan, archive_dir):
    selected = plan["request"]["selected"]
    trash_root = os.path.join(archive_dir, ".trash")
    deleted = []
    total = len(selected)
    for index, name in enumerate(selected, 1):
        emit(
            "progress",
            stage="deleting-trash",
            completed=index - 1,
            total=total,
            message="Permanently deleting trash job {0}".format(name),
        )
        permanently_delete(os.path.join(trash_root, name))
        deleted.append(name)
    result = {
        "deleted_trash_jobs": deleted,
        "freed_bytes": plan["input_bytes"],
        "deleted_files": plan["input_files"],
    }
    emit(
        "result",
        state="succeeded",
        result=result,
        message="Selected archive trash was permanently deleted",
    )


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
    result.add_argument(
        "operation",
        choices=("inspect", "plan", "run", "trash-plan", "trash-run", "purge-trash"),
    )
    result.add_argument("--download-dir", default=os.environ.get("HATH_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR))
    result.add_argument("--archive-dir", default=os.environ.get("HATH_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR))
    result.add_argument("--confirm", default="")
    return result


def main():
    args = parser().parse_args()
    try:
        if args.operation == "purge-trash":
            lock = lock_for_run()
            try:
                result = purge_archive_trash(args.archive_dir, args.confirm)
            finally:
                lock.close()
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0
        if args.operation == "inspect":
            candidates = scan_candidates(args.download_dir)
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "download_dir": args.download_dir,
                        "archive_dir": args.archive_dir,
                        "trash": trash_stats(args.archive_dir),
                        "mega": mega_identity(),
                        "candidates": candidates,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            return 0
        if args.operation in ("trash-plan", "trash-run"):
            request = read_request()
            plan = build_trash_plan(request, args.archive_dir)
            if args.operation == "trash-plan":
                print(json.dumps(plan, ensure_ascii=True, sort_keys=True))
                return 0
            lock = lock_for_run()
            try:
                execute_trash_plan(plan, args.archive_dir)
            finally:
                lock.close()
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
        if args.operation in ("run", "trash-run"):
            emit("error", state="failed", message=str(exc))
        else:
            print(json.dumps({"schema": SCHEMA, "error": str(exc)}, ensure_ascii=True))
        return 1
    except Exception as exc:
        message = "Unexpected archive failure: {0}".format(exc)
        if args.operation in ("run", "trash-run"):
            emit("error", state="failed", message=message)
        else:
            print(json.dumps({"schema": SCHEMA, "error": message}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
