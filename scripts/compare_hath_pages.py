"""Offline full-page/thumbnail feasibility comparison; never trains or promotes a model."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec.visual import DINOV2_VISUAL_VERSION, average_embeddings, dinov2_image_embeddings
from scripts.probe_hath_pages import FILE_LIMIT, read_bounded, write_json_new


def cosine(left, right):
    if len(left) != len(right) or not left:
        raise ValueError("incompatible_vectors")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("nonfinite_vector")
    norm = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    if not norm:
        raise ValueError("zero_vector")
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right)) / norm))


def mean(values):
    return round(sum(values) / len(values), 6) if values else None


def verified_blob(dataset, item):
    path = dataset / item["path"]
    if Path(item["path"]).is_absolute() or not path.resolve().is_relative_to(dataset.resolve()):
        raise ValueError("sample_outside_dataset")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("linked_sample")
    blob = read_bounded(path, FILE_LIMIT)
    if len(blob) != item["bytes"] or hashlib.sha256(blob).hexdigest() != item["sha256"]:
        raise ValueError("sample_digest_mismatch")
    return blob


def image_info(blob):
    from PIL import Image
    with Image.open(io.BytesIO(blob)) as image:
        width, height = image.size
        image.verify()
    return {"width": width, "height": height}


def reduced_image(blob, edge=250):
    from PIL import Image
    with Image.open(io.BytesIO(blob)) as image:
        image = image.convert("RGB")
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        target = io.BytesIO()
        image.save(target, format="PNG")
    return target.getvalue()


def compare(dataset, manifest, encode):
    records, failures, dimensions = [], Counter(), Counter()
    for gallery in manifest["galleries"]:
        pages, previews = [], []
        for source, target in ((gallery["pages"], pages), (gallery.get("previews", []), previews)):
            for item in source:
                try:
                    blob = verified_blob(dataset, item)
                    info = image_info(blob)
                except Exception:
                    failures["invalid_image_or_digest"] += 1
                    continue
                vectors = encode([blob, reduced_image(blob)]) if target is pages else encode([blob])
                if len(vectors) != (2 if target is pages else 1):
                    raise ValueError("encoder_output_count_mismatch")
                record = {**item, **info, "embedding": vectors[0]}
                if target is pages:
                    record["reduced_embedding"] = vectors[1]
                    record["full_vs_reduced_cosine"] = round(cosine(*vectors), 6)
                    dimensions["{}x{}".format(info["width"], info["height"])] += 1
                target.append(record)
        if not pages:
            continue
        full = average_embeddings([page["embedding"] for page in pages])
        reduced = average_embeddings([page["reduced_embedding"] for page in pages])
        preview = average_embeddings([item["embedding"] for item in previews]) if previews else None
        covers = [item for item in previews if item.get("kind") == "cover"]
        body = [page for page in pages if page["position"] != 1]
        record = {"gid": gallery["gid"], "resolution": gallery["resolution"],
                  "metadata_status": gallery["metadata_status"], "ordering": gallery["ordering"],
                  "pages": pages, "previews": previews, "full_embedding": full,
                  "reduced_full_embedding": reduced,
                  "preview_embedding": preview,
                  "full_vs_preview_cosine": round(cosine(full, preview), 6) if preview else None,
                  "cover_vs_body_cosine": round(cosine(covers[0]["embedding"], average_embeddings(
                      [page["embedding"] for page in body])), 6) if covers and body else None}
        records.append(record)
    paired = [record for record in records if record["preview_embedding"] is not None]
    overlaps, resize_overlaps = [], []
    k = min(3, len(paired) - 1)
    informative_neighbors = len(paired) > k + 1
    for row in paired:
        if k <= 0:
            break
        neighbors = {}
        for field in ("full_embedding", "preview_embedding", "reduced_full_embedding"):
            ranked = sorted((candidate for candidate in paired if candidate is not row),
                            key=lambda candidate: (-cosine(row[field], candidate[field]), candidate["gid"], candidate["resolution"]))
            neighbors[field] = [(candidate["gid"], candidate["resolution"]) for candidate in ranked[:k]]
        overlap = len(set(neighbors["full_embedding"]) & set(neighbors["preview_embedding"])) / k
        resize_overlap = len(set(neighbors["full_embedding"]) & set(neighbors["reduced_full_embedding"])) / k
        row["neighbor_comparison"] = {"k": k, "full": neighbors["full_embedding"],
                                       "preview": neighbors["preview_embedding"], "overlap": overlap,
                                       "reduced_full": neighbors["reduced_full_embedding"],
                                       "same_page_resize_overlap": resize_overlap}
        overlaps.append(overlap)
        resize_overlaps.append(resize_overlap)
    all_pages = [(row, page) for row in records for page in row["pages"]]
    pairs = []
    for index, (left_gallery, left) in enumerate(all_pages):
        for right_gallery, right in all_pages[index + 1:]:
            if left_gallery is right_gallery:
                continue
            pairs.append({"left": left["path"], "right": right["path"],
                          "cosine": round(cosine(left["embedding"], right["embedding"]), 6),
                          "exact_bytes": left["sha256"] == right["sha256"]})
    pairs.sort(key=lambda pair: (-pair["cosine"], pair["left"], pair["right"]))
    metrics = {
        "galleries_encoded": len(records), "pages_encoded": len(all_pages), "paired_galleries": len(paired),
        "previews_encoded": sum(len(row["previews"]) for row in records),
        "mean_full_vs_preview_cosine": mean([row["full_vs_preview_cosine"] for row in paired]),
        "mean_cover_vs_body_cosine": mean([row["cover_vs_body_cosine"] for row in records if row["cover_vs_body_cosine"] is not None]),
        "mean_same_page_full_vs_reduced_cosine": mean([page["full_vs_reduced_cosine"] for _, page in all_pages]),
        "mean_full_vs_reduced_gallery_cosine": mean([cosine(row["full_embedding"], row["reduced_full_embedding"]) for row in records]),
        "neighbor_k": max(0, k), "neighbor_overlap_informative": informative_neighbors,
        "mean_neighbor_overlap": mean(overlaps) if informative_neighbors else None,
        "mean_same_page_resize_neighbor_overlap": mean(resize_overlaps) if informative_neighbors else None,
        "sample_dimensions": dict(dimensions), "image_failures": dict(failures),
        "cross_gallery_exact_sample_pairs": sum(pair["exact_bytes"] for pair in pairs),
    }
    return {"metrics": metrics, "galleries": records, "nearest_cross_gallery_pages": pairs[:20]}


def verify_experiment(dataset, report_path):
    manifest_bytes = (dataset / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != "hath-page-comparison-v1" or report.get("production_read_only") is not True:
        raise ValueError("invalid_comparison_report")
    if report["dataset_manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest():
        raise ValueError("dataset_manifest_changed")
    sources = {(row["gid"], row["resolution"]): row for row in manifest["galleries"] if row["pages"]}
    results = {(row["gid"], row["resolution"]): row for row in report["galleries"]}
    if len(results) != len(report["galleries"]) or set(sources) != set(results) or report["metrics"]["image_failures"]:
        raise ValueError("incomplete_comparison")
    dimensions = set()
    counts = Counter()
    for key, source in sources.items():
        for field in ("pages", "previews"):
            items = {item["path"]: item for item in results[key][field]}
            if len(items) != len(results[key][field]) or set(items) != {item["path"] for item in source[field]}:
                raise ValueError("sample_coverage_mismatch")
            for item in source[field]:
                blob = verified_blob(dataset, item)
                observed = items[item["path"]]
                if any(observed[name] != item[name] for name in ("bytes", "sha256")):
                    raise ValueError("sample_provenance_mismatch")
                info = image_info(blob)
                if any(info[name] != observed[name] for name in info):
                    raise ValueError("sample_dimensions_mismatch")
                vector = observed["embedding"]
                dimensions.add(len(vector))
                if not vector or not all(math.isfinite(value) for value in vector) or abs(sum(v * v for v in vector) - 1) > 0.001:
                    raise ValueError("invalid_embedding")
                if field == "pages" and abs(cosine(vector, observed["reduced_embedding"]) - observed["full_vs_reduced_cosine"]) > 0.000002:
                    raise ValueError("reduced_metric_mismatch")
                counts[field] += 1
    if len(dimensions) != 1 or counts["pages"] != report["metrics"]["pages_encoded"] or counts["previews"] != report["metrics"]["previews_encoded"]:
        raise ValueError("comparison_count_mismatch")
    return {"schema": "hath-page-verification-v1", "ok": True, "all_sample_hashes_verified": True, "source_coverage_verified": True,
            "production_read_only": True, "pages_verified": counts["pages"],
            "previews_verified": counts["previews"], "embedding_dimensions": next(iter(dimensions))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verification-output", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        verification = verify_experiment(args.dataset, args.output)
        if args.verification_output:
            write_json_new(args.verification_output, verification)
        print(json.dumps(verification))
        return 0
    if args.verification_output:
        parser.error("--verification-output requires --verify-only")
    if args.output.exists():
        raise ValueError("output_already_exists")
    # Reuse the installed encoder/cache, but prohibit implicit model downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    torch.set_num_threads(min(4, torch.get_num_threads()))
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "hath-page-experiment-v1" or manifest.get("production_read_only") is not True:
        raise ValueError("invalid_experiment_manifest")
    result = compare(args.dataset, manifest, lambda blobs: dinov2_image_embeddings(blobs, device=args.device))
    result.update({"schema": "hath-page-comparison-v1", "production_read_only": True,
                   "encoder": DINOV2_VISUAL_VERSION, "device": args.device,
                   "finished_at": datetime.now(timezone.utc).isoformat(),
                   "dataset_manifest_sha256": hashlib.sha256((args.dataset / "manifest.json").read_bytes()).hexdigest(),
                   "limitations": ["Similarity and neighbor changes are not recommendation quality or a preference evaluation.",
                                   "Full pages and cached previews may depict different pages; not a resolution-only comparison.",
                                   "Reduced-page ablation uses the same page at a 250px maximum edge; not the actual site thumbnail pipeline.",
                                   "DINOv2 resizes input; full-page source bytes do not imply full-resolution reasoning.",
                                   "First/middle/last filename samples are not verified site page numbers or random samples.",
                                   "No training, database writes, model promotion, label changes or site requests."]})
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    print(json.dumps({"ok": True, "production_read_only": True, "metrics": result["metrics"]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('{"ok": false, "error": "comparison_failed"}', file=sys.stderr)
        sys.exit(1)
