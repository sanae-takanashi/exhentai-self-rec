from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any


MODEL_SCHEMA = "personalized-content-v2"
FEATURE_SCHEMA = "content-features-v3"
MIN_LABELED = 50
MIN_CLASS = 15
BOOTSTRAP_MODELS = 8
CALIBRATION_RECENCY_DECAY = 3.0
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
    if surface not in {"review", "discovery", "updates", "preview", "history", "favorite", "ban", "api"}:
        return default
    return surface


def model_data_signature(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT COUNT(*) AS events, COALESCE(MAX(id), 0) AS latest_id,
               COALESCE(MAX(created_at), '') AS latest_at
        FROM feedback
        """
    ).fetchone()
    marks = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(MAX(updated_at), '') AS latest_at FROM gallery_marks"
    ).fetchone()
    downloads = conn.execute(
        """
        SELECT COUNT(DISTINCT gallery_url) AS count, COALESCE(MAX(updated_at), '') AS latest_at
        FROM hath_downloads
        WHERE status = 'completed' AND gallery_url IS NOT NULL
        """
    ).fetchone()
    galleries = conn.execute(
        """
        SELECT COUNT(*) AS count,
               COALESCE(SUM(LENGTH(title) + LENGTH(tags_json) + LENGTH(COALESCE(tag_weights_json, ''))), 0) AS text_size,
               COALESCE(SUM(CASE WHEN detail_fetched_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS detailed,
               COALESCE(SUM(CASE WHEN visual_embedding_json IS NOT NULL AND visual_embedding_json != '' THEN 1 ELSE 0 END), 0) AS visual,
               COALESCE(MAX(visual_embedding_at), '') AS latest_visual
        FROM galleries
        """
    ).fetchone()
    visual_images = conn.execute(
        "SELECT COUNT(*) AS count, COALESCE(MAX(created_at), '') AS latest_at FROM gallery_visual_images"
    ).fetchone()
    labels = conn.execute(
        """
        SELECT COALESCE(GROUP_CONCAT(gallery_url || ':' || vote || ':' || COALESCE(score, '') || ':' || COALESCE(reason_code, ''), '|'), '')
        FROM feedback
        """
    ).fetchone()[0]
    payload = (
        f"{row['events']}|{row['latest_id']}|{row['latest_at']}|{marks['count']}|{marks['latest_at']}|"
        f"{downloads['count']}|{downloads['latest_at']}|{hath_download_signal_weight(conn)}|"
        f"{galleries['count']}|{galleries['text_size']}|{galleries['detailed']}|{galleries['visual']}|"
        f"{galleries['latest_visual']}|{visual_images['count']}|{visual_images['latest_at']}|"
        f"{FEATURE_SCHEMA}|{labels}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
            SELECT gallery_url, embedding_json, embedding_version
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


def training_examples(conn: sqlite3.Connection) -> list[dict]:
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
    examples: dict[str, dict] = {}
    reason_counts = {
        str(row["reason_code"]): int(row["count"])
        for row in conn.execute(
            """
            SELECT reason_code, COUNT(*) AS count
            FROM feedback
            WHERE vote < 0 AND reason_code IS NOT NULL AND reason_code != ''
            GROUP BY reason_code
            """
        )
    }
    for row in feedback:
        item = _decoded_gallery(row)
        if item.get("reason_code") == "duplicate_update":
            continue
        vote = float(item.get("vote") or 0)
        if vote == 0:
            continue
        item["label"] = 1 if vote > 0 else 0
        item["sample_weight"] = max(0.75, min(2.0, abs(vote)))
        reason = str(item.get("reason_code") or "")
        item["active_reason_code"] = reason if reason_counts.get(reason, 0) >= 20 else None
        item["label_source"] = "score" if item.get("score") is not None else "vote"
        examples[item["url"]] = item
    download_weight = hath_download_signal_weight(conn)
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
        for row in downloads:
            item = _decoded_gallery(row)
            item["label"] = 1
            item["sample_weight"] = download_weight
            item["label_source"] = "hath-download"
            examples[item["url"]] = item
    marks = conn.execute(
        """
        SELECT g.*, NULL AS feedback_id, NULL AS vote, NULL AS score, NULL AS reason_code,
               'api' AS surface, m.updated_at AS feedback_at, m.kind AS mark_kind
        FROM gallery_marks m JOIN galleries g ON g.url = m.gallery_url
        """
    ).fetchall()
    for row in marks:
        item = _decoded_gallery(row)
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
    active_reason = gallery.get("active_reason_code")
    for raw in gallery.get("tags") or []:
        tag = str(raw or "").strip().lower()
        if tag:
            result[f"tag={tag}"] = 1.35 if active_reason == "content_tags" else 1.0
    if active_reason == "creator_character":
        for feature in list(result):
            if feature.startswith("uploader=") or feature.startswith("tag=artist:") or feature.startswith("tag=character:"):
                result[feature] *= 1.35
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
    active_reason = gallery.get("active_reason_code")
    quality_scale = 1.35 if active_reason == "quality" else 1.0
    image_reason_scale = 1.35 if active_reason == "too_few_relevant_images" else 1.0
    page_value = 0.0 if pages is None else min(1.0, math.log1p(max(0, int(pages))) / 7.0)
    image_count = max(0, int(gallery.get("visual_image_count") or 0))
    image_cohesion = float(gallery.get("visual_image_cohesion") or 1.0)
    image_min_similarity = float(gallery.get("visual_image_min_similarity") or 1.0)
    return {
        "site_rating": (0.0 if rating is None else (float(rating) - 3.5) / 1.5) * quality_scale,
        "site_rating_missing": 1.0 if rating is None else 0.0,
        "log_pages": page_value,
        "short_gallery": (1.0 - page_value) * (1.35 if active_reason == "gallery_too_small" else 1.0),
        "page_count_missing": 1.0 if pages is None else 0.0,
        "detail_ready": 1.0 if gallery.get("detail_fetched_at") else 0.0,
        "tag_coverage": min(1.0, len(tags) / 50.0),
        "visual_ready": 1.0 if isinstance(visual, list) and visual else 0.0,
        "visual_image_stats_ready": 1.0 if image_count else 0.0,
        "visual_image_count": min(1.0, math.log1p(image_count) / math.log(13.0)),
        "visual_image_diversity": min(1.0, max(0.0, (1.0 - image_cohesion) * 4.0) * image_reason_scale),
        "visual_image_outlier": min(1.0, max(0.0, (1.0 - image_min_similarity) * 2.0) * image_reason_scale),
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
        scale = 1.35 if gallery.get("active_reason_code") == "visual_style" else 1.0
        return [float(value) * scale for value in raw]
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
    window = max(20, min(100, count // 5))
    starts = sorted({max(MIN_LABELED, int(count * fraction)) for fraction in (0.5, 0.65, 0.8)})
    starts = sorted({*starts, max(MIN_LABELED, count - window)})
    return [(start, min(count, start + window)) for start in starts if start < count]


def _select_c(matrix, labels, weights, modules: dict[str, Any]) -> tuple[float, list[dict]]:
    reports: list[dict] = []
    best_c = 0.2
    best_auc = -1.0
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
        mean_auc = sum(aucs) / len(aucs) if aucs else 0.0
        reports.append({"c": c_value, "mean_auc": round(mean_auc, 6), "folds": len(aucs)})
        if mean_auc > best_auc:
            best_auc = mean_auc
            best_c = c_value
    return best_c, reports


def _fit_calibrator(matrix, labels, weights, c_value: float, modules: dict[str, Any]):
    records: dict[int, tuple[float, int, float]] = {}
    for start, end in _rolling_splits(len(labels)):
        if len(set(labels[:start])) < 2:
            continue
        model = _classifier(modules, c_value)
        model.fit(matrix[:start], labels[:start], sample_weight=weights[:start])
        for index, (logit, target) in enumerate(
            zip(model.decision_function(matrix[start:end]), labels[start:end]),
            start=start,
        ):
            relative_position = index / max(1, len(labels) - 1)
            recency_weight = math.exp(CALIBRATION_RECENCY_DECAY * (relative_position - 1.0))
            records[index] = (float(logit), int(target), float(weights[index]) * recency_weight)
    logits = [record[0] for _index, record in sorted(records.items())]
    targets = [record[1] for _index, record in sorted(records.items())]
    calibration_weights = [record[2] for _index, record in sorted(records.items())]
    if len(logits) < 30 or len(set(targets)) < 2:
        return None
    calibrator = modules["LogisticRegression"](C=1.0, solver="liblinear", max_iter=300, random_state=1702)
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


def train_personalized_model(conn: sqlite3.Connection, persist: bool = True) -> dict:
    signature = model_data_signature(conn)
    examples = training_examples(conn)
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
        selected_c, validation = _select_c(fitted["matrix"], labels, weights, modules)
        calibrator = _fit_calibrator(fitted["matrix"], labels, weights, selected_c, modules)
        classifier = _classifier(modules, selected_c)
        classifier.fit(fitted["matrix"], labels, sample_weight=weights)
        bootstrap_models = _bootstrap_classifiers(fitted["matrix"], labels, weights, selected_c, modules)
        version = f"{MODEL_SCHEMA}-{signature}"
        accepted, acceptance = evaluation_acceptance() if _is_file_database(conn) else (True, {"source": "test"})
        artifact = {
            **base,
            "ready": True,
            "status": "ready",
            "accepted": accepted,
            "acceptance": acceptance,
            "model_version": version,
            "selected_c": selected_c,
            "validation": validation,
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "classifier": classifier,
            "calibrator": calibrator,
            "bootstrap_models": bootstrap_models,
            "dict_vectorizer": fitted["dict_vectorizer"],
            "title_vectorizer": fitted["title_vectorizer"],
            "numeric_names": fitted["numeric_names"],
            "visual_dims": fitted["visual_dims"],
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


def load_personalized_model(conn: sqlite3.Connection, train_if_needed: bool = True) -> dict:
    signature = model_data_signature(conn)
    cached = _MODEL_CACHE.get(signature)
    if cached is not None:
        return cached
    if _is_file_database(conn) and MODEL_PATH.exists():
        try:
            modules = sklearn_modules()
            artifact = modules["joblib"].load(MODEL_PATH)
            if artifact.get("schema") == MODEL_SCHEMA and artifact.get("signature") == signature:
                _MODEL_CACHE[signature] = artifact
                return artifact
        except Exception:
            pass
    if train_if_needed:
        return train_personalized_model(conn)
    return {"ready": False, "status": "missing", "signature": signature}


def score_personalized_galleries(conn: sqlite3.Connection, galleries: list[dict]) -> tuple[list[dict], dict]:
    artifact = load_personalized_model(conn)
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
                "reason_details": {"positive": positive, "negative": negative},
                "text_visual_disagreement": round(_modality_disagreement(matrix[index], artifact, modules), 6),
            }
        )
    return results, artifact


def evaluation_acceptance() -> tuple[bool, dict]:
    try:
        report = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
        if report.get("model_schema") != MODEL_SCHEMA:
            return False, {"passed": False, "reason": "evaluation report targets a different model schema"}
        acceptance = (report.get("summary") or {}).get("acceptance") or report.get("acceptance") or {}
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
        )
        if key in artifact
    }


def positive_query_tags(artifact: dict, limit: int = 12, min_support: int = 3) -> list[str]:
    if not artifact.get("ready") or limit <= 0:
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
