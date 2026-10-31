"""Read-only recovery plan for H@H metadata gaps; never exports gallery URLs."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.probe_hath_pages import join_metadata, write_json_new


URL_TABLES = ("hath_downloads", "feedback", "gallery_marks", "gallery_feature_snapshots", "recommendation_impressions")


def plan(database, source):
    galleries = copy.deepcopy(source["galleries"])
    join_metadata(database, galleries)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        items = []
        for gallery in galleries:
            missing = gallery["metadata_status"] == "missing"
            no_samples = not missing and not gallery["thumbnail_sample_entries"]
            if not missing and not no_samples:
                continue
            gid = gallery["gid"]
            if not re.fullmatch(r"[0-9]{1,20}", gid):
                raise ValueError("invalid_gallery_id")
            evidence, candidates = [], set()
            if missing:
                pattern = re.compile(r"https://(?:exhentai|e-hentai)\.org/g/" + re.escape(gid) + r"/[0-9a-f]+/?\Z")
                for table in URL_TABLES:
                    if table not in tables:
                        continue
                    # Table identifiers are a fixed allowlist; URLs never leave memory.
                    rows = conn.execute("SELECT DISTINCT gallery_url FROM " + table +
                                        " WHERE gallery_url LIKE ? LIMIT 100", ("%/g/" + gid + "/%",)).fetchall()
                    valid = {row[0] for row in rows if isinstance(row[0], str) and pattern.fullmatch(row[0])}
                    if valid:
                        evidence.append(table)
                        candidates.update(valid)
            action = "generate_local_previews" if no_samples else (
                "known_url_requires_normal_metadata_fetch" if candidates else "gallery_url_not_found_in_checked_sources")
            items.append({"gid": gid, "resolution": gallery["resolution"], "metadata_missing": missing,
                          "thumbnail_sample_entries_missing": no_samples, "gap_reason": gallery["gap_reason"],
                          "known_url_evidence_tables": evidence, "known_url_candidates": len(candidates),
                          "marker_has_matching_gallery_url": gallery.get("marker_has_matching_gallery_url", False),
                          "local_image_count": gallery["image_count"], "next_action": action})
    return {"schema": "hath-metadata-plan-v1", "production_read_only": True,
            "created_at": datetime.now(timezone.utc).isoformat(), "items": items,
            "summary": {"galleries_checked": len(galleries), "metadata_missing": sum(item["metadata_missing"] for item in items),
                        "thumbnail_sample_entries_missing": sum(item["thumbnail_sample_entries_missing"] for item in items),
                        "missing_metadata_with_known_url": sum(item["metadata_missing"] and item["known_url_candidates"] > 0 for item in items),
                        "actions": dict(Counter(item["next_action"] for item in items)),
                        "gap_reasons": dict(Counter(item["gap_reason"] for item in items if item["gap_reason"]))},
            "limitations": ["Only explicitly checked local URL columns and the bounded completion-marker check; no browser history or credentials.",
                            "No metadata fetches, invented URLs, database repair, training or production changes.",
                            "Existing image files can supply local previews even when site metadata cannot be recovered offline."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output_already_exists")
    config = json.loads((ROOT / "data/launcher/config.json").read_text(encoding="utf-8"))
    data = Path(config.get("data_dir") or ROOT / "data")
    if not data.is_absolute():
        data = ROOT / data
    source = json.loads(args.source.read_text(encoding="utf-8"))
    if source.get("schema") not in {"hath-page-source-v1", "hath-page-experiment-v1"}:
        raise ValueError("invalid_source_manifest")
    report = plan(data / "recommender.sqlite3", source)
    write_json_new(args.output, report)
    print(json.dumps({"ok": True, "summary": report["summary"]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('{"ok": false, "error": "metadata_plan_failed"}', file=sys.stderr)
        sys.exit(1)
