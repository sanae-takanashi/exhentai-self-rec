"""Read-only local page previews and isolated cover/body similarity."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading


SCHEMA = "hath-local-pages-v1"
KEY = re.compile(r"[0-9]{1,20}-(?:org|[0-9]{1,6})\Z")
ASSET = re.compile(r"[0-9a-f]{64}\Z")
MIMES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "avif": "image/avif"}
MAX_IMAGE = 8 * 1024 * 1024
MAX_INDEX = 8 * 1024 * 1024


def read_regular(path: Path, limit: int) -> bytes:
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked_local_asset")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("invalid_local_asset")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(data) > limit or any(identity(info) != identity(before) for info in (opened, after, path.stat())):
        raise ValueError("changed_local_asset")
    return data


def normalized(values):
    if not isinstance(values, list) or not values or len(values) > 4096:
        raise ValueError("invalid_local_vector")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("invalid_local_vector")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("invalid_local_vector")
    return [value / norm for value in values]


def mean_vector(vectors):
    if not vectors:
        return None
    size = len(vectors[0])
    if any(len(vector) != size for vector in vectors):
        raise ValueError("incompatible_local_vectors")
    return normalized([sum(vector[index] for vector in vectors) / len(vectors) for index in range(size)])


class LocalPageLibrary:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.rows = {}
        self.assets = {}
        self.signature = None
        self.lock = threading.RLock()
        self.collected_at = None

    def load(self):
        with self.lock:
            index = self.root / "index.json"
            info = index.stat()
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if signature == self.signature:
                return
            document = json.loads(read_regular(index, MAX_INDEX))
            if not isinstance(document, dict) or document.get("schema") != SCHEMA or not isinstance(document.get("galleries"), list):
                raise ValueError("invalid_local_index")
            rows, assets, dimensions = {}, {}, set()
            if not 0 < len(document["galleries"]) <= 100:
                raise ValueError("invalid_local_gallery_count")
            for row in document["galleries"]:
                if not isinstance(row, dict):
                    raise ValueError("invalid_local_gallery")
                key = row["key"]
                if not isinstance(key, str) or not KEY.fullmatch(key) or key != f"{row['gid']}-{row['resolution']}" or key in rows:
                    raise ValueError("invalid_local_gallery")
                if row["ordering"] not in {"numeric_filename_prefix", "filename_lexical"} or type(row["image_count"]) is not int or row["image_count"] <= 0:
                    raise ValueError("invalid_local_ordering")
                if not 0 < len(row["pages"]) <= 3:
                    raise ValueError("invalid_local_samples")
                for image in [*row["pages"], *([row["cover"]] if row.get("cover") else [])]:
                    digest = image["sha256"]
                    suffix = image["suffix"]
                    if not ASSET.fullmatch(digest) or suffix not in MIMES or not 0 < image["bytes"] <= MAX_IMAGE:
                        raise ValueError("invalid_local_image")
                    if any(type(image[name]) is not int or image[name] <= 0 for name in ("width", "height")):
                        raise ValueError("invalid_local_dimensions")
                    if digest in assets and assets[digest] != {name: image[name] for name in ("sha256", "suffix", "bytes", "width", "height")}:
                        raise ValueError("conflicting_local_image")
                    assets[digest] = {name: image[name] for name in ("sha256", "suffix", "bytes", "width", "height")}
                if any(type(image["position"]) is not int or not 0 < image["position"] <= row["image_count"] for image in row["pages"]):
                    raise ValueError("invalid_local_position")
                if len({image["position"] for image in row["pages"]}) != len(row["pages"]):
                    raise ValueError("duplicate_local_position")
                for field in ("cover_vector", "body_vector"):
                    if row.get(field) is not None:
                        row[field] = normalized(row[field])
                        dimensions.add(len(row[field]))
                if bool(row.get("cover_vector")) != bool(row.get("cover")) or (row.get("body_vector") is not None and not any(image["position"] != 1 for image in row["pages"])):
                    raise ValueError("local_vector_source_mismatch")
                rows[key] = row
            if len(dimensions) > 1:
                raise ValueError("incompatible_local_vectors")
            self.rows, self.assets = rows, assets
            self.collected_at = str(document.get("collected_at") or "")
            self.signature = signature

    def image(self, digest):
        if not ASSET.fullmatch(digest):
            raise KeyError("unknown_local_image")
        with self.lock:
            self.load()
            image = self.assets[digest]
            blob = read_regular(self.root / "images" / f"{digest}.{image['suffix']}", MAX_IMAGE)
            if len(blob) != image["bytes"] or hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError("local_image_digest_mismatch")
            return blob, MIMES[image["suffix"]]

    def catalog(self, conn):
        with self.lock:
            try:
                self.load()
            except FileNotFoundError:
                return {"available": False, "reason": "not_configured", "items": []}
            except (OSError, ValueError, KeyError, TypeError):
                return {"available": False, "reason": "invalid_library", "items": []}
            metadata = self.metadata(conn)
            return {"available": True, "collected_at": self.collected_at,
                    "items": [self.public(row, metadata) for row in self.rows.values()],
                    "comparable_count": len(self.paired())}

    def metadata(self, conn):
        gids = sorted({row["gid"] for row in self.rows.values()})
        placeholders = ",".join("?" for _ in gids)
        result = {}
        for row in conn.execute(f"SELECT gid, title, url FROM galleries WHERE gid IN ({placeholders}) ORDER BY url", gids):
            result.setdefault(str(row["gid"]), []).append(dict(row))
        return result

    def public(self, row, metadata):
        matches = metadata.get(row["gid"], [])
        known = matches[0] if len(matches) == 1 else None
        image = lambda item: {**{name: item[name] for name in ("width", "height")},
                              "src": f"/api/local-pages/image?id={item['sha256']}"}
        return {"key": row["key"], "gid": row["gid"], "resolution": row["resolution"],
                "title": known["title"] if known else f"Gallery {row['gid']}",
                "url": known["url"] if known and re.fullmatch(r"https://(?:exhentai|e-hentai)\.org/g/[0-9]+/[0-9a-f]+/", known["url"]) else None,
                "metadata_status": "matched" if known else "ambiguous" if matches else "missing",
                "ordering": row["ordering"], "image_count": row["image_count"],
                "pages": [{**image(item), "position": item["position"]} for item in row["pages"]],
                "cover": image(row["cover"]) if row.get("cover") else None,
                "comparable": row.get("cover_vector") is not None and row.get("body_vector") is not None}

    def paired(self):
        rows = [row for row in self.rows.values() if row.get("cover_vector") is not None and row.get("body_vector") is not None]
        by_gid = {}
        for row in sorted(rows, key=lambda item: (-max(page["width"] for page in item["pages"]), item["key"])):
            by_gid.setdefault(row["gid"], row)
        return list(by_gid.values())

    def compare(self, key, conn):
        with self.lock:
            self.load()
            row = self.rows[key]
            metadata = self.metadata(conn)
            paired = self.paired()
            result = {"item": self.public(row, metadata), "candidate_count": len(paired),
                      "cover": [], "body": [], "comparable": row.get("cover_vector") is not None and row.get("body_vector") is not None}
            if not result["comparable"]:
                return result
            # Both columns search the same paired set, excluding every resolution of the query gid.
            candidates = [candidate for candidate in paired if candidate["gid"] != row["gid"]]
            result["candidate_count"] = len(candidates)
            for source in ("cover", "body"):
                field = f"{source}_vector"
                score = lambda candidate: sum(a * b for a, b in zip(row[field], candidate[field]))
                ranked = sorted(candidates, key=lambda candidate: (-score(candidate), candidate["key"]))[:3]
                result[source] = [{**self.public(candidate, metadata), "cosine": round(max(-1., min(1., score(candidate))), 6)} for candidate in ranked]
            return result
