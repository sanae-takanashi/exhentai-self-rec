"""Bounded startup source identity, with no settings, database or environment dump."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def _text(path, limit=4096):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError()
    return data.decode("ascii").strip()


def local_revision(root=ROOT):
    try:
        git = root / ".git"
        if git.is_file():
            pointer = _text(git)
            if not pointer.startswith("gitdir: "):
                return None
            git = (root / pointer[8:]).resolve()
        head = _text(git / "HEAD")
        if re.fullmatch(r"[0-9a-f]{40,64}", head):
            return head
        if not head.startswith("ref: refs/"):
            return None
        ref = head[5:]
        if not re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref) or any(part in {".", ".."} for part in ref.split("/")):
            return None
        common = (git / _text(git / "commondir")).resolve() if (git / "commondir").is_file() else git
        for directory in dict.fromkeys((git, common)):
            if (directory / ref).is_file():
                value = _text(directory / ref)
                return value if re.fullmatch(r"[0-9a-f]{40,64}", value) else None
        for line in _text(common / "packed-refs", 2 * 1024 * 1024).splitlines():
            parts = line.split(" ")
            if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40,64}", parts[0]):
                return parts[0]
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def _linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def _walk_error(error):
    raise error


def source_digest(root=ROOT):
    """Hash backend Python sources, not data, web assets or the launcher's UI."""
    try:
        deadline = time.monotonic() + 2
        directory = root / "exh_rec"
        if _linked(directory):
            return None
        paths = []
        directories = 0
        for parent, folders, files in os.walk(directory, onerror=_walk_error):
            directories += 1
            if directories > 64 or time.monotonic() > deadline:
                return None
            folders[:] = sorted(name for name in folders if name != "__pycache__")
            if any(_linked(Path(parent) / name) for name in folders):
                return None
            paths.extend(Path(parent) / name for name in files if name.endswith(".py"))
            if len(paths) > 512:
                return None
        if not paths or not (directory / "app.py").is_file():
            return None
        paths.extend(root / "scripts" / name for name in ("service_bootstrap.py", "run_with_hath.py"))
        if _linked(root / "scripts"):
            return None
        digest = hashlib.sha256()
        total = 0
        for path in sorted(paths):
            if _linked(path) or not path.is_file() or time.monotonic() > deadline:
                return None
            before = path.stat()
            with path.open("rb") as stream:
                content = stream.read(2 * 1024 * 1024 + 1)
            total += len(content)
            after = path.stat()
            if (len(content) > 2 * 1024 * 1024 or total > 16 * 1024 * 1024
                    or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                return None
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(4, "big") + name + len(content).to_bytes(8, "big") + content)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def capture_source(root=ROOT):
    return {"revision": local_revision(root), "source_digest": source_digest(root)}


def capture_runtime(root=ROOT):
    return {"service": "exh-rec", "schema": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "python": ".".join(str(value) for value in sys.version_info[:3]),
            **capture_source(root)}
