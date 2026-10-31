"""Read-only H@H download audit. Never reads client keys, cookies, or cache."""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"})
HASH_FILE_LIMIT = 32 * 1024 * 1024
HASH_TOTAL_LIMIT = 256 * 1024 * 1024


def scan_library(root, parse_name):
    """Inspect complete galleries and at most three representative files each."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid_download_root")
    try:
        from PIL import Image
    except ImportError:
        Image = None
    galleries = []
    hashes = Counter()
    resolutions = Counter()
    dimensions = Counter()
    extensions = Counter()
    sampled = hashed_bytes = dimension_failures = hash_skipped = 0
    incomplete = skipped = sample_bytes = 0
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(".") or directory.is_symlink() or not directory.is_dir():
            continue
        identity = parse_name(directory.name)
        if not identity:
            skipped += 1
            continue
        marker = directory / "galleryinfo.txt"
        if marker.is_symlink() or not marker.is_file():
            incomplete += 1
            continue
        images = sorted(
            child for child in directory.iterdir()
            if not child.name.startswith(".") and not child.is_symlink() and child.is_file()
            and child.suffix.lower() in IMAGE_SUFFIXES
        )
        sizes = [child.stat().st_size for child in images]
        with marker.open("rb") as stream:
            info = stream.read(32768).decode("utf-8", errors="replace")
        marker_gids = re.findall(r"https?://(?:e-hentai|exhentai)\.org/g/(\d+)/[0-9a-f]+", info)
        extensions.update(child.suffix.lower() for child in images)
        resolutions.update([identity["resolution"]])
        galleries.append({
            "gid": identity["gid"], "resolution": identity["resolution"],
            "image_count": len(images), "image_bytes": sum(sizes),
            "marker_has_matching_gallery_url": identity["gid"] in marker_gids,
        })
        # Deterministic positions are a feasibility sample, not representative training data.
        positions = sorted({0, len(images) // 2, len(images) - 1}) if images else []
        for index in positions:
            image = images[index]
            size = sizes[index]
            sampled += 1
            sample_bytes += size
            if size <= HASH_FILE_LIMIT and hashed_bytes + size <= HASH_TOTAL_LIMIT:
                digest = hashlib.sha256()
                with image.open("rb") as stream:
                    for block in iter(lambda: stream.read(65536), b""):
                        digest.update(block)
                hashes.update([digest.hexdigest()])
                hashed_bytes += size
            else:
                hash_skipped += 1
            if Image is not None:
                try:
                    with Image.open(image) as decoded:
                        dimensions.update(["{}x{}".format(*decoded.size)])
                except (OSError, ValueError, Image.DecompressionBombError):
                    dimension_failures += 1
    return {
        "galleries": galleries,
        "summary": {
            "complete_galleries": len(galleries), "incomplete_galleries": incomplete,
            "unrecognized_directories": skipped,
            "image_count": sum(g["image_count"] for g in galleries),
            "image_bytes": sum(g["image_bytes"] for g in galleries),
            "resolutions": dict(resolutions), "extensions": dict(extensions),
            "sample_count": sampled, "sample_bytes": sample_bytes,
            "sample_dimensions": dict(dimensions), "pillow_available": Image is not None,
            "dimension_failures": dimension_failures, "hashed_bytes": hashed_bytes,
            "hash_skipped": hash_skipped,
            "sample_exact_duplicate_groups": sum(n > 1 for n in hashes.values()),
            "sample_exact_duplicate_extra_files": sum(n - 1 for n in hashes.values()),
        },
    }


def audit_database(path, remote):
    """Open SQLite in read-only mode and select only library coverage fields."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        downloads = list(conn.execute(
            "SELECT gid, resolution, gallery_url FROM hath_downloads WHERE status = 'completed'"
        ))
        gids = sorted({row["gid"] for row in downloads} |
                      {row["gid"] for row in remote["galleries"]})
        rows = {}
        for offset in range(0, len(gids), 200):
            batch = gids[offset:offset + 200]
            query = """
                SELECT g.gid, g.page_count, g.samples_json,
                       g.visual_embedding_json IS NOT NULL AS has_visual,
                       EXISTS (SELECT 1 FROM gallery_visual_images v
                               WHERE v.gallery_url = g.url) AS has_page_vectors
                FROM galleries g WHERE g.gid IN ({})
                ORDER BY g.detail_fetched_at, g.last_seen_at
            """.format(",".join("?" for _ in batch))
            for row in conn.execute(query, batch):
                rows[row["gid"]] = dict(row)
    current = remote["galleries"]
    covered = [rows[g["gid"]] for g in current if g["gid"] in rows]
    samples = 0
    for row in covered:
        try:
            entries = json.loads(row["samples_json"] or "[]")
        except (TypeError, ValueError):
            entries = []
        samples += int(isinstance(entries, list) and bool(entries))
    tracked_keys = {(row["gid"], row["resolution"]) for row in downloads}
    page_count_deltas = [
        g for g in current if g["gid"] in rows
        and rows[g["gid"]]["page_count"] is not None
        and g["image_count"] != rows[g["gid"]]["page_count"]
    ]
    return {
        "completed_records": len(downloads),
        "linked_records": sum(bool(row["gallery_url"]) for row in downloads),
        "unlinked_records": sum(not row["gallery_url"] for row in downloads),
        "historical_resolutions": dict(Counter(row["resolution"] for row in downloads)),
        "current_galleries_in_metadata": len(covered),
        "current_galleries_missing_metadata": len(current) - len(covered),
        "missing_metadata_with_gallery_url_in_marker": sum(
            bool(g.get("marker_has_matching_gallery_url")) for g in current if g["gid"] not in rows
        ),
        "current_galleries_with_thumbnail_samples": samples,
        "current_galleries_with_gallery_visual": sum(row["has_visual"] for row in covered),
        "current_galleries_with_page_vector_records": sum(row["has_page_vectors"] for row in covered),
        "current_galleries_missing_completion_record": sum(
            (g["gid"], g["resolution"]) not in tracked_keys for g in current
        ),
        "current_image_count_differs_from_metadata": len(page_count_deltas),
    }


def remote_scan(config, source):
    host = config.get("hath_ssh_host", "")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise ValueError("invalid_ssh_host")
    executable = config.get("ssh_executable") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    args = [executable, "-F", config["hath_ssh_config"], "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes",
            "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR", host,
            "/usr/bin/python3 -B - --remote-probe"]
    result = subprocess.run(args, input=source.encode("utf-8"), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=90)
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("remote_read_failed")
    return json.loads(result.stdout.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise ValueError("output_already_exists")
    if args.remote_probe:
        sys.path.insert(0, "/srv/hath-observer")
        from hath_observer.core import parse_download_directory_name
        result = scan_library(Path("/srv/hath/download"), parse_download_directory_name)
    else:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "data/launcher/config.json").read_text(encoding="utf-8"))
        if config.get("hath_enabled") is not True:
            raise ValueError("hath_not_enabled")
        data_dir = Path(config.get("data_dir") or root / "data")
        if not data_dir.is_absolute():
            data_dir = root / data_dir
        remote = remote_scan(config, Path(__file__).read_text(encoding="utf-8"))
        result = {
            "schema": "hath-library-audit-v1", "read_only": True,
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "remote": remote["summary"],
            "database": audit_database(data_dir / "recommender.sqlite3", remote),
            "limitations": [
                "Only existing completed download directories; no CDN cache inspection.",
                "At most first/middle/last files, ordered by filename, are sampled.",
                "Matching image counts and a completion marker do not prove file integrity.",
                "Stored page vectors may describe thumbnails, not downloaded full pages.",
                "Account trust, quality, Hath earnings and free quota are not inspected.",
            ],
        }
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True)
    if args.output:
        # Exclusive creation keeps prior evidence intact.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
        print(json.dumps({"ok": True, "read_only": True}))
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('{"ok": false, "error": "audit_failed"}')
        sys.exit(1)
