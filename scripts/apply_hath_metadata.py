"""Explicitly stage or insert missing metadata; default is an offline preview."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec.hath_metadata_repair import (
    SCHEMA, MetadataClient, apply_metadata, document, missing_targets, staged_rows, targets_from_evidence,
)
from exh_rec.hath_recovery import RecoveryError, runtime_settings, validate_output_directory, write_new
from exh_rec.local_pages import LocalPageLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--library", type=Path, default=ROOT / "data/hath-page-library")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--stage", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    output_created = False
    try:
        if (args.fetch and args.apply) or ((args.fetch or args.apply) and not args.confirmed) or not 1 <= args.limit <= 25:
            raise RecoveryError("explicit_confirmation_required")
        output = validate_output_directory(args.output)
        database = args.database
        if database is None:
            config = document(ROOT / "data/launcher/config.json")
            directory = Path(config.get("data_dir") or ROOT / "data")
            database = (directory if directory.is_absolute() else ROOT / directory) / "recommender.sqlite3"
        targets, evidence = targets_from_evidence(args.evidence, LocalPageLibrary(args.library))
        staged = staged_rows(args.stage, targets, evidence)
        missing = missing_targets(database, targets)
        output.mkdir(parents=True, exist_ok=False)
        output_created = True
        write_new(output / "plan.json", {"evidence": evidence, "targets": targets, "missing": len(missing)})
        if args.fetch:
            done = {target["gid"] for target, _ in staged}
            batch = [target for target in missing if target["gid"] not in done][:args.limit]
            if not batch:
                raise RecoveryError("no_pending_metadata")
            entries = MetadataClient(*runtime_settings(database)).fetch(batch)
            write_new(output / "metadata.json", {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
                                                 "evidence": evidence, "entries": entries})
            result = {"ok": True, "mode": "fetch", "fetched": len(entries), "production_read_only": True}
        elif args.apply:
            if len(staged) != len(targets):
                raise RecoveryError("incomplete_metadata_stage")
            applied = apply_metadata(database, staged, output / "before.sqlite3", output / "receipt.json")
            result = {"ok": True, "mode": "apply", "inserted": applied["inserted"], "skipped": applied["skipped"]}
        else:
            result = {"ok": True, "mode": "preview", "targets": len(targets), "missing": len(missing), "staged": len(staged)}
        write_new(output / "summary.json", result)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RecoveryError) else "metadata_repair_failed"
        failure = {"ok": False, "error": reason}
        if output_created:
            try:
                write_new(output / "failure.json", failure)
            except Exception:
                pass
        print(json.dumps(failure))
        return 1


if __name__ == "__main__":
    sys.exit(main())
