"""Publish verified offline experiment assets to a new local-only preview library."""

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec.local_pages import LocalPageLibrary, PAGE_SEARCH_SCHEMA, SCHEMA, mean_vector, normalized
from scripts.compare_hath_pages import verified_blob, verify_experiment
from scripts.probe_hath_pages import write_json_new


def build(experiment, output):
    from PIL import Image

    if output.exists() or output.is_symlink():
        raise ValueError("output_already_exists")
    dataset = experiment / "dataset-batched"
    report_path = experiment / "comparison.json"
    verification = verify_experiment(dataset, report_path)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    manifest_bytes = (dataset / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    sources = {(row["gid"], row["resolution"]): row for row in manifest["galleries"]}
    rows, assets = [], {}
    formats = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "AVIF": "avif"}

    def image(item):
        blob = verified_blob(dataset, item)
        with Image.open(io.BytesIO(blob)) as decoded:
            suffix = formats[decoded.format]
            width, height = decoded.size
            decoded.verify()
        digest = hashlib.sha256(blob).hexdigest()
        assets[f"{digest}.{suffix}"] = blob
        return {"sha256": digest, "suffix": suffix, "bytes": len(blob), "width": width, "height": height}

    for row in report["galleries"]:
        source = sources[row["gid"], row["resolution"]]
        covers = [item for item in row["previews"] if item.get("kind") == "cover"]
        body = [normalized(item["embedding"]) for item in row["pages"] if item["position"] != 1]
        rows.append({"key": f"{row['gid']}-{row['resolution']}", "gid": row["gid"], "resolution": row["resolution"],
                     "ordering": source["ordering"], "image_count": source["image_count"],
                     "pages": [{**image(item), "position": item["position"],
                                "embedding": normalized(item["embedding"]),
                                "embedding_version": report["encoder"]} for item in row["pages"]],
                     "cover": image(covers[0]) if len(covers) == 1 else None,
                     "cover_vector": normalized(covers[0]["embedding"]) if len(covers) == 1 else None,
                     "body_vector": mean_vector(body)})
    # Keep filenames and metadata independent of remote paths and site credentials.
    document = {"schema": SCHEMA, "page_embedding_schema": PAGE_SEARCH_SCHEMA,
                "page_embedding_dimension": verification["embedding_dimensions"],
                "encoder": report["encoder"], "collected_at": manifest["collected_at"],
                "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "source_comparison_sha256": hashlib.sha256(report_bytes).hexdigest(), "galleries": rows}
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    for name, blob in assets.items():
        with (output / "images" / name).open("xb") as stream:
            stream.write(blob)
    # The index is the publication marker, written only after all assets are verified.
    for name, blob in assets.items():
        if (output / "images" / name).read_bytes() != blob:
            raise ValueError("library_write_mismatch")
    write_json_new(output / "index.json", document)
    library = LocalPageLibrary(output)
    library.load()
    for digest in library.assets:
        library.image(digest)
    return {"ok": True, "galleries": len(rows), "comparable_galleries": len(library.paired()),
            "assets": len(assets), "indexed_pages": library.indexed_pages,
            "metadata_status": dict(Counter(row["metadata_status"] for row in report["galleries"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.experiment, args.output)
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"ok": false, "error": "local_library_build_failed"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
