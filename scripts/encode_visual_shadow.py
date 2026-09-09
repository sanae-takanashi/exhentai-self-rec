from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec import db  # noqa: E402
from exh_rec.app import cached_thumbnail, network_proxy  # noqa: E402
from exh_rec.net import apply_proxy_environment  # noqa: E402
from exh_rec.recommender import (  # noqa: E402
    store_visual_embedding_variant,
    store_visual_image_embeddings,
)
from exh_rec.visual import (  # noqa: E402
    DINOV2_BAKEOFF_VISUAL_VERSION,
    DINOV2_VISUAL_VERSION,
    SIGLIP2_VISUAL_VERSION,
    average_embeddings,
    dinov2_image_embeddings,
    siglip2_image_embeddings,
)


ENCODERS = {
    "dinov2": {
        "version": DINOV2_BAKEOFF_VISUAL_VERSION,
        "function": dinov2_image_embeddings,
    },
    "siglip2": {
        "version": SIGLIP2_VISUAL_VERSION,
        "function": siglip2_image_embeddings,
    },
}


def image_urls(conn, row) -> list[str]:
    persisted = [
        str(item["image_url"])
        for item in conn.execute(
            """
            SELECT image_url
            FROM gallery_visual_images
            WHERE gallery_url = ? AND embedding_version = ? AND image_url IS NOT NULL
            ORDER BY image_key
            """,
            (row["url"], DINOV2_VISUAL_VERSION),
        )
        if item["image_url"]
    ]
    if persisted:
        return persisted[:12]
    values = []
    if row["thumb_url"]:
        values.append(str(row["thumb_url"]))
    try:
        samples = json.loads(row["samples_json"] or "[]")
    except json.JSONDecodeError:
        samples = []
    values.extend(str(item) for item in samples if isinstance(item, str) and item)
    return list(dict.fromkeys(values))[:12]


def candidates(conn, *, labeled_only: bool, limit: int, force: bool, versions: list[str]):
    label_filter = """
        AND (
            EXISTS (SELECT 1 FROM feedback f WHERE f.gallery_url = g.url AND f.vote != 0)
            OR EXISTS (SELECT 1 FROM gallery_marks m WHERE m.gallery_url = g.url)
        )
    """ if labeled_only else ""
    missing_filter = ""
    parameters: list[object] = []
    if not force:
        placeholders = ",".join("?" for _ in versions)
        missing_filter = f"""
            AND (
                SELECT COUNT(DISTINCT e.embedding_version)
                FROM gallery_visual_embeddings e
                WHERE e.gallery_url = g.url AND e.embedding_version IN ({placeholders})
            ) < {len(versions)}
        """
        parameters.extend(versions)
    parameters.append(limit)
    return conn.execute(
        f"""
        SELECT g.url, g.thumb_url, g.samples_json
        FROM galleries g
        WHERE (g.thumb_url IS NOT NULL OR g.samples_json != '[]')
        {label_filter}
        {missing_filter}
        ORDER BY CASE WHEN EXISTS (
            SELECT 1 FROM feedback f WHERE f.gallery_url = g.url AND f.vote != 0
        ) THEN 0 ELSE 1 END, g.last_seen_at DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()


def peak_vram_bytes() -> int | None:
    try:
        import torch

        return int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Encode DINOv2/SigLIP2 shadow vectors without changing production vectors")
    parser.add_argument("--database", type=Path, default=db.DB_PATH)
    parser.add_argument("--encoders", default="dinov2,siglip2")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--all-galleries", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "visual-shadow-encode.json")
    args = parser.parse_args()
    selected_encoders = [value.strip().lower() for value in args.encoders.split(",") if value.strip()]
    unknown = [value for value in selected_encoders if value not in ENCODERS]
    if unknown or not selected_encoders:
        parser.error(f"encoders must be selected from {', '.join(ENCODERS)}")
    db.DB_PATH = args.database.resolve()
    if not args.dry_run:
        db.init_db()
    versions = [ENCODERS[name]["version"] for name in selected_encoders]
    connection = (
        db.connect()
        if not args.dry_run
        else sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    )
    with connection as conn:
        conn.row_factory = sqlite3.Row
        apply_proxy_environment(network_proxy(conn))
        has_shadow_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gallery_visual_embeddings'"
        ).fetchone()
        rows = candidates(
            conn,
            labeled_only=not args.all_galleries,
            limit=max(1, args.limit),
            force=args.force or not bool(has_shadow_table),
            versions=versions,
        )
    report = {
        "database": str(db.DB_PATH),
        "device": args.device,
        "encoders": selected_encoders,
        "candidate_count": len(rows),
        "dry_run": args.dry_run,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "results": {name: {"galleries": 0, "images": 0, "seconds": 0.0, "errors": []} for name in selected_encoders},
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        with db.connect() as conn:
            urls = image_urls(conn, row)
            existing_versions = {
                str(item["embedding_version"])
                for item in conn.execute(
                    "SELECT embedding_version FROM gallery_visual_embeddings WHERE gallery_url = ?",
                    (row["url"],),
                )
            }
        entries = []
        for image_url in urls:
            try:
                blob, _content_type = cached_thumbnail(image_url, str(row["url"]))
                entries.append((image_url, blob))
            except Exception as exc:
                for name in selected_encoders:
                    report["results"][name]["errors"].append(f"{row['url']} image: {exc}")
        if not entries:
            continue
        for name in selected_encoders:
            config = ENCODERS[name]
            if not args.force and config["version"] in existing_versions:
                continue
            started = time.perf_counter()
            try:
                vectors = config["function"]([blob for _url, blob in entries], device=args.device)
                aggregate = average_embeddings(vectors)
                with db.connect() as conn:
                    store_visual_embedding_variant(
                        conn,
                        str(row["url"]),
                        aggregate,
                        version=config["version"],
                        encoder=name,
                        image_count=len(vectors),
                    )
                    store_visual_image_embeddings(
                        conn,
                        str(row["url"]),
                        [
                            {"image_url": image_url, "embedding": vector}
                            for (image_url, _blob), vector in zip(entries, vectors)
                        ],
                        version=config["version"],
                    )
                result = report["results"][name]
                result["galleries"] += 1
                result["images"] += len(vectors)
                result["seconds"] += time.perf_counter() - started
            except Exception as exc:
                report["results"][name]["errors"].append(f"{row['url']}: {type(exc).__name__}: {exc}")
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    report["peak_vram_bytes"] = peak_vram_bytes()
    for result in report["results"].values():
        result["seconds"] = round(float(result["seconds"]), 3)
        result["images_per_second"] = round(result["images"] / result["seconds"], 3) if result["seconds"] else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(not result["errors"] for result in report["results"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
