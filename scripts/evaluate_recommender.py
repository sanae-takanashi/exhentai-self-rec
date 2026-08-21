from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exh_rec import db  # noqa: E402
from exh_rec.personalized import (  # noqa: E402
    FEATURE_SCHEMA,
    MODEL_SCHEMA,
    PersonalizedModelUnavailable,
    gallery_feature_snapshot,
    score_personalized_galleries,
    sklearn_modules,
    train_personalized_model,
)
from exh_rec.recommender import (  # noqa: E402
    MODEL_MODE_HYBRID,
    parse_visual_embedding,
    recommend_page,
    retrain_model,
    score_gallery,
    tag_corpus_strengths,
    visual_preference_model,
)


def latest_feedback(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.id, f.gallery_url, f.vote, f.score, f.created_at
        FROM feedback f
        JOIN (SELECT gallery_url, MAX(id) AS id FROM feedback GROUP BY gallery_url) latest
          ON latest.id = f.id
        WHERE f.vote != 0
        ORDER BY f.created_at, f.id
        """
    ).fetchall()


def gallery_payload(
    conn: sqlite3.Connection,
    gallery_url: str,
    at: str | None = None,
    strict_snapshot: bool = False,
) -> dict | None:
    row = conn.execute("SELECT * FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if row is None:
        return None
    gallery = dict(row)
    if at:
        snapshot = gallery_feature_snapshot(conn, gallery_url, at)
        if snapshot is None and strict_snapshot:
            return None
        if snapshot:
            gallery.update(snapshot)
    gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
    gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json") or "{}")
    gallery["visual_embedding"] = parse_visual_embedding(gallery.get("visual_embedding_json"))
    return gallery


def non_overlapping_starts(count: int, window: int, max_folds: int = 5) -> list[int]:
    minimum_train = max(50, count // 3)
    available = max(0, count - minimum_train)
    fold_count = min(max_folds, available // window)
    if fold_count <= 0 and available >= 20:
        fold_count = 1
        window = available
    first = count - fold_count * window
    return [first + index * window for index in range(fold_count)]


def baseline_scores(galleries: list[dict]) -> dict[str, list[float]]:
    random_scores = [
        int(hashlib.sha256(str(item.get("url") or "").encode("utf-8")).hexdigest()[:12], 16)
        / float(16**12)
        for item in galleries
    ]
    recency_values = [str(item.get("feature_snapshot_at") or item.get("first_seen_at") or "") for item in galleries]
    recency_order = {value: index for index, value in enumerate(sorted(set(recency_values)))}
    recency_scores = [float(recency_order[value]) for value in recency_values]
    site_rating = [float(item.get("rating") or 0.0) for item in galleries]
    return {"random": random_scores, "recency": recency_scores, "site_rating": site_rating}


def restrict_temporal_training_data(
    conn: sqlite3.Connection,
    training_ids: list[int],
    test_start: str,
) -> None:
    if training_ids:
        placeholders = ",".join("?" for _ in training_ids)
        conn.execute(f"DELETE FROM feedback WHERE id NOT IN ({placeholders})", training_ids)
    else:
        conn.execute("DELETE FROM feedback")
    conn.execute(
        """
        DELETE FROM gallery_marks
        WHERE julianday(updated_at) IS NULL
           OR julianday(updated_at) >= julianday(?)
        """,
        (test_start,),
    )
    conn.execute(
        """
        DELETE FROM gallery_classification_overrides
        WHERE julianday(updated_at) IS NULL
           OR julianday(updated_at) >= julianday(?)
        """,
        (test_start,),
    )
    conn.execute(
        """
        DELETE FROM hath_downloads
        WHERE completed_at IS NULL
           OR julianday(completed_at) IS NULL
           OR julianday(completed_at) >= julianday(?)
        """,
        (test_start,),
    )


def metric_set(
    labels: list[int], probabilities: list[float], modules: dict, ranking_scores: list[float] | None = None
) -> dict:
    np = modules["np"]
    y = np.asarray(labels, dtype="int8")
    p = np.asarray(probabilities, dtype="float64")
    ranking = p if ranking_scores is None else np.asarray(ranking_scores, dtype="float64")
    order = np.argsort(-ranking)
    result = {
        "count": len(labels),
        "positive_count": int(y.sum()),
        "roc_auc": None,
        "pr_auc": None,
        "brier": round(float(modules["brier_score_loss"](y, p)), 6),
        "ece": round(expected_calibration_error(y, p), 6),
    }
    if len(set(labels)) > 1:
        result["roc_auc"] = round(float(modules["roc_auc_score"](y, ranking)), 6)
        result["pr_auc"] = round(float(modules["average_precision_score"](y, ranking)), 6)
    for cutoff in (10, 20):
        selected = order[: min(cutoff, len(order))]
        result[f"precision_at_{cutoff}"] = round(float(y[selected].mean()), 6) if len(selected) else None
        result[f"ndcg_at_{cutoff}"] = round(ndcg_at(y, ranking, cutoff), 6)
    high = p >= 0.8
    low = p <= 0.2
    result["high_confidence_false_positive_rate"] = (
        round(float((1 - y[high]).mean()), 6) if int(high.sum()) else None
    )
    result["low_score_missed_positive_rate"] = round(float(y[low].mean()), 6) if int(low.sum()) else None
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


def ndcg_at(labels, probabilities, cutoff: int) -> float:
    order = sorted(range(len(labels)), key=lambda index: probabilities[index], reverse=True)[:cutoff]
    ideal = sorted(labels, reverse=True)[:cutoff]
    dcg = sum(float(labels[index]) / math.log2(rank + 2) for rank, index in enumerate(order))
    idcg = sum(float(value) / math.log2(rank + 2) for rank, value in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def evaluate(db_path: Path, window: int = 100, strict_snapshots: bool = True) -> dict:
    modules = sklearn_modules()
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    feedback = latest_feedback(source)
    starts = non_overlapping_starts(len(feedback), window)
    folds = []
    eligible_test_count = 0
    requested_test_count = 0
    for start in starts:
        test = feedback[start : start + window]
        if not test:
            continue
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        source.backup(conn)
        training_ids = [int(row["id"]) for row in feedback[:start]]
        restrict_temporal_training_data(conn, training_ids, str(test[0]["created_at"]))
        retrain_model(conn)
        artifact = train_personalized_model(
            conn,
            persist=False,
            strict_temporal=strict_snapshots,
        )

        bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
        weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
        visual = visual_preference_model(conn)
        strengths = tag_corpus_strengths(conn)
        requested_test_count += len(test)
        paired = []
        for row in test:
            gallery = gallery_payload(
                conn,
                row["gallery_url"],
                at=str(row["created_at"]),
                strict_snapshot=strict_snapshots,
            )
            if gallery is not None:
                paired.append((row, gallery))
        eligible_test_count += len(paired)
        if len(paired) < 20 or not artifact.get("ready"):
            conn.close()
            continue
        test = [row for row, _gallery in paired]
        galleries = [gallery for _row, gallery in paired]
        labels = [1 if float(row["vote"]) > 0 else 0 for row in test]
        legacy = []
        legacy_scores = []
        for gallery in galleries:
            score, _ = score_gallery(gallery, bootstrap, weights, visual_model=visual, tag_strengths=strengths)
            legacy_scores.append(score)
            legacy.append(1.0 / (1.0 + math.exp(-max(-12.0, min(12.0, score)))))
        predictions, _ = score_personalized_galleries(conn, galleries, artifact=artifact)
        personalized = [float(item["like_probability"]) for item in predictions]
        personalized_model = list(personalized)
        served_scores = None
        if personalized:
            test_urls = [str(row["gallery_url"]) for row in test]
            conn.execute("UPDATE galleries SET review_excluded = 1")
            placeholders = ",".join("?" for _ in test_urls)
            conn.execute(f"UPDATE galleries SET review_excluded = 0 WHERE url IN ({placeholders})", test_urls)
            served = recommend_page(
                conn,
                limit=len(test_urls),
                candidate_limit=max(100, len(test_urls)),
                freshness_weight=1.0,
                model_mode=MODEL_MODE_HYBRID,
                personalized_artifact=artifact,
            )["items"]
            served_positions = {item["url"]: index for index, item in enumerate(served)}
            served_probabilities = {
                item["url"]: float(item["like_probability"])
                for item in served
                if item.get("like_probability") is not None
            }
            personalized = [
                served_probabilities.get(gallery["url"], probability)
                for gallery, probability in zip(galleries, personalized)
            ]
            served_scores = [
                float(len(test_urls) - served_positions[gallery["url"]])
                if gallery["url"] in served_positions
                else float(-1 - index)
                for index, gallery in enumerate(galleries)
            ]
        fold = {
            "train_count": start,
            "test_count": len(test),
            "test_start": test[0]["created_at"],
            "test_end": test[-1]["created_at"],
            "legacy": metric_set(labels, legacy, modules, ranking_scores=legacy_scores),
            "baselines": {
                name: metric_set(labels, [1.0 / (1.0 + math.exp(-score)) for score in scores], modules, ranking_scores=scores)
                for name, scores in baseline_scores(galleries).items()
            },
            "personalized": (
                metric_set(labels, personalized, modules, ranking_scores=served_scores)
                if personalized else None
            ),
            "personalized_model": (
                metric_set(labels, personalized_model, modules) if personalized_model else None
            ),
            "served_count": 0 if served_scores is None else sum(score > 0 for score in served_scores),
            "model": {
                key: artifact.get(key)
                for key in ("status", "selected_c", "sample_count", "calibration")
            },
            "segments": segment_metrics(galleries, labels, personalized, modules) if personalized else {},
        }
        folds.append(fold)
        conn.close()
    source.close()
    summary = summarize(folds)
    snapshot_coverage = eligible_test_count / requested_test_count if requested_test_count else 0.0
    summary["snapshot_coverage"] = round(snapshot_coverage, 6)
    summary["strict_snapshots"] = strict_snapshots
    summary["acceptance"] = acceptance(summary, folds)
    return {
        "model_schema": MODEL_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "sample_count": len(feedback),
        "database": str(db_path),
        "folds": folds,
        "summary": summary,
    }


def segment_metrics(galleries: list[dict], labels: list[int], probabilities: list[float], modules: dict) -> dict:
    segments = {
        "detail_ready": [bool(item.get("detail_fetched_at")) for item in galleries],
        "visual_ready": [bool(item.get("visual_embedding")) for item in galleries],
    }
    result = {}
    for name, mask in segments.items():
        for value in (False, True):
            indices = [index for index, selected in enumerate(mask) if selected == value]
            if len(indices) >= 10:
                result[f"{name}={str(value).lower()}"] = metric_set(
                    [labels[index] for index in indices],
                    [probabilities[index] for index in indices],
                    modules,
                )
    return result


def summarize(folds: list[dict]) -> dict:
    model_names = ["legacy", "personalized", "personalized_model", "random", "recency", "site_rating"]
    result = {"fold_count": len(folds), **{name: {} for name in model_names}}
    for model in model_names:
        for metric in (
            "roc_auc", "pr_auc", "ndcg_at_10", "ndcg_at_20", "precision_at_10",
            "precision_at_20", "brier", "ece", "high_confidence_false_positive_rate",
            "low_score_missed_positive_rate",
        ):
            values = []
            for fold in folds:
                metrics = (fold.get("baselines") or {}).get(model) if model in {"random", "recency", "site_rating"} else fold.get(model)
                if metrics and metrics.get(metric) is not None:
                    values.append(float(metrics[metric]))
            result[model][metric] = round(statistics.mean(values), 6) if values else None
            result[model][f"{metric}_ci95"] = confidence_interval(values)
    return result


def confidence_interval(values: list[float]) -> list[float] | None:
    if not values:
        return None
    mean = statistics.mean(values)
    if len(values) == 1:
        return [round(mean, 6), round(mean, 6)]
    margin = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return [round(mean - margin, 6), round(mean + margin, 6)]


def paired_delta_ci(folds: list[dict], model: str, baseline: str, metric: str) -> list[float] | None:
    values = []
    for fold in folds:
        left = fold.get(model) or {}
        right = fold.get(baseline) or (fold.get("baselines") or {}).get(baseline) or {}
        if left.get(metric) is not None and right.get(metric) is not None:
            values.append(float(left[metric]) - float(right[metric]))
    return confidence_interval(values)


def acceptance(summary: dict, folds: list[dict]) -> dict:
    personalized = summary.get("personalized") or {}
    baselines = {
        name: summary.get(name) or {}
        for name in ("legacy", "random", "recency", "site_rating")
    }
    best_ndcg_name = max(
        baselines,
        key=lambda name: float(baselines[name].get("ndcg_at_20") or -1.0),
    )
    best_precision_name = max(
        baselines,
        key=lambda name: float(baselines[name].get("precision_at_10") or -1.0),
    )
    ndcg_delta_ci = paired_delta_ci(folds, "personalized", best_ndcg_name, "ndcg_at_20")
    precision_delta_ci = paired_delta_ci(folds, "personalized", best_precision_name, "precision_at_10")
    calibration_ready = bool(folds) and all(
        bool(((fold.get("model") or {}).get("calibration") or {}).get("ready")) for fold in folds
    )
    checks = {
        "at_least_3_non_overlapping_folds": int(summary.get("fold_count") or 0) >= 3,
        "strict_snapshot_coverage_at_least_0_80": (
            bool(summary.get("strict_snapshots"))
            and float(summary.get("snapshot_coverage") or 0.0) >= 0.80
        ),
        "independent_calibration_ready_in_all_folds": calibration_ready,
        "ndcg_at_20_beats_best_baseline": (
            personalized.get("ndcg_at_20") is not None
            and personalized["ndcg_at_20"] > float(baselines[best_ndcg_name].get("ndcg_at_20") or -1.0)
            and ndcg_delta_ci is not None
            and ndcg_delta_ci[0] >= -0.01
        ),
        "precision_at_10_not_worse_than_best_baseline": (
            precision_delta_ci is not None and precision_delta_ci[0] >= -0.02
        ),
        "ece_at_most_0_10": (
            personalized.get("ece") is not None
            and personalized["ece"] <= 0.10
            and (personalized.get("ece_ci95") or [1.0, 1.0])[1] <= 0.12
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "best_ndcg_baseline": best_ndcg_name,
        "best_precision_baseline": best_precision_name,
        "ndcg_at_20_delta_ci95": ndcg_delta_ci,
        "precision_at_10_delta_ci95": precision_delta_ci,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only temporal evaluation for the local recommender")
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-current-features",
        action="store_true",
        help="diagnostic only: fall back to current gallery features when no historical snapshot exists",
    )
    args = parser.parse_args()
    try:
        report = evaluate(
            args.db.resolve(),
            window=max(20, min(500, args.window)),
            strict_snapshots=not args.allow_current_features,
        )
    except PersonalizedModelUnavailable as exc:
        report = {"database": str(args.db.resolve()), "error": str(exc), "acceptance": {"passed": False}}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not report.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
