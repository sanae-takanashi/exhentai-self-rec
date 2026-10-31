"""Bounded, read-only H@H page collection into a new offline experiment directory."""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import zipfile


FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
PREVIEW_TOTAL_LIMIT = 32 * 1024 * 1024
WIRE_LIMIT = TOTAL_LIMIT + 2 * 1024 * 1024
GALLERY_LIMIT = 100
BATCH_SIZE = 8
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"})
ASSET_RE = re.compile(r"images/[0-9]{1,20}-(?:org|[0-9]{1,6})/[0-9]{6}\.(?:jpg|jpeg|png|webp|gif|avif)\Z")


def plain_file(path):
    return not path.is_symlink() and path.is_file()


def read_bounded(path, limit):
    """Reject links and files replaced or resized while being read."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("unsafe_or_oversized_file")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
            raise ValueError("file_changed")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    if len(data) > limit or len(data) != before.st_size or any(
        (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns) !=
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        for value in (after, final)
    ):
        raise ValueError("file_changed")
    return data


def collect_remote(root, parse_name, selected_keys=None, byte_budget=None):
    if root.is_symlink() or not root.is_dir() or root.resolve() != root.absolute():
        raise ValueError("invalid_download_root")
    galleries, assets, skipped = [], {}, Counter()
    budget = TOTAL_LIMIT if byte_budget is None else byte_budget
    if isinstance(budget, bool) or not isinstance(budget, int) or not 0 <= budget <= TOTAL_LIMIT:
        raise ValueError("invalid_byte_budget")
    total = 0
    identities = set()
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(".") or directory.is_symlink() or not directory.is_dir():
            continue
        identity = parse_name(directory.name)
        if not identity or not re.fullmatch(r"[0-9]{1,20}", identity["gid"]) or not re.fullmatch(
            r"org|[0-9]{1,6}", identity["resolution"]
        ):
            skipped["unrecognized_directory"] += 1
            continue
        marker = directory / "galleryinfo.txt"
        if not plain_file(marker):
            skipped["incomplete_gallery"] += 1
            continue
        key = (identity["gid"], identity["resolution"])
        if key in identities:
            raise ValueError("ambiguous_gallery_directories")
        identities.add(key)
        if len(galleries) >= GALLERY_LIMIT:
            skipped["gallery_limit"] += 1
            continue
        images = sorted(child for child in directory.iterdir() if not child.name.startswith(".")
                        and child.suffix.lower() in IMAGE_SUFFIXES and plain_file(child))
        # Natural page order is only provable for a uniformly numeric filename prefix.
        prefixes = [re.match(r"^(\d+)(?:\D|$)", child.name) for child in images]
        numeric = bool(images) and all(prefixes) and len({int(p.group(1)) for p in prefixes}) == len(images)
        if numeric:
            images.sort(key=lambda child: int(re.match(r"^(\d+)", child.name).group(1)))
        gallery = {"gid": key[0], "resolution": key[1], "image_count": len(images),
                   "ordering": "numeric_filename_prefix" if numeric else "filename_lexical",
                   "marker_has_matching_gallery_url": False, "pages": []}
        try:
            info = read_bounded(marker, 32768).decode("utf-8", errors="replace")
            gallery["marker_has_matching_gallery_url"] = key[0] in re.findall(
                r"https?://(?:e-hentai|exhentai)\.org/g/(\d+)/[0-9a-f]+", info)
        except (OSError, ValueError):
            skipped["marker_unreadable_or_oversized"] += 1
        positions = sorted({0, len(images) // 2, len(images) - 1}) if images else []
        if selected_keys is not None and "{}-{}".format(*key) not in selected_keys:
            positions = []
        for position in positions:
            path = images[position]
            size = path.stat().st_size
            if size > FILE_LIMIT or total + size > budget:
                skipped["byte_limit"] += 1
                continue
            try:
                blob = read_bounded(path, FILE_LIMIT)
            except (OSError, ValueError):
                skipped["image_changed_or_unreadable"] += 1
                continue
            if total + len(blob) > budget:
                skipped["byte_limit"] += 1
                continue
            relative = "images/{}-{}/{:06d}{}".format(key[0], key[1], position + 1, path.suffix.lower())
            assets[relative] = blob
            gallery["pages"].append({"path": relative, "position": position + 1,
                                     "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})
            total += len(blob)
        galleries.append(gallery)
    manifest = {"schema": "hath-page-source-v1", "galleries": galleries,
                "skipped": dict(skipped), "sample_bytes": total}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True))
        for name, blob in assets.items():
            archive.writestr(name, blob)
    return output.getvalue()


def fetch_remote(config, transport_report=None, selected_keys=None, byte_budget=None):
    host = config.get("hath_ssh_host", "")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise ValueError("invalid_ssh_host")
    remote_args = ["/usr/bin/python3", "-B", "-", "--remote-probe"]
    if selected_keys is not None:
        if len(selected_keys) > BATCH_SIZE or any(not re.fullmatch(r"[0-9]{1,20}-(?:org|[0-9]{1,6})", key) for key in selected_keys):
            raise ValueError("invalid_gallery_selection")
        remote_args.extend(["--gallery-keys", ",".join(selected_keys) or "none"])
    if byte_budget is not None:
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int) or not 0 <= byte_budget <= TOTAL_LIMIT:
            raise ValueError("invalid_byte_budget")
        remote_args.extend(["--byte-budget", str(byte_budget)])
    command = [config.get("ssh_executable") or r"C:\Windows\System32\OpenSSH\ssh.exe",
               "-F", config["hath_ssh_config"], "-T", "-o", "BatchMode=yes", "-o",
               "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes", "-o",
               "ConnectTimeout=15", "-o", "LogLevel=ERROR", host] + remote_args
    wire = bytearray()
    timed_out = threading.Event()
    started = time.monotonic()
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        def expire():
            timed_out.set()
            process.kill()
        timer = threading.Timer(480, expire)
        timer.start()
        try:
            process.stdin.write(Path(__file__).read_bytes())
            process.stdin.close()
            while True:
                block = process.stdout.read1(65536)
                if not block:
                    break
                wire.extend(block)
                if len(wire) > WIRE_LIMIT:
                    process.kill()
                    raise ValueError("remote_output_limit")
            if process.wait() != 0:
                raise ValueError("remote_probe_failed")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            if transport_report is not None:
                with transport_report.open("x", encoding="utf-8") as stream:
                    json.dump({"bytes_received": len(wire), "timed_out": timed_out.is_set(),
                               "remote_exit_code": process.returncode,
                               "seconds": round(time.monotonic() - started, 3)}, stream)
                    stream.write("\n")
    return bytes(wire)


def write_json_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


def collect_batches(config, output, resume=False):
    if output.is_symlink() or output.resolve() != output.absolute():
        raise ValueError("invalid_output_directory")
    if resume:
        if (output / "manifest.json").exists():
            raise ValueError("experiment_already_complete")
        inventory = json.loads(read_bounded(output / "inventory.json", FILE_LIMIT).decode("utf-8"))
    else:
        inventory = unpack_verified(fetch_remote(config, selected_keys=[]), output / "inventory")
        write_json_new(output / "inventory.json", inventory)
    rows = inventory["galleries"]
    if len(rows) > GALLERY_LIMIT or inventory.get("schema") != "hath-page-source-v1":
        raise ValueError("invalid_inventory")
    total = 0
    combined_skipped = Counter(inventory["skipped"])
    for offset in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[offset:offset + BATCH_SIZE]
        keys = ["{}-{}".format(row["gid"], row["resolution"]) for row in batch_rows]
        name = "batch-{:03d}".format(offset // BATCH_SIZE)
        batch_output = output / name
        checkpoint = batch_output / "source.json"
        if resume and checkpoint.exists():
            source = json.loads(read_bounded(checkpoint, FILE_LIMIT).decode("utf-8"))
        else:
            if batch_output.exists():
                raise ValueError("partial_batch_requires_new_output")
            # Each batch is independently bounded and durable before the next SSH request.
            transport = output / (name + "-transport-{}.json".format(time.time_ns() if hasattr(time, "time_ns") else int(time.time() * 1000000)))
            wire = fetch_remote(config, transport_report=transport, selected_keys=keys, byte_budget=TOTAL_LIMIT - total)
            source = unpack_verified(wire, batch_output)
            write_json_new(checkpoint, source)
        by_key = {"{}-{}".format(row["gid"], row["resolution"]): row for row in source["galleries"]}
        for row, key in zip(batch_rows, keys):
            observed = by_key.get(key)
            if not observed or any(observed[field] != row[field] for field in ("image_count", "ordering")):
                raise ValueError("gallery_changed_between_batches")
            row["pages"] = []
            for page in observed["pages"]:
                if not ASSET_RE.fullmatch(page["path"]) or not page["path"].startswith("images/" + key + "/"):
                    raise ValueError("invalid_checkpoint_path")
                path = batch_output / page["path"]
                blob = read_bounded(path, FILE_LIMIT)
                if len(blob) != page["bytes"] or hashlib.sha256(blob).hexdigest() != page["sha256"]:
                    raise ValueError("checkpoint_digest_mismatch")
                total += len(blob)
                if total > TOTAL_LIMIT:
                    raise ValueError("total_collection_budget_exceeded")
                row["pages"].append(dict(page, path=name + "/" + page["path"]))
        for key in ("byte_limit", "image_changed_or_unreadable"):
            combined_skipped[key] += source["skipped"].get(key, 0)
    return {"galleries": rows, "sample_bytes": total,
            "skipped": {key: value for key, value in combined_skipped.items() if value}}


def unpack_verified(wire, output):
    if output.exists() or output.is_symlink():
        raise ValueError("output_already_exists")
    if len(wire) > WIRE_LIMIT:
        raise ValueError("remote_output_limit")
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(names) > GALLERY_LIMIT * 3 + 1:
            raise ValueError("invalid_archive_members")
        for entry in entries:
            if entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1 or (
                entry.filename != "manifest.json" and not ASSET_RE.fullmatch(entry.filename)
            ) or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > FILE_LIMIT:
                raise ValueError("unsafe_archive_member")
        if sum(entry.file_size for entry in entries) > WIRE_LIMIT:
            raise ValueError("archive_size_limit")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("schema") != "hath-page-source-v1" or len(manifest["galleries"]) > GALLERY_LIMIT:
            raise ValueError("invalid_manifest")
        expected, identities = {}, set()
        for gallery in manifest["galleries"]:
            gid, resolution = gallery["gid"], gallery["resolution"]
            if not re.fullmatch(r"[0-9]{1,20}", gid) or not re.fullmatch(r"org|[0-9]{1,6}", resolution):
                raise ValueError("invalid_gallery_identity")
            key = (gid, resolution)
            if key in identities or len(gallery["pages"]) > 3:
                raise ValueError("invalid_gallery_samples")
            identities.add(key)
            for page in gallery["pages"]:
                name = page["path"]
                if not ASSET_RE.fullmatch(name) or not name.startswith("images/{}-{}/".format(*key)) or name in expected:
                    raise ValueError("invalid_sample_path")
                expected[name] = page
        if set(names) != set(expected) | {"manifest.json"}:
            raise ValueError("archive_manifest_mismatch")
        if sum(page["bytes"] for page in expected.values()) > TOTAL_LIMIT:
            raise ValueError("sample_size_limit")
        for name, page in expected.items():
            blob = archive.read(name)
            if len(blob) != page["bytes"] or hashlib.sha256(blob).hexdigest() != page["sha256"]:
                raise ValueError("sample_digest_mismatch")
        output.mkdir(parents=True, exist_ok=False)
        for name in expected:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(archive.read(name))
    return manifest


def join_metadata(database, galleries):
    """No settings, titles, tokens, URLs or labels are exported to the manifest."""
    internal = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        for gallery in galleries:
            gid = gallery["gid"]
            rows = conn.execute(
                "SELECT url, thumb_url, samples_json, page_count FROM galleries WHERE gid = ? "
                "ORDER BY detail_fetched_at DESC, last_seen_at DESC, url", (gid,)).fetchall()
            downloads = conn.execute(
                "SELECT gallery_url FROM hath_downloads WHERE gid = ? AND resolution = ? AND status = 'completed'",
                (gid, gallery["resolution"])).fetchall()
            gallery["metadata_matches"] = len(rows)
            gallery["linked_completion_records"] = sum(bool(row["gallery_url"]) for row in downloads)
            gallery["completion_records"] = len(downloads)
            gallery["metadata_status"] = "present" if rows else "missing"
            gallery["gap_reason"] = None if rows else (
                "linked_url_missing_from_metadata" if gallery["linked_completion_records"]
                else "completion_has_no_gallery_url" if downloads else "no_completion_record")
            if not rows:
                gallery["thumbnail_sample_entries"] = 0
                continue
            row = rows[0]
            try:
                samples = json.loads(row["samples_json"] or "[]")
            except (ValueError, TypeError):
                samples = []
            samples = samples if isinstance(samples, list) else []
            samples = [entry for entry in samples if isinstance(entry, str) and entry or
                       isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"]]
            gallery["thumbnail_sample_entries"] = len(samples)
            gallery["metadata_image_count_matches"] = row["page_count"] == gallery["image_count"]
            internal[(gid, gallery["resolution"])] = {"cover": row["thumb_url"], "samples": samples[:12]}
    return internal


def copy_cached_previews(cache, output, galleries, internal):
    from PIL import Image
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from exh_rec.app import crop_sprite_bytes, sprite_crop_box
    failures = Counter()
    total = 0
    for gallery in galleries:
        metadata = internal.get((gallery["gid"], gallery["resolution"]), {})
        gallery["previews"] = []
        seen = set()
        entries = [("cover", metadata.get("cover"))] + [("sample", entry) for entry in metadata.get("samples", [])]
        for index, (kind, entry) in enumerate(entries):
            if not entry:
                continue
            url = entry.get("url") if isinstance(entry, dict) else entry
            box = sprite_crop_box(entry) if isinstance(entry, dict) else None
            if isinstance(entry, dict) and not box:
                failures["invalid_sprite_frame"] += 1
                continue
            cache_key = "{}#{},{},{},{}".format(url, *box) if box else url
            if cache_key in seen:
                continue
            seen.add(cache_key)
            path = cache / (hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + ".bin")
            cropped_cache = bool(box) and plain_file(path)
            if box and not cropped_cache:
                path = cache / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".bin")
            if not plain_file(path):
                failures["not_cached"] += 1
                continue
            try:
                blob = read_bounded(path, FILE_LIMIT)
                if box and not cropped_cache:
                    with Image.open(io.BytesIO(blob)) as sheet:
                        if box[0] + box[2] > sheet.width or box[1] + box[3] > sheet.height:
                            raise ValueError("sprite_out_of_bounds")
                    blob, _ = crop_sprite_bytes(blob, *box)
                with Image.open(io.BytesIO(blob)) as image:
                    if cropped_cache and image.size != (box[2], box[3]):
                        raise ValueError("cached_frame_size_mismatch")
                    image.verify()
            except Exception:
                failures["invalid_or_changed_cache_image"] += 1
                continue
            if len(blob) > FILE_LIMIT or total + len(blob) > PREVIEW_TOTAL_LIMIT:
                failures["preview_byte_limit"] += 1
                continue
            relative = "previews/{}-{}/{:02d}.bin".format(gallery["gid"], gallery["resolution"], index)
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(blob)
            gallery["previews"].append({"path": relative, "kind": kind, "bytes": len(blob),
                                        "sha256": hashlib.sha256(blob).hexdigest()})
            total += len(blob)
    return dict(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transport-report", type=Path)
    parser.add_argument("--gallery-keys", help=argparse.SUPPRESS)
    parser.add_argument("--byte-budget", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--batched", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.remote_probe:
        sys.path.insert(0, "/srv/hath-observer")
        from hath_observer.core import parse_download_directory_name
        selected = None
        if args.gallery_keys is not None:
            selected = [] if args.gallery_keys == "none" else args.gallery_keys.split(",")
            if len(selected) > BATCH_SIZE or any(not re.fullmatch(r"[0-9]{1,20}-(?:org|[0-9]{1,6})", key) for key in selected):
                raise ValueError("invalid_gallery_selection")
        sys.stdout.buffer.write(collect_remote(Path("/srv/hath/download"), parse_download_directory_name, selected, args.byte_budget))
        return 0
    if args.resume and not args.batched:
        raise ValueError("resume_requires_batched_collection")
    if not args.output or args.output.exists() and not args.resume:
        raise ValueError("new_output_directory_required")
    if args.transport_report and args.transport_report.exists():
        raise ValueError("transport_report_already_exists")
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "data/launcher/config.json").read_text(encoding="utf-8"))
    if config.get("hath_enabled") is not True:
        raise ValueError("hath_not_enabled")
    data = Path(config.get("data_dir") or root / "data")
    if not data.is_absolute():
        data = root / data
    source = collect_batches(config, args.output, resume=args.resume) if args.batched else unpack_verified(
        fetch_remote(config, args.transport_report), args.output)
    galleries = source["galleries"]
    internal = join_metadata(data / "recommender.sqlite3", galleries)
    preview_failures = copy_cached_previews(data / "thumbs", args.output, galleries, internal)
    manifest = {"schema": "hath-page-experiment-v1", "production_read_only": True,
                "collected_at": datetime.now(timezone.utc).isoformat(), "galleries": galleries,
                "summary": {"galleries": len(galleries), "pages": sum(len(g["pages"]) for g in galleries),
                            "page_bytes": source["sample_bytes"], "metadata_missing": sum(g["metadata_status"] == "missing" for g in galleries),
                            "gap_reasons": dict(Counter(g["gap_reason"] for g in galleries if g["gap_reason"])),
                            "matched_without_sample_entries": sum(g["metadata_status"] == "present" and not g["thumbnail_sample_entries"] for g in galleries),
                            "cached_previews": sum(len(g["previews"]) for g in galleries),
                            "remote_skipped": source["skipped"], "preview_failures": preview_failures},
                "limitations": ["Existing completed downloads only; no site requests or CDN cache reads.",
                                "Positions use filename order, not verified site page identity.",
                                "Missing metadata stays missing; no invented gallery URLs or labels.",
                                "Only existing thumbnail cache bytes; no authenticated fetches.",
                                "Non-random download-selected sample; not a recommendation evaluation."]}
    with (args.output / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    print(json.dumps({"ok": True, "production_read_only": True, "summary": manifest["summary"]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('{"ok": false, "error": "page_probe_failed"}', file=sys.stderr)
        sys.exit(1)
