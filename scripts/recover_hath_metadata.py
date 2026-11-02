"""Plan or run a bounded, read-only gid/SHA-1 recovery investigation."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec.hath_recovery import RecoveryClient, load_reuse, markdown_repair_preview, markdown_report, query_keys, recover, recovery_plan, repair_preview, runtime_settings, validate_output_directory, write_new
from exh_rec.local_pages import LocalPageLibrary, read_regular


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=ROOT / "data/hath-page-library")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-requests", type=int, default=12)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reuse", type=Path, action="append", default=[], metavar="DIRECTORY",
                        help="Reuse validated successful lookups from a prior plan/journal (24-hour maximum age).")
    args = parser.parse_args()
    try:
        args.output = validate_output_directory(args.output)
        if not 1 <= args.max_requests <= 300:
            raise ValueError("invalid_request_limit")
        database = args.database
        if database is None:
            config = json.loads(read_regular(ROOT / "data/launcher/config.json", 1024 * 1024))
            directory = Path(config.get("data_dir") or ROOT / "data")
            if not directory.is_absolute():
                directory = ROOT / directory
            database = directory / "recommender.sqlite3"
        plan = recovery_plan(LocalPageLibrary(args.library), database, args.limit)
        reuse, sources = load_reuse(args.reuse, plan)
        plan["reuse_sources"] = sources
        plan["reusable_queries"] = len(reuse)
        plan["new_queries"] = len({pair for row in plan["selected"] for pair in query_keys(row)} - reuse.keys())
        client = RecoveryClient(*runtime_settings(database)) if args.execute and plan["new_queries"] else None
        validate_output_directory(args.output)
        args.output.mkdir(parents=True, exist_ok=False)
        write_new(args.output / "plan.json", plan)
        if not args.execute:
            print(json.dumps({"ok": True, "mode": "plan", "missing": plan["missing"], "selected": len(plan["selected"]), "unselected": plan["unselected"],
                              "reusable_queries": plan["reusable_queries"], "new_queries": plan["new_queries"]}))
            return 0
        with (args.output / "events.jsonl").open("x", encoding="utf-8") as stream:
            def record(event):
                stream.write(json.dumps(event, ensure_ascii=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            report = recover(plan, client.lookup if client else None, args.max_requests, record, reuse)
        write_new(args.output / "report.json", report)
        with (args.output / "report.md").open("x", encoding="utf-8") as stream:
            stream.write(markdown_report(report))
        preview = repair_preview(report, database)
        write_new(args.output / "repair-preview.json", preview)
        with (args.output / "repair-preview.md").open("x", encoding="utf-8") as stream:
            stream.write(markdown_repair_preview(preview))
        print(json.dumps({"ok": True, "mode": "execute", "read_only": True, "summary": report["summary"],
                          "repair_preview": preview["summary"]}))
        return 0
    except Exception:
        print('{"ok": false, "error": "recovery_failed"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
