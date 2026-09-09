from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_SCHEMA = "personalized-content-v4"
FEATURE_SCHEMA = "content-features-v6"
MIN_LABELED = 50
MIN_CLASS = 15
MIN_CALIBRATION_SAMPLES = 20
CALIBRATION_HOLDOUT_FRACTION = 0.20
BOOTSTRAP_MODELS = 8
CALIBRATION_RECENCY_DECAY = 3.0
CALIBRATION_C = 0.2
LOW_INTEREST_MAX_POSITIVE_RATE = 0.10
LOW_INTEREST_MAX_POSITIVE_LOSS_RATE = 0.05
LOW_INTEREST_MAX_THRESHOLD = 0.50
LOW_INTEREST_MIN_OOF_SAMPLES = 80
LOW_INTEREST_MIN_TRIAGED_SAMPLES = 30
LOW_INTEREST_WILSON_Z = 1.96
NEGATIVE_REASON_CODES = {
    "visual_style",
    "content_tags",
    "creator_character",
    "quality",
    "gallery_too_small",
    "too_few_relevant_images",
    "duplicate_update",
    "other",
}
MODEL_PATH = Path(
    os.environ.get(
        "EXH_REC_MODEL_PATH",
        Path(__file__).resolve().parent.parent / "data" / "personalized-model.joblib",
    )
)
EVALUATION_PATH = Path(
    os.environ.get(
        "EXH_REC_EVALUATION_PATH",
        Path(__file__).resolve().parent.parent / "data" / "recommendation-evaluation.json",
    )
)
_MODEL_CACHE: dict[str, Any] = {}
_MODEL_LOCK = threading.RLock()


class PersonalizedModelUnavailable(RuntimeError):
    pass


def sklearn_modules() -> dict[str, Any]:
    try:
        import joblib
        import numpy as np
        from scipy import sparse
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score, brier_score_loss, ndcg_score, roc_auc_score
    except Exception as exc:
        raise PersonalizedModelUnavailable(f"scikit-learn stack unavailable: {exc}") from exc
    return {
        "joblib": joblib,
        "np": np,
        "sparse": sparse,
        "DictVectorizer": DictVectorizer,
        "TfidfVectorizer": TfidfVectorizer,
        "LogisticRegression": LogisticRegression,
        "roc_auc_score": roc_auc_score,
        "average_precision_score": average_precision_score,
        "brier_score_loss": brier_score_loss,
        "ndcg_score": ndcg_score,
    }


def normalize_reason_code(value: object) -> str | None:
    reason = str(value or "").strip().lower()
    if not reason:
        return None
    if reason not in NEGATIVE_REASON_CODES:
        raise ValueError("unsupported feedback reason_code")
    return reason


def normalize_surface(value: object, default: str = "review") -> str:
    surface = str(value or default).strip().lower().replace("_", "-")
    if surface not in {
        "review", "low-interest", "audit", "discovery", "updates", "preview",
        "history", "favorite", "ban", "api",
    }:
        return default
    return surface


def model_data_signature(conn: sqlite3.Connection, examples: list[dict] | None = None) -> str:
    examples = training_examples(conn) if examples is None else examples
    keys = (
        "url", "label", "sample_weight", "label_source", "feedback_at", "title", "title_jpn",
        "category", "uploader", "rating", "page_count", "tags", "tag_weights", "detail_fetched_at",
        "visual_embedding_digest", "visual_embedding_length", "visual_embedding_version", "visual_image_count",
        "visual_image_cohesion", "visual_image_min_similarity",
        "feature_snapshot_id", "feature_snapshot_at",
    )
    compact_examples = []
    for item in examples:
        raw_embedding = str(item.get("visual_embedding_json") or "")
        compact = {key: item.get(key) for key in keys}
        compact["visual_embedding_digest"] = item.get("visual_embedding_digest") or (
            hashlib.sha256(raw_embedding.encode("utf-8")).hexdigest() if raw_embedding else None
        )
        compact["visual_embedding_length"] = len(raw_embedding)
        compact_examples.append(compact)
    payload = {
        "feature_schema": FEATURE_SCHEMA,
        "examples": compact_examples,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _decoded_gallery(row: sqlite3.Row | dict) -> dict:
    gallery = dict(row)
    for source, target, empty in (
        ("tags_json", "tags", []),
        ("tag_weights_json", "tag_weights", {}),
    ):
        raw = gallery.pop(source, None)
        if target in gallery:
            continue
        try:
            parsed = json.loads(raw or ("[]" if isinstance(empty, list) else "{}"))
        except (TypeError, json.JSONDecodeError):
            parsed = empty
        gallery[target] = parsed if isinstance(parsed, type(empty)) else empty
    return gallery


def gallery_feature_snapshot(
    conn: sqlite3.Connection,
    gallery_url: str,
    at: str,
) -> dict | None:
    row = conn.execute(
        """
        SELECT id AS feature_snapshot_id, captured_at AS feature_snapshot_at,
               title, title_jpn, category, uploader, rating, tags_json,
               tag_weights_json, page_count, detail_fetched_at,
               visual_embedding_json, visual_embedding_digest,
               visual_embedding_version, visual_embedding_at
        FROM gallery_feature_snapshots
        WHERE gallery_url = ?
          AND julianday(captured_at) IS NOT NULL
          AND julianday(captured_at) <= julianday(?)
        ORDER BY captured_at DESC, id DESC
        LIMIT 1
        """,
        (gallery_url, at),
    ).fetchone()
    return dict(row) if row else None


def gallery_feature_snapshot_map(
    conn: sqlite3.Connection,
    targets: list[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    unique_targets = list(dict.fromkeys((str(url), str(at)) for url, at in targets if url and at))
    snapshots: dict[tuple[str, str], dict] = {}
    for offset in range(0, len(unique_targets), 250):
        chunk = unique_targets[offset : offset + 250]
        values = ",".join("(?, ?, ?)" for _ in chunk)
        params: list[object] = []
        for index, (gallery_url, at) in enumerate(chunk):
            params.extend((index, gallery_url, at))
        rows = conn.execute(
            f"""
            WITH targets(target_index, gallery_url, target_at) AS (VALUES {values})
            SELECT t.target_index,
                   s.id AS feature_snapshot_id, s.captured_at AS feature_snapshot_at,
                   s.title, s.title_jpn, s.category, s.uploader, s.rating, s.tags_json,
                   s.tag_weights_json, s.page_count, s.detail_fetched_at,
                   s.visual_embedding_json, s.visual_embedding_digest,
                   s.visual_embedding_version, s.visual_embedding_at
            FROM targets t
            JOIN gallery_feature_snapshots s ON s.id = (
                SELECT candidate.id
                FROM gallery_feature_snapshots candidate
                WHERE candidate.gallery_url = t.gallery_url
                  AND julianday(candidate.captured_at) IS NOT NULL
                  AND julianday(candidate.captured_at) <= julianday(t.target_at)
                ORDER BY candidate.captured_at DESC, candidate.id DESC
                LIMIT 1
            )
            """,
            params,
        ).fetchall()
        for row in rows:
            key = chunk[int(row["target_index"])]
            snapshot = dict(row)
            snapshot.pop("target_index", None)
            snapshots[key] = snapshot
    return snapshots


def _apply_feature_snapshot(
    conn: sqlite3.Connection,
    item: dict,
    at: str,
    strict: bool,
    snapshots: dict[tuple[str, str], dict] | None = None,
) -> dict | None:
    key = (str(item.get("url") or ""), str(at or ""))
    snapshot = snapshots.get(key) if snapshots is not None else gallery_feature_snapshot(conn, *key)
    if snapshot is None:
        return None if strict else item
    item.pop("tags", None)
    item.pop("tag_weights", None)
    item.update(snapshot)
    return _decoded_gallery(item)


def _attach_visual_image_features(conn: sqlite3.Connection, galleries: list[dict]) -> None:
    targets = {str(item.get("url") or ""): item for item in galleries if item.get("url")}
    if not targets:
        return
    grouped: dict[str, list[list[float]]] = {}
    urls = list(targets)
    for offset in range(0, len(urls), 500):
        chunk = urls[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT gallery_url, embedding_json, embedding_version, created_at
            FROM gallery_visual_images
            WHERE gallery_url IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            gallery = targets.get(str(row["gallery_url"]))
            if gallery is None:
                continue
            expected_version = str(gallery.get("visual_embedding_version") or "")
            if expected_version and str(row["embedding_version"] or "") != expected_version:
                continue
            snapshot_at = str(gallery.get("feature_snapshot_at") or "")
            if snapshot_at and str(row["created_at"] or "") > snapshot_at:
                continue
            try:
                vector = [float(value) for value in json.loads(row["embedding_json"] or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if vector and all(math.isfinite(value) for value in vector):
                grouped.setdefault(str(row["gallery_url"]), []).append(vector)
    for gallery_url, vectors in grouped.items():
        dims = len(vectors[0])
        vectors = [vector for vector in vectors if len(vector) == dims]
        if not vectors:
            continue
        centroid = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dims)]
        norm = math.sqrt(sum(value * value for value in centroid))
        if norm <= 0:
            continue
        centroid = [value / norm for value in centroid]
        similarities = [
            max(-1.0, min(1.0, sum(value * centroid[index] for index, value in enumerate(vector))))
            for vector in vectors
        ]
        targets[gallery_url].update(
            {
                "visual_image_count": len(vectors),
                "visual_image_cohesion": sum(similarities) / len(similarities),
                "visual_image_min_similarity": min(similarities),
            }
        )


def training_examples(conn: sqlite3.Connection, strict_temporal: bool = False) -> list[dict]:
    feedback = conn.execute(
        """
        SELECT g.*, f.id AS feedback_id, f.vote, f.score, f.reason_code, f.surface,
               f.created_at AS feedback_at, NULL AS mark_kind
        FROM feedback f
        JOIN galleries g ON g.url = f.gallery_url
        JOIN (SELECT gallery_url, MAX(id) AS id FROM feedback GROUP BY gallery_url) latest
          ON latest.id = f.id
        """
    ).fetchall()
    download_weight = hath_download_signal_weight(conn)
    downloads = []
    if download_weight > 0:
        downloads = conn.execute(
            """
            SELECT g.*, NULL AS feedback_id, NULL AS vote, NULL AS score, NULL AS reason_code,
                   'hath' AS surface, MAX(d.completed_at) AS feedback_at, 'hath-download' AS mark_kind
            FROM hath_downloads d
            JOIN galleries g ON g.url = d.gallery_url
            WHERE d.status = 'completed'
              AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.gallery_url = d.gallery_url)
              AND NOT EXISTS (SELECT 1 FROM gallery_marks m WHERE m.gallery_url = d.gallery_url)
            GROUP BY d.gallery_url
            """
        ).fetchall()
    marks = conn.execute(
        """
        SELECT g.*, NULL AS feedback_id, NULL AS vote, NULL AS score, NULL AS reason_code,
               'api' AS surface, m.updated_at AS feedback_at, m.kind AS mark_kind
        FROM gallery_marks m JOIN galleries g ON g.url = m.gallery_url
        """
    ).fetchall()
    snapshots = gallery_feature_snapshot_map(
        conn,
        [
            (str(row["url"] or ""), str(row["feedback_at"] or ""))
            for row in [*feedback, *downloads, *marks]
        ],
    )
    examples: dict[str, dict] = {}
    for row in feedback:
        item = _decoded_gallery(row)
        item = _apply_feature_snapshot(
            conn,
            item,
            str(item.get("feedback_at") or ""),
            strict_temporal,
            snapshots,
        )
        if item is None:
            continue
        if item.get("reason_code") == "duplicate_update":
            continue
        vote = float(item.get("vote") or 0)
        if vote == 0:
            continue
        item["label"] = 1 if vote > 0 else 0
        item["sample_weight"] = max(0.75, min(2.0, abs(vote)))
        item["label_source"] = "score" if item.get("score") is not None else "vote"
        examples[item["url"]] = item
    if download_weight > 0:
        for row in downloads:
            item = _decoded_gallery(row)
            item = _apply_feature_snapshot(
                conn,
                item,
                str(item.get("feedback_at") or ""),
                strict_temporal,
                snapshots,
            )
            if item is None:
                continue
            item["label"] = 1
            item["sample_weight"] = download_weight
            item["label_source"] = "hath-download"
            examples[item["url"]] = item
    for row in marks:
        item = _decoded_gallery(row)
        item = _apply_feature_snapshot(
            conn,
            item,
            str(item.get("feedback_at") or ""),
            strict_temporal,
            snapshots,
        )
        if item is None:
            continue
        item["label"] = 1 if item["mark_kind"] == "favorite" else 0
        item["sample_weight"] = 3.0
        item["label_source"] = item["mark_kind"]
        examples[item["url"]] = item
    ordered = sorted(examples.values(), key=lambda item: (str(item.get("feedback_at") or ""), item["url"]))
    _attach_visual_image_features(conn, ordered)
    return ordered


def hath_download_signal_weight(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key = 'hath_download_signal_weight'").fetchone()
    try:
        value = float(row[0]) if row else 1.25
    except (TypeError, ValueError, OverflowError):
        value = 1.25
    if not math.isfinite(value):
        value = 1.25
    return max(0.0, min(2.0, value))


def _metadata_features(gallery: dict) -> dict[str, float]:
    result: dict[str, float] = {"bias": 1.0}
    category = str(gallery.get("category") or "").strip().lower()
    uploader = str(gallery.get("uploader") or "").strip().lower()
    if category:
        result[f"category={category}"] = 1.0
    if uploader:
        result[f"uploader={uploader}"] = 1.0
    for raw in gallery.get("tags") or []:
        tag = str(raw or "").strip().lower()
        if tag:
            result[f"tag={tag}"] = 1.0
    return result


def _numeric_features(gallery: dict) -> dict[str, float]:
    rating = gallery.get("rating")
    pages = gallery.get("page_count")
    tags = gallery.get("tags") or []
    visual = gallery.get("visual_embedding")
    if visual is None:
        try:
            visual = json.loads(gallery.get("visual_embedding_json") or "null")
        except (TypeError, json.JSONDecodeError):
            visual = None
    page_value = 0.0 if pages is None else min(1.0, math.log1p(max(0, int(pages))) / 7.0)
    image_count = max(0, int(gallery.get("visual_image_count") or 0))
    image_cohesion = float(gallery.get("visual_image_cohesion") or 1.0)
    image_min_similarity = float(gallery.get("visual_image_min_similarity") or 1.0)
    return {
        "site_rating": 0.0 if rating is None else (float(rating) - 3.5) / 1.5,
        "site_rating_missing": 1.0 if rating is None else 0.0,
        "log_pages": page_value,
        "short_gallery": 1.0 - page_value,
        "page_count_missing": 1.0 if pages is None else 0.0,
        "detail_ready": 1.0 if gallery.get("detail_fetched_at") else 0.0,
        "tag_coverage": min(1.0, len(tags) / 50.0),
        "visual_ready": 1.0 if isinstance(visual, list) and visual else 0.0,
        "visual_image_stats_ready": 1.0 if image_count else 0.0,
        "visual_image_count": min(1.0, math.log1p(image_count) / math.log(13.0)),
        "visual_image_diversity": min(1.0, max(0.0, (1.0 - image_cohesion) * 4.0)),
        "visual_image_outlier": min(1.0, max(0.0, (1.0 - image_min_similarity) * 2.0)),
    }


def _title(gallery: dict) -> str:
    return " ".join(str(gallery.get(key) or "") for key in ("title", "title_jpn")).strip()


def _visual(gallery: dict, dims: int) -> list[float]:
    raw = gallery.get("visual_embedding")
    if raw is None:
        try:
            raw = json.loads(gallery.get("visual_embedding_json") or "null")
        except (TypeError, json.JSONDecodeError):
            raw = None
    if not isinstance(raw, list) or len(raw) != dims:
        return [0.0] * dims
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return [0.0] * dims


def _active_visual_dims(examples: list[dict]) -> int:
    counts: dict[int, int] = {}
    for item in examples:
        try:
            values = json.loads(item.get("visual_embedding_json") or "null")
        except (TypeError, json.JSONDecodeError):
            values = item.get("visual_embedding")
        if isinstance(values, list) and values:
            counts[len(values)] = counts.get(len(values), 0) + 1
    return max(counts, key=counts.get) if counts else 0


def _center_visual_matrix(visual, modules: dict[str, Any], mean=None):
    np = modules["np"]
    if visual.shape[1] == 0:
        return visual, []
    present = np.linalg.norm(visual, axis=1) > 0
    if mean is None:
        mean_array = visual[present].mean(axis=0) if int(present.sum()) else np.zeros(visual.shape[1])
    else:
        mean_array = np.asarray(mean, dtype="float32")
    centered = visual.copy()
    indices = np.flatnonzero(present)
    values = centered[indices] - mean_array
    norms = np.linalg.norm(values, axis=1)
    nonzero = norms > 0
    values[nonzero] /= norms[nonzero, None]
    centered[indices] = values
    centered[~present] = 0.0
    return centered, [float(value) for value in mean_array]


def _fit_vectorizers(examples: list[dict], modules: dict[str, Any]) -> dict:
    dict_vectorizer = modules["DictVectorizer"](sparse=True)
    metadata = dict_vectorizer.fit_transform([_metadata_features(item) for item in examples])
    title_vectorizer = modules["TfidfVectorizer"](
        analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=4096, sublinear_tf=True
    )
    try:
        titles = title_vectorizer.fit_transform([_title(item) for item in examples])
    except ValueError:
        title_vectorizer = None
        titles = modules["sparse"].csr_matrix((len(examples), 0))
    numeric_names = sorted(_numeric_features(examples[0]))
    numeric = modules["np"].asarray(
        [[_numeric_features(item)[name] for name in numeric_names] for item in examples], dtype="float32"
    )
    visual_dims = _active_visual_dims(examples)
    visual = modules["np"].asarray([_visual(item, visual_dims) for item in examples], dtype="float32")
    visual, visual_mean = _center_visual_matrix(visual, modules)
    matrix = modules["sparse"].hstack(
        [metadata, titles, modules["sparse"].csr_matrix(numeric), modules["sparse"].csr_matrix(visual)],
        format="csr",
    )
    metadata_support: dict[str, int] = {}
    for item in examples:
        for name in _metadata_features(item):
            metadata_support[name] = metadata_support.get(name, 0) + 1
    return {
        "dict_vectorizer": dict_vectorizer,
        "title_vectorizer": title_vectorizer,
        "numeric_names": numeric_names,
        "visual_dims": visual_dims,
        "visual_mean": visual_mean,
        "metadata_support": metadata_support,
        "matrix": matrix,
    }


def _transform(examples: list[dict], artifact: dict, modules: dict[str, Any]):
    metadata = artifact["dict_vectorizer"].transform([_metadata_features(item) for item in examples])
    if artifact["title_vectorizer"] is None:
        titles = modules["sparse"].csr_matrix((len(examples), 0))
    else:
        titles = artifact["title_vectorizer"].transform([_title(item) for item in examples])
    numeric = modules["np"].asarray(
        [[_numeric_features(item)[name] for name in artifact["numeric_names"]] for item in examples], dtype="float32"
    )
    visual = modules["np"].asarray(
        [_visual(item, artifact["visual_dims"]) for item in examples], dtype="float32"
    )
    visual, _mean = _center_visual_matrix(visual, modules, artifact.get("visual_mean") or None)
    return modules["sparse"].hstack(
        [metadata, titles, modules["sparse"].csr_matrix(numeric), modules["sparse"].csr_matrix(visual)],
        format="csr",
    )


def _classifier(modules: dict[str, Any], c_value: float):
    return modules["LogisticRegression"](
        C=c_value,
        class_weight="balanced",
        solver="liblinear",
        max_iter=500,
        random_state=1701,
    )


def _rolling_splits(count: int) -> list[tuple[int, int]]:
    if count <= MIN_CLASS * 2:
        return []
    minimum_train = (
        MIN_CLASS * 2
        if count <= MIN_LABELED
        else min(MIN_LABELED, max(MIN_CLASS * 2, count // 2))
    )
    window = max(10, min(100, count // 5))
    starts = sorted({max(minimum_train, int(count * fraction)) for fraction in (0.5, 0.65, 0.8)})
    starts = sorted({*starts, max(minimum_train, count - window)})
    return [(start, min(count, start + window)) for start in starts if start < count]


def _select_c(matrix, labels, weights, modules: dict[str, Any]) -> tuple[float, list[dict]]:
    reports: list[dict] = []
    best_c = 0.2
    best_auc: float | None = None
    for c_value in (0.05, 0.2, 1.0, 4.0):
        aucs = []
        for start, end in _rolling_splits(len(labels)):
            train_labels = labels[:start]
            test_labels = labels[start:end]
            if len(set(train_labels)) < 2 or len(set(test_labels)) < 2:
                continue
            model = _classifier(modules, c_value)
            model.fit(matrix[:start], train_labels, sample_weight=weights[:start])
            probabilities = model.predict_proba(matrix[start:end])[:, 1]
            aucs.append(float(modules["roc_auc_score"](test_labels, probabilities)))
        mean_auc = sum(aucs) / len(aucs) if aucs else None
        reports.append({"c": c_value, "mean_auc": None if mean_auc is None else round(mean_auc, 6), "folds": len(aucs)})
        if mean_auc is not None and (best_auc is None or mean_auc > best_auc):
            best_auc = mean_auc
            best_c = c_value
    return best_c, reports


def _calibration_start(labels) -> int | None:
    count = len(labels)
    start = max(MIN_LABELED, int(count * (1.0 - CALIBRATION_HOLDOUT_FRACTION)))
    if count - start < MIN_CALIBRATION_SAMPLES:
        start = count - MIN_CALIBRATION_SAMPLES
    if start < MIN_CLASS * 2 or len(set(labels[:start])) < 2 or len(set(labels[start:])) < 2:
        return None
    return start


def _fit_calibrator(matrix, labels, weights, c_value: float, modules: dict[str, Any], start: int | None):
    if start is None:
        return None
    model = _classifier(modules, c_value)
    model.fit(matrix[:start], labels[:start], sample_weight=weights[:start])
    logits = [float(value) for value in model.decision_function(matrix[start:])]
    targets = [int(value) for value in labels[start:]]
    calibration_weights = []
    for index, weight in enumerate(weights[start:], start=start):
        relative_position = index / max(1, len(labels) - 1)
        recency_weight = math.exp(CALIBRATION_RECENCY_DECAY * (relative_position - 1.0))
        calibration_weights.append(float(weight) * recency_weight)
    if len(logits) < MIN_CALIBRATION_SAMPLES or len(set(targets)) < 2:
        return None
    calibrator = modules["LogisticRegression"](
        C=CALIBRATION_C,
        solver="liblinear",
        max_iter=300,
        random_state=1702,
    )
    calibrator.fit(
        modules["np"].asarray(logits).reshape(-1, 1),
        targets,
        sample_weight=calibration_weights,
    )
    return calibrator


def _bootstrap_classifiers(matrix, labels, weights, c_value: float, modules: dict[str, Any]) -> list[Any]:
    rng = modules["np"].random.default_rng(20260812)
    models = []
    for index in range(BOOTSTRAP_MODELS):
        sample = rng.integers(0, len(labels), len(labels))
        if len(set(labels[sample])) < 2:
            sample = modules["np"].arange(len(labels))
        model = _classifier(modules, c_value)
        model.set_params(random_state=20260812 + index)
        model.fit(matrix[sample], labels[sample], sample_weight=weights[sample])
        models.append(model)
    return models


def _calibrated(logits, calibrator, modules: dict[str, Any]):
    if calibrator is not None:
        return calibrator.predict_proba(modules["np"].asarray(logits).reshape(-1, 1))[:, 1]
    values = modules["np"].clip(logits, -20, 20)
    return 1.0 / (1.0 + modules["np"].exp(-values))


def _ece(labels, probabilities, modules: dict[str, Any], bins: int = 10) -> float:
    total = len(labels)
    result = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (probabilities >= lower) & (probabilities < upper if index < bins - 1 else probabilities <= upper)
        count = int(mask.sum())
        if count:
            result += count / total * abs(float(labels[mask].mean()) - float(probabilities[mask].mean()))
    return result


def _wilson_upper_bound(positives: int, total: int, z: float = LOW_INTEREST_WILSON_Z) -> float:
    if total <= 0:
        return 1.0
    rate = positives / total
    denominator = 1.0 + z * z / total
    center = rate + z * z / (2.0 * total)
    margin = z * math.sqrt((rate * (1.0 - rate) + z * z / (4.0 * total)) / total)
    return min(1.0, (center + margin) / denominator)


def _temporal_oof_probabilities(
    examples: list[dict],
    selected_c: float,
    modules: dict[str, Any],
) -> tuple[list[dict], list[dict], bool]:
    predictions_by_index: dict[int, dict] = {}
    folds: list[dict] = []
    all_calibrated = True
    for start, end in _rolling_splits(len(examples)):
        train = examples[:start]
        test = examples[start:end]
        train_labels = modules["np"].asarray([int(item["label"]) for item in train], dtype="int8")
        test_labels = modules["np"].asarray([int(item["label"]) for item in test], dtype="int8")
        if len(set(train_labels)) < 2 or not test:
            continue
        fitted = _fit_vectorizers(train, modules)
        train_weights = modules["np"].asarray(
            [float(item["sample_weight"]) for item in train], dtype="float32"
        )
        calibration_start = _calibration_start(train_labels)
        calibrator = _fit_calibrator(
            fitted["matrix"], train_labels, train_weights, selected_c, modules, calibration_start
        )
        calibration_ready = calibrator is not None
        all_calibrated = all_calibrated and calibration_ready
        classifier = _classifier(modules, selected_c)
        classifier.fit(fitted["matrix"], train_labels, sample_weight=train_weights)
        test_matrix = _transform(test, fitted, modules)
        probabilities = _calibrated(classifier.decision_function(test_matrix), calibrator, modules)
        for relative_index, probability in enumerate(probabilities):
            absolute_index = start + relative_index
            predictions_by_index.setdefault(
                absolute_index,
                {
                    "index": absolute_index,
                    "probability": round(float(probability), 6),
                    "label": int(test_labels[relative_index]),
                },
            )
        folds.append(
            {
                "train_count": start,
                "test_count": end - start,
                "calibration_ready": calibration_ready,
            }
        )
    return list(predictions_by_index.values()), folds, bool(folds) and all_calibrated


def learn_low_interest_threshold(
    examples: list[dict],
    selected_c: float,
    modules: dict[str, Any],
    model_version: str,
    generated_at: str,
) -> dict:
    predictions, folds, calibration_ready = _temporal_oof_probabilities(
        examples, selected_c, modules
    )
    targets = {
        "max_positive_rate": LOW_INTEREST_MAX_POSITIVE_RATE,
        "max_positive_loss_rate": LOW_INTEREST_MAX_POSITIVE_LOSS_RATE,
        "positive_rate_upper_95": LOW_INTEREST_MAX_POSITIVE_RATE,
        "min_oof_samples": LOW_INTEREST_MIN_OOF_SAMPLES,
        "min_triaged_samples": LOW_INTEREST_MIN_TRIAGED_SAMPLES,
        "max_threshold": LOW_INTEREST_MAX_THRESHOLD,
    }
    base = {
        "method": "temporal-oof",
        "model_version": model_version,
        "generated_at": generated_at,
        "fold_count": len(folds),
        "sample_count": len(predictions),
        "calibration_ready": calibration_ready,
        "targets": targets,
        "folds": folds,
    }
    if len(predictions) < LOW_INTEREST_MIN_OOF_SAMPLES:
        return {**base, "ready": False, "status": "insufficient-oof-data", "threshold": None}
    if not calibration_ready:
        return {**base, "ready": False, "status": "oof-calibration-unavailable", "threshold": None}

    ordered = sorted(predictions, key=lambda item: (item["probability"], item["index"]))
    total_positives = sum(item["label"] for item in ordered)
    best: dict | None = None
    triaged_positives = 0
    cursor = 0
    while cursor < len(ordered):
        threshold = float(ordered[cursor]["probability"])
        if threshold > LOW_INTEREST_MAX_THRESHOLD:
            break
        group_end = cursor
        while group_end < len(ordered) and float(ordered[group_end]["probability"]) == threshold:
            triaged_positives += int(ordered[group_end]["label"])
            group_end += 1
        triaged_count = group_end
        if triaged_count >= LOW_INTEREST_MIN_TRIAGED_SAMPLES:
            positive_rate = triaged_positives / triaged_count
            positive_loss_rate = triaged_positives / max(1, total_positives)
            upper_95 = _wilson_upper_bound(triaged_positives, triaged_count)
            if (
                positive_rate <= LOW_INTEREST_MAX_POSITIVE_RATE
                and positive_loss_rate <= LOW_INTEREST_MAX_POSITIVE_LOSS_RATE
                and upper_95 <= LOW_INTEREST_MAX_POSITIVE_RATE
            ):
                best = {
                    "threshold": round(threshold, 6),
                    "triaged_count": triaged_count,
                    "triaged_positive_count": triaged_positives,
                    "positive_rate": round(positive_rate, 6),
                    "positive_rate_upper_95": round(upper_95, 6),
                    "positive_loss_rate": round(positive_loss_rate, 6),
                    "workload_reduction_rate": round(triaged_count / len(ordered), 6),
                }
        cursor = group_end
    if best is None:
        return {**base, "ready": False, "status": "safety-target-not-met", "threshold": None}
    return {**base, **best, "ready": True, "status": "ready"}


def train_personalized_model(
    conn: sqlite3.Connection,
    persist: bool = True,
    strict_temporal: bool = False,
) -> dict:
    with _MODEL_LOCK:
        examples = training_examples(conn, strict_temporal=strict_temporal)
        signature = model_data_signature(conn, examples)
        return _train_personalized_model(conn, persist=persist, examples=examples, signature=signature)


def _train_personalized_model(
    conn: sqlite3.Connection,
    persist: bool,
    examples: list[dict],
    signature: str,
) -> dict:
    positives = sum(int(item["label"]) for item in examples)
    negatives = len(examples) - positives
    base = {
        "schema": MODEL_SCHEMA,
        "signature": signature,
        "sample_count": len(examples),
        "positive_count": positives,
        "negative_count": negatives,
    }
    if len(examples) < MIN_LABELED or min(positives, negatives) < MIN_CLASS:
        status = {**base, "ready": False, "status": "insufficient-data"}
        _MODEL_CACHE[signature] = status
        return status
    try:
        modules = sklearn_modules()
        fitted = _fit_vectorizers(examples, modules)
        labels = modules["np"].asarray([int(item["label"]) for item in examples], dtype="int8")
        weights = modules["np"].asarray([float(item["sample_weight"]) for item in examples], dtype="float32")
        calibration_start = _calibration_start(labels)
        selection_end = calibration_start if calibration_start is not None else len(labels)
        selected_c, validation = _select_c(
            fitted["matrix"][:selection_end], labels[:selection_end], weights[:selection_end], modules
        )
        calibrator = _fit_calibrator(
            fitted["matrix"], labels, weights, selected_c, modules, calibration_start
        )
        classifier = _classifier(modules, selected_c)
        classifier.fit(fitted["matrix"], labels, sample_weight=weights)
        bootstrap_models = _bootstrap_classifiers(fitted["matrix"], labels, weights, selected_c, modules)
        version = f"{MODEL_SCHEMA}-{signature}"
        trained_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        low_interest_threshold = learn_low_interest_threshold(
            examples,
            selected_c,
            modules,
            model_version=version,
            generated_at=trained_at,
        )
        accepted, acceptance = (
            evaluation_acceptance(len(examples)) if _is_file_database(conn) else (True, {"source": "test"})
        )
        artifact = {
            **base,
            "ready": True,
            "status": "ready",
            "accepted": accepted,
            "acceptance": acceptance,
            "model_version": version,
            "score_scale": "probability",
            "selected_c": selected_c,
            "validation": validation,
            "calibration": {
                "method": "independent-temporal-holdout",
                "start": calibration_start,
                "sample_count": 0 if calibration_start is None else len(labels) - calibration_start,
                "ready": calibrator is not None,
            },
            "low_interest_threshold": low_interest_threshold,
            "trained_at": trained_at,
            "classifier": classifier,
            "calibrator": calibrator,
            "bootstrap_models": bootstrap_models,
            "dict_vectorizer": fitted["dict_vectorizer"],
            "title_vectorizer": fitted["title_vectorizer"],
            "numeric_names": fitted["numeric_names"],
            "visual_dims": fitted["visual_dims"],
            "visual_mean": fitted["visual_mean"],
            "metadata_support": fitted["metadata_support"],
            "multi_interest_enabled": False,
        }
        _MODEL_CACHE.clear()
        _MODEL_CACHE[signature] = artifact
        if persist and _is_file_database(conn):
            _atomic_dump(artifact, modules)
        _record_training_run(conn, artifact)
        return artifact
    except Exception as exc:
        status = {**base, "ready": False, "status": "failed", "error": str(exc)}
        _MODEL_CACHE[signature] = status
        _record_training_run(conn, status)
        return status


def _is_file_database(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA database_list").fetchall()
    return any(str(row[2] or "") for row in rows if row[1] == "main")


def _atomic_dump(artifact: dict, modules: dict[str, Any]) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="personalized-", suffix=".joblib", dir=MODEL_PATH.parent)
    os.close(handle)
    try:
        modules["joblib"].dump(artifact, temporary, compress=3)
        os.replace(temporary, MODEL_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_training_run(conn: sqlite3.Connection, artifact: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO model_training_runs(
                model_version, status, sample_count, positive_count, negative_count,
                selected_c, metrics_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.get("model_version") or f"{MODEL_SCHEMA}-{artifact.get('signature', 'unknown')}",
                artifact.get("status") or "unknown",
                int(artifact.get("sample_count") or 0),
                int(artifact.get("positive_count") or 0),
                int(artifact.get("negative_count") or 0),
                artifact.get("selected_c"),
                json.dumps({"validation": artifact.get("validation", [])}, ensure_ascii=True),
                artifact.get("error"),
            ),
        )
    except sqlite3.OperationalError:
        pass


def load_personalized_model(
    conn: sqlite3.Connection,
    train_if_needed: bool = True,
    allow_stale: bool = False,
) -> dict:
    if allow_stale and _is_file_database(conn) and MODEL_PATH.exists():
        active_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'model_retrain_active_signature'"
        ).fetchone()
        active_signature = str(active_row[0] or "") if active_row else ""
        if active_signature:
            try:
                modules = sklearn_modules()
                artifact = modules["joblib"].load(MODEL_PATH)
                if (
                    artifact.get("schema") == MODEL_SCHEMA
                    and artifact.get("ready")
                    and artifact.get("signature") == active_signature
                ):
                    return artifact
            except Exception:
                pass
    with _MODEL_LOCK:
        examples = training_examples(conn)
        signature = model_data_signature(conn, examples)
        cached = _MODEL_CACHE.get(signature)
        if cached is not None:
            if _is_file_database(conn) and cached.get("ready"):
                accepted, acceptance = evaluation_acceptance(int(cached.get("sample_count") or 0))
                cached["accepted"] = accepted
                cached["acceptance"] = acceptance
            return cached
        if _is_file_database(conn) and MODEL_PATH.exists():
            try:
                modules = sklearn_modules()
                artifact = modules["joblib"].load(MODEL_PATH)
                if artifact.get("schema") == MODEL_SCHEMA and artifact.get("signature") == signature:
                    accepted, acceptance = evaluation_acceptance(int(artifact.get("sample_count") or 0))
                    artifact["accepted"] = accepted
                    artifact["acceptance"] = acceptance
                    _MODEL_CACHE[signature] = artifact
                    return artifact
                active_row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'model_retrain_active_signature'"
                ).fetchone()
                active_signature = str(active_row[0] or "") if active_row else ""
                if (
                    allow_stale
                    and artifact.get("schema") == MODEL_SCHEMA
                    and artifact.get("ready")
                    and artifact.get("signature") == active_signature
                ):
                    stale = dict(artifact)
                    stale["stale"] = True
                    stale["pending_signature"] = signature
                    return stale
            except Exception:
                pass
        if train_if_needed:
            return _train_personalized_model(
                conn,
                persist=True,
                examples=examples,
                signature=signature,
            )
        return {"ready": False, "status": "missing", "signature": signature}


def score_personalized_galleries(
    conn: sqlite3.Connection,
    galleries: list[dict],
    artifact: dict | None = None,
    train_if_needed: bool = False,
    allow_stale_model: bool = True,
) -> tuple[list[dict], dict]:
    artifact = (
        load_personalized_model(
            conn,
            train_if_needed=train_if_needed,
            allow_stale=allow_stale_model,
        )
        if artifact is None
        else artifact
    )
    if not artifact.get("ready") or not galleries:
        return [], artifact
    modules = sklearn_modules()
    decoded = [_decoded_gallery(item) for item in galleries]
    _attach_visual_image_features(conn, decoded)
    matrix = _transform(decoded, artifact, modules)
    logits = artifact["classifier"].decision_function(matrix)
    probabilities = _calibrated(logits, artifact.get("calibrator"), modules)
    bootstrap_probabilities = []
    for model in artifact.get("bootstrap_models") or []:
        bootstrap_probabilities.append(_calibrated(model.decision_function(matrix), artifact.get("calibrator"), modules))
    if bootstrap_probabilities:
        uncertainty = modules["np"].std(modules["np"].vstack(bootstrap_probabilities), axis=0)
    else:
        uncertainty = modules["np"].zeros(len(galleries))
    results = []
    for index, gallery in enumerate(decoded):
        probability = float(probabilities[index])
        spread = float(uncertainty[index])
        positive, negative = _explain_row(matrix[index], artifact, modules)
        results.append(
            {
                "like_probability": round(probability, 6),
                "uncertainty": round(spread, 6),
                "confidence": round(max(0.0, min(1.0, 1.0 - spread * 4.0)), 6),
                "rank_score": round(probability, 6),
                "model_version": artifact["model_version"],
                "score_scale": artifact.get("score_scale", "probability"),
                "reason_details": {"positive": positive, "negative": negative},
                "text_visual_disagreement": round(_modality_disagreement(matrix[index], artifact, modules), 6),
            }
        )
    return results, artifact


def evaluation_acceptance(current_sample_count: int | None = None) -> tuple[bool, dict]:
    try:
        report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
        if report.get("model_schema") != MODEL_SCHEMA:
            return False, {"passed": False, "reason": "evaluation report targets a different model schema"}
        if report.get("feature_schema") != FEATURE_SCHEMA:
            return False, {"passed": False, "reason": "evaluation report targets a different feature schema"}
        generated_at = str(report.get("generated_at") or "")
        try:
            generated = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return False, {"passed": False, "reason": "evaluation report has no valid generation time"}
        age_days = (datetime.now(timezone.utc) - generated).total_seconds() / 86400.0
        if age_days < -1 or age_days > 30:
            return False, {"passed": False, "reason": "evaluation report is older than 30 days"}
        evaluated_samples = int(report.get("sample_count") or 0)
        minimum_samples = max(MIN_LABELED, int((current_sample_count or 0) * 0.5))
        if evaluated_samples < minimum_samples:
            return False, {
                "passed": False,
                "reason": "evaluation report covers too little of the current labeled data",
                "evaluated_samples": evaluated_samples,
                "minimum_samples": minimum_samples,
            }
        acceptance = dict((report.get("summary") or {}).get("acceptance") or report.get("acceptance") or {})
        acceptance.update({"generated_at": generated_at, "sample_count": evaluated_samples})
        return bool(acceptance.get("passed")), acceptance
    except (OSError, json.JSONDecodeError):
        return False, {"passed": False, "reason": "no passing temporal evaluation report"}


def _feature_names(artifact: dict) -> list[str]:
    metadata = list(artifact["dict_vectorizer"].get_feature_names_out())
    title = [] if artifact["title_vectorizer"] is None else [
        f"title:{value}" for value in artifact["title_vectorizer"].get_feature_names_out()
    ]
    numeric = [f"numeric:{value}" for value in artifact["numeric_names"]]
    visual = [f"visual:{index}" for index in range(artifact["visual_dims"])]
    return [*metadata, *title, *numeric, *visual]


def _explain_row(row, artifact: dict, modules: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    coefficients = artifact["classifier"].coef_[0]
    names = _feature_names(artifact)
    contributions: dict[str, float] = {}
    for index, value in zip(row.indices, row.data):
        contribution = float(value) * float(coefficients[index])
        name = names[index]
        if name.startswith("title:"):
            name = "title similarity"
        elif name.startswith("visual:"):
            name = "visual similarity"
        contributions[name] = contributions.get(name, 0.0) + contribution
    ranked = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
    positive = [{"feature": name, "contribution": round(value, 4)} for name, value in ranked if value > 0][:4]
    negative = [{"feature": name, "contribution": round(value, 4)} for name, value in ranked if value < 0][:4]
    return positive, negative


def _modality_disagreement(row, artifact: dict, modules: dict[str, Any]) -> float:
    coefficients = artifact["classifier"].coef_[0]
    metadata_end = len(artifact["dict_vectorizer"].get_feature_names_out())
    title_end = metadata_end + (0 if artifact["title_vectorizer"] is None else len(artifact["title_vectorizer"].get_feature_names_out()))
    numeric_end = title_end + len(artifact["numeric_names"])
    content_logit = float(row[:, :numeric_end].dot(coefficients[:numeric_end]).item())
    visual_logit = float(row[:, numeric_end:].dot(coefficients[numeric_end:]).item()) if artifact["visual_dims"] else 0.0
    return min(1.0, abs(content_logit - visual_logit) / 6.0)


def model_public_status(artifact: dict) -> dict:
    return {
        key: artifact.get(key)
        for key in (
            "ready", "status", "model_version", "trained_at", "sample_count", "positive_count",
            "negative_count", "selected_c", "validation", "multi_interest_enabled", "error",
            "accepted", "acceptance",
            "score_scale", "calibration", "low_interest_threshold",
        )
        if key in artifact
    }


def positive_query_tags(artifact: dict, limit: int = 12, min_support: int = 3) -> list[str]:
    if not artifact.get("ready") or not artifact.get("accepted") or limit <= 0:
        return []
    names = list(artifact["dict_vectorizer"].get_feature_names_out())
    coefficients = artifact["classifier"].coef_[0]
    candidates = []
    for index, name in enumerate(names):
        if not name.startswith("tag=") or float(coefficients[index]) <= 0:
            continue
        tag = name[4:]
        support = int((artifact.get("metadata_support") or {}).get(name, 0))
        if support >= min_support:
            candidates.append((float(coefficients[index]), tag))
    return [tag for _coefficient, tag in sorted(candidates, reverse=True)[:limit]]
