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
    MODEL_SCHEMA,
    PersonalizedModelUnavailable,
    score_personalized_galleries,
    sklearn_modules,
    train_personalized_model,
)
from exh_rec.recommender import (  # noqa: E402
    parse_visual_embedding,
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


def gallery_payload(conn: sqlite3.Connection, gallery_url: str) -> dict:
    row = conn.execute("SELECT * FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    gallery = dict(row)
    gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
    gallery["tag_weights"] = json.loads(gallery.pop("tag_weights_json") or "{}")
    gallery["visual_embedding"] = parse_visual_embedding(gallery.get("visual_embedding_json"))
    return gallery


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
    conn.execute("DELETE FROM gallery_marks")
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


def evaluate(db_path: Path, window: int = 100) -> dict:
    modules = sklearn_modules()
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    feedback = latest_feedback(source)
    starts = sorted({max(50, int(len(feedback) * fraction)) for fraction in (0.45, 0.55, 0.65, 0.75, 0.85)})
    folds = []
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
        artifact = train_personalized_model(conn, persist=False)

        bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
        weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
        visual = visual_preference_model(conn)
        strengths = tag_corpus_strengths(conn)
        galleries = [gallery_payload(conn, row["gallery_url"]) for row in test]
        labels = [1 if float(row["vote"]) > 0 else 0 for row in test]
        legacy = []
        legacy_scores = []
        for gallery in galleries:
            score, _ = score_gallery(gallery, bootstrap, weights, visual_model=visual, tag_strengths=strengths)
            legacy_scores.append(score)
            legacy.append(1.0 / (1.0 + math.exp(-max(-12.0, min(12.0, score)))))
        predictions, _ = score_personalized_galleries(conn, galleries)
        personalized = [float(item["like_probability"]) for item in predictions]
        fold = {
            "train_count": start,
            "test_count": len(test),
            "test_start": test[0]["created_at"],
            "test_end": test[-1]["created_at"],
            "legacy": metric_set(labels, legacy, modules, ranking_scores=legacy_scores),
            "personalized": metric_set(labels, personalized, modules) if personalized else None,
            "model": {key: artifact.get(key) for key in ("status", "selected_c", "sample_count")},
            "segments": segment_metrics(galleries, labels, personalized, modules) if personalized else {},
        }
        folds.append(fold)
        conn.close()
    source.close()
    summary = summarize(folds)
    summary["acceptance"] = acceptance(summary)
    return {"model_schema": MODEL_SCHEMA, "database": str(db_path), "folds": folds, "summary": summary}


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
    result = {"fold_count": len(folds), "legacy": {}, "personalized": {}}
    for model in ("legacy", "personalized"):
        for metric in (
            "roc_auc", "pr_auc", "ndcg_at_10", "ndcg_at_20", "precision_at_10",
            "precision_at_20", "brier", "ece", "high_confidence_false_positive_rate",
            "low_score_missed_positive_rate",
        ):
            values = [fold[model][metric] for fold in folds if fold.get(model) and fold[model].get(metric) is not None]
            result[model][metric] = round(statistics.mean(values), 6) if values else None
    return result


def acceptance(summary: dict) -> dict:
    legacy = summary.get("legacy") or {}
    personalized = summary.get("personalized") or {}
    checks = {
        "roc_auc_gain_at_least_0_05": (
            personalized.get("roc_auc") is not None
            and legacy.get("roc_auc") is not None
            and personalized["roc_auc"] - legacy["roc_auc"] >= 0.05
        ),
        "ndcg_at_20_not_lower": (
            personalized.get("ndcg_at_20") is not None
            and legacy.get("ndcg_at_20") is not None
            and personalized["ndcg_at_20"] >= legacy["ndcg_at_20"]
        ),
        "precision_at_10_not_lower": (
            personalized.get("precision_at_10") is not None
            and legacy.get("precision_at_10") is not None
            and personalized["precision_at_10"] >= legacy["precision_at_10"]
        ),
        "ece_at_most_0_10": personalized.get("ece") is not None and personalized["ece"] <= 0.10,
    }
    return {"passed": all(checks.values()), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only temporal evaluation for the local recommender")
    parser.add_argument("--db", type=Path, default=db.DB_PATH)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(args.db.resolve(), window=max(20, min(500, args.window)))
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
