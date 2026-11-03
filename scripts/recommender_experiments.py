"""Isolated, model-only development ablations with a reserved final holdout."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exh_rec import personalized as p
from scripts.evaluate_recommender import (
    gallery_payload, latest_feedback, materialize_history,
    metric_set, normalized_time, restrict_temporal_training_data,
)

VARIANTS = ("v5", "no_visual", "no_title", "decay_90_days", "explicit_only", "uniform_calibration")


def development_split(rows, since: str, holdout_size: int = 100):
    if holdout_size < 20:
        raise ValueError("holdout requires at least 20 labels")
    cutoff = normalized_time(since)
    cohort = [row for row in rows if normalized_time(row["created_at"]) >= cutoff]
    if len(cohort) < holdout_size + 40:
        raise ValueError("need at least 40 development labels plus the reserved holdout")
    start = len(cohort) - holdout_size
    # A timestamp shared with holdout labels must never enter development.
    while start > 0 and cohort[start - 1]["created_at"] == cohort[start]["created_at"]:
        start -= 1
    if start < 40:
        raise ValueError("timestamp-safe development cohort is too small")
    return cohort[:start], cohort[start:]


def variant_examples(examples, variant, cutoff, training=True):
    examples = deepcopy(examples)
    if variant == "explicit_only" and training:
        examples = [item for item in examples if item["label_source"] != "hath-download"]
    for item in examples:
        if variant == "no_visual":
            for key in ("visual_embedding", "visual_embedding_json", "visual_embedding_version",
                        "visual_image_count", "visual_image_cohesion", "visual_image_min_similarity"):
                item.pop(key, None)
        if variant == "no_title":
            item["title"] = item["title_jpn"] = ""
        if variant == "decay_90_days" and training:
            age = (datetime.fromisoformat(normalized_time(cutoff))
                   - datetime.fromisoformat(normalized_time(item["feedback_at"]))).total_seconds() / 86400
            item["sample_weight"] *= 0.5 ** (max(0, age) / 90)
    return examples


def select_variant(development):
    reference = development["v5"]
    eligible = [name for name, metrics in development.items()
                if name != "v5" and metrics["ndcg_at_20"] >= reference["ndcg_at_20"] + 0.02
                and metrics["precision_at_10"] >= reference["precision_at_10"] - 0.02
                and metrics.get("ece") is not None and reference.get("ece") is not None
                and metrics["ece"] <= reference["ece"]]
    return max(eligible, key=lambda name: (development[name]["ndcg_at_20"], -development[name]["ece"])) if eligible else "v5"


def fit_predict(conn, test, variant, modules):
    cutoff = str(test[0]["created_at"])
    train = variant_examples(p.training_examples(conn, strict_temporal=True), variant, cutoff)
    labels = modules["np"].asarray([item["label"] for item in train], dtype="int8")
    weights = modules["np"].asarray([item["sample_weight"] for item in train], dtype="float32")
    start = p._calibration_start(labels)
    end = len(train) if start is None else start
    selected_c, _ = p._select_c(None, labels[:end], weights[:end], modules, examples=train[:end])
    with ExitStack() as stack:
        if variant == "uniform_calibration":
            original = p._fit_calibrator

            def calibrate(matrix, labels, weights, c_value, modules, start, classifier=None):
                return original(matrix, labels, modules["np"].ones(len(weights)), c_value,
                                modules, start, classifier=classifier)

            stack.enter_context(patch.object(p, "CALIBRATION_RECENCY_DECAY", 0.0))
            stack.enter_context(patch.object(p, "_fit_calibrator", calibrate))
        fitted, classifier, calibrator, start, fit_count = p._fit_temporal_classifier(train, selected_c, modules)
    pairs = [(row, gallery_payload(conn, row["gallery_url"], at=str(row["created_at"]), strict_snapshot=True))
             for row in test]
    pairs = [(row, gallery) for row, gallery in pairs if gallery is not None]
    galleries = [gallery for _, gallery in pairs]
    p._attach_visual_image_features(conn, galleries)
    galleries = variant_examples(galleries, variant, cutoff, training=False)
    logits = classifier.decision_function(p._transform(galleries, fitted, modules))
    probabilities = p._calibrated(logits, calibrator, modules) if calibrator is not None else None
    metrics = metric_set([int(row["vote"] > 0) for row, _ in pairs], probabilities, modules, ranking_scores=logits)
    return {"metrics": metrics, "train_count": len(train), "classifier_fit_count": fit_count,
            "selected_c": selected_c, "calibration_ready": calibrator is not None,
            "requested": len(test), "covered": len(pairs)}


def experiment(db_path: Path, since: str, holdout_size: int = 100):
    modules = p.sklearn_modules()
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as live:
        live.backup(source)
    development, holdout = development_split(latest_feedback(source), since, holdout_size)
    report = {"diagnostic_only": True, "production_read_only": True,
              "scope": "model-only labeled outcomes; not actual serving or activation evidence",
              "label_policy": "explicit-nonneutral-excluding-duplicate-update",
              "selection_rule": "development NDCG@20 gain >=0.02, P@10 loss <=0.02, no ECE regression",
              "holdout_rule": "latest labels reserved before comparison; only selected variant and v5 tested",
              "development_count": len(development), "holdout_count": len(holdout),
              "holdout_start": holdout[0]["created_at"], "development": {}, "holdout": {}}
    for name, test, variants in (("development", development, VARIANTS), ("holdout", holdout, None)):
        if variants is None:
            variants = tuple(dict.fromkeys(("v5", report["selected_variant"])))
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        source.backup(conn)
        restrict_temporal_training_data(conn, [], str(test[0]["created_at"]))
        materialize_history(conn, str(test[0]["created_at"]))
        # Test galleries can first appear after the cutoff; read their snapshots
        # from the immutable source while training remains a strict prefix.
        for row in test:
            gallery = source.execute("SELECT * FROM galleries WHERE url=?", (row["gallery_url"],)).fetchone()
            if conn.execute("SELECT 1 FROM galleries WHERE url=?", (row["gallery_url"],)).fetchone() is None:
                conn.execute("INSERT INTO galleries(url,title) VALUES (?,?)", (row["gallery_url"], gallery["title"]))
        for variant in variants:
            report[name][variant] = fit_predict(conn, test, variant, modules)
        conn.close()
        if name == "development":
            report["selected_variant"] = select_variant({key: item["metrics"] for key, item in report[name].items()})
    source.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--holdout-size", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    report = experiment(args.db, args.since, args.holdout_size)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
