from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exh_rec import db  # noqa: E402
from exh_rec.personalized import (  # noqa: E402
    _classifier,
    _fit_vectorizers,
    _transform,
    _visual,
    sklearn_modules,
    training_examples,
)
from scripts.evaluate_recommender import metric_set, non_overlapping_starts  # noqa: E402


def block_boundaries(fitted: dict) -> list[tuple[int, int]]:
    metadata_end = len(fitted["dict_vectorizer"].get_feature_names_out())
    title_end = metadata_end + (
        0 if fitted["title_vectorizer"] is None else len(fitted["title_vectorizer"].get_feature_names_out())
    )
    numeric_end = title_end + len(fitted["numeric_names"])
    total = numeric_end + fitted["visual_dims"]
    return [(0, metadata_end), (metadata_end, title_end), (title_end, numeric_end), (numeric_end, total)]


def transform_variant(train, test, fitted: dict, modules: dict, center_visual: bool, balance_blocks: bool):
    sparse = modules["sparse"]
    np = modules["np"]
    train_blocks = []
    test_blocks = []
    boundaries = block_boundaries(fitted)
    for block_index, (start, end) in enumerate(boundaries):
        train_block = train[:, start:end].tocsr()
        test_block = test[:, start:end].tocsr()
        if block_index == 3 and end > start and center_visual:
            train_dense = train_block.toarray()
            test_dense = test_block.toarray()
            train_present = np.linalg.norm(train_dense, axis=1) > 0
            test_present = np.linalg.norm(test_dense, axis=1) > 0
            mean = train_dense[train_present].mean(axis=0) if int(train_present.sum()) else np.zeros(end - start)
            for values, present in ((train_dense, train_present), (test_dense, test_present)):
                indices = np.flatnonzero(present)
                centered = values[indices] - mean
                norms = np.linalg.norm(centered, axis=1)
                nonzero = norms > 0
                centered[nonzero] /= norms[nonzero, None]
                values[indices] = centered
                values[~present] = 0.0
            train_block = sparse.csr_matrix(train_dense)
            test_block = sparse.csr_matrix(test_dense)
        if balance_blocks and end > start:
            row_norms = np.sqrt(train_block.multiply(train_block).sum(axis=1)).A1
            active = row_norms[row_norms > 0]
            scale = 1.0 / float(active.mean()) if len(active) else 1.0
            train_block = train_block * scale
            test_block = test_block * scale
        train_blocks.append(train_block)
        test_blocks.append(test_block)
    return sparse.hstack(train_blocks, format="csr"), sparse.hstack(test_blocks, format="csr")


def benchmark(db_path: Path, window: int = 100) -> dict:
    modules = sklearn_modules()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    examples = training_examples(conn)
    conn.close()
    starts = non_overlapping_starts(len(examples), window)
    variants = {
        "raw": (False, False),
        "visual_centered": (True, False),
        "block_balanced": (False, True),
        "visual_centered_block_balanced": (True, True),
    }
    reports = {name: [] for name in variants}
    for start in starts:
        train_items = examples[:start]
        test_items = examples[start : start + window]
        fitted = _fit_vectorizers(train_items, modules)
        transformed_train = fitted["matrix"]
        transformed_test = _transform(test_items, fitted, modules)
        visual_start = transformed_train.shape[1] - fitted["visual_dims"]
        raw_train_visual = modules["np"].asarray(
            [_visual(item, fitted["visual_dims"]) for item in train_items], dtype="float32"
        )
        raw_test_visual = modules["np"].asarray(
            [_visual(item, fitted["visual_dims"]) for item in test_items], dtype="float32"
        )
        raw_train = modules["sparse"].hstack(
            [transformed_train[:, :visual_start], modules["sparse"].csr_matrix(raw_train_visual)],
            format="csr",
        )
        raw_test = modules["sparse"].hstack(
            [transformed_test[:, :visual_start], modules["sparse"].csr_matrix(raw_test_visual)],
            format="csr",
        )
        train_labels = modules["np"].asarray([item["label"] for item in train_items])
        test_labels = [int(item["label"]) for item in test_items]
        weights = modules["np"].asarray([item["sample_weight"] for item in train_items])
        for name, (center_visual, balance_blocks) in variants.items():
            train_matrix, test_matrix = transform_variant(
                raw_train, raw_test, fitted, modules, center_visual, balance_blocks
            )
            model = _classifier(modules, 0.2)
            model.fit(train_matrix, train_labels, sample_weight=weights)
            scores = [float(value) for value in model.decision_function(test_matrix)]
            probabilities = [1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, value)))) for value in scores]
            reports[name].append(metric_set(test_labels, probabilities, modules, ranking_scores=scores))
    summary = {}
    for name, folds in reports.items():
        summary[name] = {
            metric: round(statistics.mean(fold[metric] for fold in folds), 6)
            for metric in ("roc_auc", "pr_auc", "ndcg_at_20", "precision_at_10")
        }
    return {
        "database": str(db_path),
        "sample_count": len(examples),
        "fold_count": len(starts),
        "window": window,
        "warning": "current-feature diagnostic; not eligible for production acceptance",
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic ablation for personalized feature transforms")
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = benchmark(args.db.resolve(), max(20, min(500, args.window)))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
