from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exh_rec import db  # noqa: E402
from exh_rec.classification import (  # noqa: E402
    label_continuing_classifier_sample,
    sample_continuing_classifier_candidates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and label the continuing-classifier sample set")
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--sample", type=int, metavar="COUNT")
    parser.add_argument("--random-fraction", type=float, default=0.30)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--label", type=int, metavar="SAMPLE_ID")
    parser.add_argument("--classification", choices=("review", "updates"))
    args = parser.parse_args()
    if (args.label is None) == (args.sample is None):
        parser.error("choose exactly one of --sample or --label")
    if args.label is not None and not args.classification:
        parser.error("--classification is required with --label")

    original_path = db.DB_PATH
    db.DB_PATH = args.db.resolve()
    try:
        db.init_db()
        with db.connect() as conn:
            if args.sample is not None:
                result = sample_continuing_classifier_candidates(
                    conn,
                    limit=args.sample,
                    random_fraction=args.random_fraction,
                    seed=args.seed,
                )
            else:
                result = label_continuing_classifier_sample(
                    conn,
                    args.label,
                    args.classification,
                )
    finally:
        db.DB_PATH = original_path
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
