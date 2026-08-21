from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any


CLASSIFIER_SCHEMA = "continuing-classifier-v2"
MIN_EXAMPLES = 20
MIN_CLASS_EXAMPLES = 5
MIN_BALANCED_ACCURACY = 0.65
MIN_DECISION_CONFIDENCE = 0.80
MODEL_PATH = Path(
    os.environ.get(
        "EXH_REC_CLASSIFIER_PATH",
        Path(__file__).resolve().parent.parent / "data" / "continuing-classifier.joblib",
    )
)
_CACHE: dict[str, Any] = {}


def _modules() -> dict[str, Any]:
    try:
        import joblib
        import numpy as np
        from scipy import sparse
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import balanced_accuracy_score
        from sklearn.model_selection import StratifiedKFold
    except Exception as exc:
        raise RuntimeError(f"scikit-learn stack unavailable: {exc}") from exc
    return {
        "joblib": joblib,
        "np": np,
        "sparse": sparse,
        "DictVectorizer": DictVectorizer,
        "TfidfVectorizer": TfidfVectorizer,
        "LogisticRegression": LogisticRegression,
        "balanced_accuracy_score": balanced_accuracy_score,
        "StratifiedKFold": StratifiedKFold,
    }


def _signature(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT labels.gallery_url, labels.classification, labels.updated_at, g.title, g.title_jpn,
               g.category, g.page_count, g.parent_url
        FROM (
            SELECT gallery_url, classification, updated_at
            FROM gallery_classification_overrides
            UNION ALL
            SELECT s.gallery_url, s.classification, s.labeled_at AS updated_at
            FROM gallery_classification_samples s
            WHERE s.sampling_strategy = 'uncertainty' AND s.classification IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM gallery_classification_overrides o WHERE o.gallery_url = s.gallery_url
              )
        ) labels
        JOIN galleries g ON g.url = labels.gallery_url
        ORDER BY labels.gallery_url
        """
    ).fetchall()
    evaluation_rows = conn.execute(
        """
        SELECT gallery_url, sampling_strategy, sampling_frame, selection_probability, classification, labeled_at
        FROM gallery_classification_samples
        WHERE classification IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    payload = json.dumps(
        {
            "training": [list(row) for row in rows],
            "sampled": [list(row) for row in evaluation_rows],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _examples(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT g.*, labels.classification
            FROM (
                SELECT gallery_url, classification, updated_at
                FROM gallery_classification_overrides
                UNION ALL
                SELECT s.gallery_url, s.classification, s.labeled_at AS updated_at
                FROM gallery_classification_samples s
                WHERE s.sampling_strategy = 'uncertainty' AND s.classification IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM gallery_classification_overrides o WHERE o.gallery_url = s.gallery_url
                  )
            ) labels
            JOIN galleries g ON g.url = labels.gallery_url
            ORDER BY labels.updated_at, labels.gallery_url
            """
        )
    ]


def _title(item: dict) -> str:
    return " ".join(str(item.get(key) or "") for key in ("title", "title_jpn")).strip()


def _metadata(item: dict) -> dict[str, float]:
    title = _title(item).lower()
    category = str(item.get("category") or "").strip().lower()
    pages = item.get("page_count")
    result: dict[str, float] = {
        "bias": 1.0,
        "has_parent": 1.0 if item.get("parent_url") else 0.0,
        "page_count_known": 1.0 if pages is not None else 0.0,
        "log_pages": 0.0 if pages is None else min(1.0, math.log1p(max(0, int(pages))) / 7.0),
        "has_date": 1.0 if any(char.isdigit() for char in title) else 0.0,
    }
    if category:
        result[f"category={category}"] = 1.0
    for source in ("pixiv", "fanbox", "patreon", "fantia", "twitter", "x.com"):
        if source in title:
            result[f"source={source}"] = 1.0
    for signal in ("archive", "collection", "imageset", "image set", "ongoing", "monthly"):
        if signal in title:
            result[f"signal={signal}"] = 1.0
    return result


def _fit_matrix(items: list[dict], modules: dict[str, Any]) -> tuple[Any, dict]:
    dictionary = modules["DictVectorizer"](sparse=True)
    metadata = dictionary.fit_transform([_metadata(item) for item in items])
    titles = modules["TfidfVectorizer"](
        analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=2048, sublinear_tf=True
    )
    try:
        title_matrix = titles.fit_transform([_title(item) for item in items])
    except ValueError:
        titles = None
        title_matrix = modules["sparse"].csr_matrix((len(items), 0))
    matrix = modules["sparse"].hstack([metadata, title_matrix], format="csr")
    return matrix, {"dictionary": dictionary, "titles": titles}


def _transform(items: list[dict], artifact: dict, modules: dict[str, Any]):
    metadata = artifact["dictionary"].transform([_metadata(item) for item in items])
    titles = artifact.get("titles")
    title_matrix = (
        modules["sparse"].csr_matrix((len(items), 0))
        if titles is None
        else titles.transform([_title(item) for item in items])
    )
    return modules["sparse"].hstack([metadata, title_matrix], format="csr")


def train_continuing_classifier(conn: sqlite3.Connection, persist: bool = True) -> dict:
    signature = _signature(conn)
    items = _examples(conn)
    updates = sum(item["classification"] == "updates" for item in items)
    reviews = len(items) - updates
    base = {
        "schema": CLASSIFIER_SCHEMA,
        "signature": signature,
        "sample_count": len(items),
        "updates_count": updates,
        "review_count": reviews,
    }
    if len(items) < MIN_EXAMPLES or min(updates, reviews) < MIN_CLASS_EXAMPLES:
        status = {**base, "ready": False, "accepted": False, "status": "insufficient-data"}
        _CACHE.clear()
        _CACHE[signature] = status
        return status
    try:
        modules = _modules()
        labels = modules["np"].asarray([1 if item["classification"] == "updates" else 0 for item in items])
        folds = min(4, updates, reviews)
        validation = []
        splitter = modules["StratifiedKFold"](n_splits=folds, shuffle=True, random_state=20260812)
        indices = modules["np"].arange(len(items))
        for train_index, test_index in splitter.split(indices, labels):
            train_items = [items[int(index)] for index in train_index]
            test_items = [items[int(index)] for index in test_index]
            train_matrix, fold_vectorizers = _fit_matrix(train_items, modules)
            test_matrix = _transform(test_items, fold_vectorizers, modules)
            model = modules["LogisticRegression"](
                C=0.5, class_weight="balanced", solver="liblinear", max_iter=400, random_state=20260812
            )
            model.fit(train_matrix, labels[train_index])
            predicted = model.predict(test_matrix)
            validation.append(float(modules["balanced_accuracy_score"](labels[test_index], predicted)))
        mean_accuracy = sum(validation) / len(validation)
        matrix, vectorizers = _fit_matrix(items, modules)
        model = modules["LogisticRegression"](
            C=0.5, class_weight="balanced", solver="liblinear", max_iter=400, random_state=20260812
        )
        model.fit(matrix, labels)
        unbiased = _unbiased_evaluation(conn, model, vectorizers, modules)
        accepted = (
            mean_accuracy >= MIN_BALANCED_ACCURACY
            and unbiased.get("ready", False)
            and float(unbiased.get("balanced_accuracy") or 0.0) >= MIN_BALANCED_ACCURACY
        )
        artifact = {
            **base,
            **vectorizers,
            "model": model,
            "ready": True,
            "accepted": accepted,
            "status": "ready" if accepted else "shadow",
            "balanced_accuracy": round(mean_accuracy, 6),
            "unbiased_evaluation": unbiased,
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        }
        _CACHE.clear()
        _CACHE[signature] = artifact
        if persist and _is_file_database(conn):
            _atomic_dump(artifact, modules)
        return artifact
    except Exception as exc:
        status = {**base, "ready": False, "accepted": False, "status": "failed", "error": str(exc)}
        _CACHE.clear()
        _CACHE[signature] = status
        return status


def _is_file_database(conn: sqlite3.Connection) -> bool:
    return any(str(row[2] or "") for row in conn.execute("PRAGMA database_list") if row[1] == "main")


def _atomic_dump(artifact: dict, modules: dict[str, Any]) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="continuing-", suffix=".joblib", dir=MODEL_PATH.parent)
    os.close(handle)
    try:
        modules["joblib"].dump(artifact, temporary, compress=3)
        os.replace(temporary, MODEL_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_continuing_classifier(conn: sqlite3.Connection) -> dict:
    signature = _signature(conn)
    if signature in _CACHE:
        return _CACHE[signature]
    if _is_file_database(conn) and MODEL_PATH.exists():
        try:
            artifact = _modules()["joblib"].load(MODEL_PATH)
            if artifact.get("schema") == CLASSIFIER_SCHEMA and artifact.get("signature") == signature:
                _CACHE[signature] = artifact
                return artifact
        except Exception:
            pass
    return train_continuing_classifier(conn)


def continuing_classifier_decisions(conn: sqlite3.Connection, items: list[dict]) -> tuple[dict[str, dict], dict]:
    artifact = load_continuing_classifier(conn)
    if not artifact.get("ready") or not artifact.get("accepted") or not items:
        return {}, public_classifier_status(artifact)
    modules = _modules()
    matrix = _transform(items, artifact, modules)
    probabilities = artifact["model"].predict_proba(matrix)[:, 1]
    decisions: dict[str, dict] = {}
    for item, probability in zip(items, probabilities):
        probability = float(probability)
        confidence = max(probability, 1.0 - probability)
        if confidence < MIN_DECISION_CONFIDENCE:
            continue
        decisions[str(item.get("url") or "")] = {
            "classification": "updates" if probability >= 0.5 else "review",
            "probability": round(probability, 6),
            "confidence": round(confidence, 6),
            "model_version": f"{CLASSIFIER_SCHEMA}-{artifact['signature']}",
        }
    return decisions, public_classifier_status(artifact)


def public_classifier_status(artifact: dict) -> dict:
    return {
        key: artifact.get(key)
        for key in (
            "ready", "accepted", "status", "sample_count", "updates_count", "review_count",
            "balanced_accuracy", "trained_at", "error",
            "unbiased_evaluation",
        )
        if key in artifact
    }


def sample_continuing_classifier_candidates(
    conn: sqlite3.Connection,
    limit: int = 20,
    random_fraction: float = 0.30,
    seed: int | None = None,
) -> list[dict]:
    limit = max(1, min(200, int(limit)))
    random_fraction = max(0.1, min(0.9, float(random_fraction)))
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT g.*
            FROM galleries g
            WHERE NOT EXISTS (
                SELECT 1 FROM gallery_classification_overrides o WHERE o.gallery_url = g.url
            )
              AND NOT EXISTS (
                SELECT 1 FROM gallery_classification_samples s WHERE s.gallery_url = g.url
              )
            ORDER BY g.last_seen_at DESC
            """
        )
    ]
    if not rows:
        return []
    artifact = load_continuing_classifier(conn)
    probabilities = [0.5] * len(rows)
    if artifact.get("ready"):
        modules = _modules()
        probabilities = [float(value) for value in artifact["model"].predict_proba(
            _transform(rows, artifact, modules)
        )[:, 1]]
    random_count = min(len(rows), max(1, round(limit * random_fraction)))
    rng = random.Random(seed if seed is not None else int(time.time() // 86400))
    random_indices = set(rng.sample(range(len(rows)), random_count))
    remaining = [index for index in range(len(rows)) if index not in random_indices]
    uncertainty_count = min(limit - random_count, len(remaining))
    uncertainty_indices = sorted(remaining, key=lambda index: abs(probabilities[index] - 0.5))[:uncertainty_count]
    selected = [
        (index, "random", random_count / len(rows)) for index in sorted(random_indices)
    ] + [
        (index, "uncertainty", uncertainty_count / max(1, len(remaining)))
        for index in uncertainty_indices
    ]
    result = []
    for index, strategy, selection_probability in selected:
        cursor = conn.execute(
            """
            INSERT INTO gallery_classification_samples(
                gallery_url, sampling_strategy, sampling_frame,
                selection_probability, model_probability
            ) VALUES (?, ?, 'full-library', ?, ?)
            """,
            (rows[index]["url"], strategy, selection_probability, probabilities[index]),
        )
        result.append(
            {
                "id": int(cursor.lastrowid),
                "gallery_url": rows[index]["url"],
                "title": rows[index].get("title"),
                "sampling_strategy": strategy,
                "sampling_frame": "full-library",
                "selection_probability": round(selection_probability, 8),
                "model_probability": round(probabilities[index], 6),
            }
        )
    return result


def label_continuing_classifier_sample(
    conn: sqlite3.Connection,
    sample_id: int,
    classification: str,
) -> dict:
    classification = str(classification or "").strip().lower()
    if classification not in {"review", "updates"}:
        raise ValueError("classification must be review or updates")
    cursor = conn.execute(
        """
        UPDATE gallery_classification_samples
        SET classification = ?, labeled_at = CURRENT_TIMESTAMP
        WHERE id = ? AND classification IS NULL
        """,
        (classification, int(sample_id)),
    )
    if not cursor.rowcount:
        raise ValueError("classification sample not found or already labeled")
    row = conn.execute(
        "SELECT * FROM gallery_classification_samples WHERE id = ?",
        (int(sample_id),),
    ).fetchone()
    train_continuing_classifier(conn)
    return dict(row)


def _unbiased_evaluation(conn, model, vectorizers: dict, modules: dict[str, Any]) -> dict:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT g.*, s.classification, s.selection_probability
            FROM gallery_classification_samples s
            JOIN galleries g ON g.url = s.gallery_url
            WHERE s.sampling_strategy = 'random'
              AND s.sampling_frame = 'full-library'
              AND s.classification IS NOT NULL
            ORDER BY s.labeled_at, s.id
            """
        )
    ]
    updates = sum(row["classification"] == "updates" for row in rows)
    reviews = len(rows) - updates
    base = {"sample_count": len(rows), "updates_count": updates, "review_count": reviews}
    if len(rows) < MIN_EXAMPLES or min(updates, reviews) < MIN_CLASS_EXAMPLES:
        return {**base, "ready": False, "status": "insufficient-data"}
    labels = modules["np"].asarray([1 if row["classification"] == "updates" else 0 for row in rows])
    predicted = model.predict(_transform(rows, vectorizers, modules))
    weights = modules["np"].asarray([
        1.0 / max(1e-9, float(row["selection_probability"])) for row in rows
    ])
    class_scores = []
    for value in (0, 1):
        mask = labels == value
        class_scores.append(float(weights[mask & (predicted == labels)].sum() / weights[mask].sum()))
    return {
        **base,
        "ready": True,
        "status": "ready",
        "balanced_accuracy": round(sum(class_scores) / 2.0, 6),
        "selection": "inverse-probability-weighted random exploration holdout",
    }
