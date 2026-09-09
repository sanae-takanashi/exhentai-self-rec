from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec import db  # noqa: E402
from exh_rec.personalized import (  # noqa: E402
    _classifier,
    _fit_vectorizers,
    _transform,
    sklearn_modules,
    training_examples,
)
from exh_rec.visual import (  # noqa: E402
    DINOV2_BAKEOFF_VISUAL_VERSION,
    DINOV2_VISUAL_VERSION,
    SIGLIP2_VISUAL_VERSION,
)


VARIANTS = {
    "no_visual": None,
    "production_dinov2": DINOV2_VISUAL_VERSION,
    "bakeoff_dinov2": DINOV2_BAKEOFF_VISUAL_VERSION,
    "siglip2": SIGLIP2_VISUAL_VERSION,
}
HIGHER_IS_BETTER = {"roc_auc", "precision_at_10", "ndcg_at_20"}


def parse_embedding(raw: object) -> list[float] | None:
    try:
        values = json.loads(str(raw or "null"))
        result = [float(value) for value in values]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not result or not all(math.isfinite(value) for value in result):
        return None
    return result


def load_embeddings(conn: sqlite3.Connection, variant: str) -> dict[str, list[float]]:
    version = VARIANTS[variant]
    if variant == "no_visual":
        return {}
    if variant == "production_dinov2":
        rows = conn.execute(
            """
            SELECT url AS gallery_url, visual_embedding_json AS embedding_json
            FROM galleries
            WHERE visual_embedding_version = ? AND visual_embedding_json IS NOT NULL
            """,
            (version,),
        )
    else:
        rows = conn.execute(
            """
            SELECT gallery_url, embedding_json
            FROM gallery_visual_embeddings
            WHERE embedding_version = ?
            """,
            (version,),
        )
    result = {}
    for row in rows:
        embedding = parse_embedding(row["embedding_json"])
        if embedding is not None:
            result[str(row["gallery_url"])] = embedding
    return result


def common_examples(
    conn: sqlite3.Connection,
    variants: list[str],
) -> tuple[list[dict], dict[str, dict[str, list[float]]], dict]:
    examples = training_examples(conn, strict_temporal=False)
    embeddings = {variant: load_embeddings(conn, variant) for variant in variants}
    visual_variants = [variant for variant in variants if variant != "no_visual"]
    common_urls = {str(item["url"]) for item in examples}
    for variant in visual_variants:
        common_urls &= set(embeddings[variant])
    selected = [item for item in examples if str(item["url"]) in common_urls]
    coverage = {
        "labeled_total": len(examples),
        "common_labeled": len(selected),
        "positive_count": sum(int(item["label"]) for item in selected),
        "negative_count": sum(1 - int(item["label"]) for item in selected),
        "variant_embedding_counts": {
            variant: len(embeddings[variant]) if variant != "no_visual" else None
            for variant in variants
        },
    }
    return selected, embeddings, coverage


def variant_examples(
    examples: list[dict],
    embeddings: dict[str, dict[str, list[float]]],
    variant: str,
) -> list[dict]:
    result = []
    for source in examples:
        item = copy.copy(source)
        item.pop("visual_image_count", None)
        item.pop("visual_image_cohesion", None)
        item.pop("visual_image_min_similarity", None)
        if variant == "no_visual":
            item["visual_embedding"] = None
            item["visual_embedding_json"] = None
            item["visual_embedding_version"] = None
        else:
            vector = embeddings[variant][str(item["url"])]
            item["visual_embedding"] = vector
            item["visual_embedding_json"] = json.dumps(vector, ensure_ascii=True)
            item["visual_embedding_version"] = VARIANTS[variant]
        result.append(item)
    return result


def expected_calibration_error(labels, probabilities, bins: int = 10) -> float:
    total = len(labels)
    result = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (probabilities >= lower) & (
            probabilities < upper if index < bins - 1 else probabilities <= upper
        )
        count = int(mask.sum())
        if count:
            result += count / total * abs(float(labels[mask].mean()) - float(probabilities[mask].mean()))
    return result


def ndcg_at(labels, scores, cutoff: int) -> float:
    order = sorted(range(len(labels)), key=lambda index: float(scores[index]), reverse=True)[:cutoff]
    ideal = sorted((int(value) for value in labels), reverse=True)[:cutoff]
    dcg = sum(float(labels[index]) / math.log2(rank + 2) for rank, index in enumerate(order))
    idcg = sum(float(value) / math.log2(rank + 2) for rank, value in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def metrics(labels, probabilities, modules: dict) -> dict:
    np = modules["np"]
    y = np.asarray(labels, dtype="int8")
    p = np.asarray(probabilities, dtype="float64")
    order = np.argsort(-p)
    top = order[: min(10, len(order))]
    return {
        "count": len(labels),
        "positive_count": int(y.sum()),
        "roc_auc": (
            round(float(modules["roc_auc_score"](y, p)), 6) if len(set(labels)) > 1 else None
        ),
        "precision_at_10": round(float(y[top].mean()), 6) if len(top) else None,
        "ndcg_at_20": round(ndcg_at(y, p, 20), 6),
        "brier": round(float(modules["brier_score_loss"](y, p)), 6),
        "ece": round(expected_calibration_error(y, p), 6),
    }


def fit_predict(train: list[dict], test: list[dict], modules: dict) -> list[float]:
    fitted = _fit_vectorizers(train, modules)
    labels = modules["np"].asarray([int(item["label"]) for item in train], dtype="int8")
    weights = modules["np"].asarray(
        [float(item.get("sample_weight") or 1.0) for item in train], dtype="float32"
    )
    model = _classifier(modules, 0.2)
    model.fit(fitted["matrix"], labels, sample_weight=weights)
    return [float(value) for value in model.predict_proba(_transform(test, fitted, modules))[:, 1]]


def summarize(values: list[float]) -> dict:
    return {
        "mean": round(statistics.fmean(values), 6) if values else None,
        "stddev": round(statistics.pstdev(values), 6) if len(values) > 1 else 0.0 if values else None,
        "folds": len(values),
    }


def bootstrap_interval(values: list[float], np, seed: int = 20260902) -> list[float] | None:
    if not values:
        return None
    rng = np.random.default_rng(seed)
    source = np.asarray(values, dtype="float64")
    samples = rng.choice(source, size=(5000, len(source)), replace=True).mean(axis=1)
    return [round(float(value), 6) for value in np.quantile(samples, [0.025, 0.975])]


def paired_gallery_bootstrap_interval(
    labels,
    reference_probabilities,
    candidate_probabilities,
    metric: str,
    modules: dict,
    *,
    seed: int = 20260902,
    samples: int = 5000,
) -> list[float] | None:
    """Bootstrap paired encoder deltas over galleries, not repeated CV folds."""
    np = modules["np"]
    labels = np.asarray(labels, dtype="int8")
    reference = np.asarray(reference_probabilities, dtype="float64")
    candidate = np.asarray(candidate_probabilities, dtype="float64")
    if not len(labels) or len(reference) != len(labels) or len(candidate) != len(labels):
        return None
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(max(1, int(samples))):
        indices = rng.integers(0, len(labels), size=len(labels))
        sampled_labels = labels[indices]
        reference_metric = metrics(sampled_labels, reference[indices], modules).get(metric)
        candidate_metric = metrics(sampled_labels, candidate[indices], modules).get(metric)
        if reference_metric is None or candidate_metric is None:
            continue
        delta = float(candidate_metric) - float(reference_metric)
        deltas.append(delta if metric in HIGHER_IS_BETTER else -delta)
    if not deltas:
        return None
    return [round(float(value), 6) for value in np.quantile(deltas, [0.025, 0.975])]


def repeated_stratified_evaluation(
    by_variant: dict[str, list[dict]],
    modules: dict,
    *,
    folds: int,
    repeats: int,
    bootstrap_samples: int = 1000,
) -> dict:
    from sklearn.model_selection import RepeatedStratifiedKFold

    first = next(iter(by_variant.values()))
    labels = modules["np"].asarray([int(item["label"]) for item in first], dtype="int8")
    splitter = RepeatedStratifiedKFold(n_splits=folds, n_repeats=repeats, random_state=20260902)
    reports = {variant: [] for variant in by_variant}
    # The first complete K-fold pass yields one out-of-fold prediction per
    # gallery. It is the independent unit for paired bootstrap uncertainty.
    oof_predictions = {variant: [None] * len(labels) for variant in by_variant}
    for split_index, (train_indices, test_indices) in enumerate(splitter.split(labels, labels)):
        for variant, examples in by_variant.items():
            train = [examples[int(index)] for index in train_indices]
            test = [examples[int(index)] for index in test_indices]
            probabilities = fit_predict(train, test, modules)
            if split_index < folds:
                for index, probability in zip(test_indices, probabilities):
                    oof_predictions[variant][int(index)] = probability
            reports[variant].append(
                {"split": split_index, **metrics([int(item["label"]) for item in test], probabilities, modules)}
            )
    aggregate = {}
    for variant, folds_report in reports.items():
        aggregate[variant] = {
            metric: summarize([float(item[metric]) for item in folds_report if item.get(metric) is not None])
            for metric in ("roc_auc", "precision_at_10", "ndcg_at_20", "brier", "ece")
        }
    baseline = next(
        (name for name in ("production_dinov2", "bakeoff_dinov2") if name in reports),
        next(iter(reports)),
    )
    paired = {}
    for variant, folds_report in reports.items():
        if variant == baseline:
            continue
        paired[variant] = {}
        for metric in ("roc_auc", "precision_at_10", "ndcg_at_20", "brier", "ece"):
            deltas = [
                float(candidate[metric]) - float(reference[metric])
                for candidate, reference in zip(folds_report, reports[baseline])
                if candidate.get(metric) is not None and reference.get(metric) is not None
            ]
            improvements = [value if metric in HIGHER_IS_BETTER else -value for value in deltas]
            oof_ready = (
                all(value is not None for value in oof_predictions[baseline])
                and all(value is not None for value in oof_predictions[variant])
            )
            paired[variant][metric] = {
                "raw_delta_mean": round(statistics.fmean(deltas), 6) if deltas else None,
                "improvement_mean": round(statistics.fmean(improvements), 6) if improvements else None,
                "improvement_ci95": (
                    paired_gallery_bootstrap_interval(
                        labels,
                        oof_predictions[baseline],
                        oof_predictions[variant],
                        metric,
                        modules,
                        samples=bootstrap_samples,
                    )
                    if oof_ready
                    else None
                ),
                "ci_method": "paired-gallery-bootstrap-oof",
                "ci_unit_count": len(labels) if oof_ready else 0,
            }
    return {
        "baseline": baseline,
        "aggregate": aggregate,
        "paired_deltas": paired,
        "paired_oof_count": len(labels),
        "paired_bootstrap_samples": max(1, int(bootstrap_samples)),
        "folds": reports,
    }


def temporal_evaluation(by_variant: dict[str, list[dict]], modules: dict) -> dict:
    count = len(next(iter(by_variant.values())))
    split = max(1, int(count * 0.8))
    result = {"train_count": split, "test_count": count - split, "variants": {}}
    for variant, examples in by_variant.items():
        train, test = examples[:split], examples[split:]
        train_labels = {int(item["label"]) for item in train}
        test_labels = [int(item["label"]) for item in test]
        if len(train_labels) < 2 or len(set(test_labels)) < 2:
            result["variants"][variant] = {"status": "insufficient-class-coverage"}
            continue
        probabilities = fit_predict(train, test, modules)
        result["variants"][variant] = {"status": "ok", **metrics(test_labels, probabilities, modules)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only visual encoder shadow bake-off")
    parser.add_argument("--database", type=Path, default=db.DB_PATH)
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "visual-encoder-benchmark.json")
    args = parser.parse_args()
    variants = [value.strip() for value in args.variants.split(",") if value.strip()]
    unknown = [value for value in variants if value not in VARIANTS]
    if unknown or not variants:
        parser.error(f"variants must be selected from {', '.join(VARIANTS)}")
    started = time.perf_counter()
    conn = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        has_shadow_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gallery_visual_embeddings'"
        ).fetchone()
        if any(variant not in {"no_visual", "production_dinov2"} for variant in variants) and not has_shadow_table:
            report = {
                "status": "shadow-embeddings-unavailable",
                "database": str(args.database.resolve()),
                "variants": variants,
                "shadow_only": True,
            }
        else:
            examples, embeddings, coverage = common_examples(conn, variants)
            min_class = min(coverage["positive_count"], coverage["negative_count"])
            if len(examples) < 20 or min_class < max(2, args.folds):
                report = {
                    "status": "insufficient-common-labeled-data",
                    "database": str(args.database.resolve()),
                    "variants": variants,
                    "coverage": coverage,
                    "shadow_only": True,
                }
            else:
                modules = sklearn_modules()
                by_variant = {
                    variant: variant_examples(examples, embeddings, variant) for variant in variants
                }
                report = {
                    "status": "ok",
                    "database": str(args.database.resolve()),
                    "variants": variants,
                    "coverage": coverage,
                    "repeated_stratified": repeated_stratified_evaluation(
                        by_variant,
                        modules,
                        folds=max(2, min(args.folds, min_class)),
                        repeats=max(1, args.repeats),
                        bootstrap_samples=max(100, args.bootstrap_samples),
                    ),
                    "temporal_newest_20_percent": temporal_evaluation(by_variant, modules),
                    "shadow_only": True,
                }
    finally:
        conn.close()
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["limitations"] = [
        "Current shadow embeddings may postdate their labels, so this is diagnostic rather than release evidence.",
        "Brier and ECE use raw fold-local logistic probabilities, not the production temporal calibrator.",
        "All visual variants use the exact same common-gallery rows and train-only visual centering.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
