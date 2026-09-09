from __future__ import annotations

import bisect
import json
import math
import random
import sqlite3
from datetime import date, datetime, timedelta

from .db import snapshot_gallery_features


AUDIT_SOURCE = "fallback-tail-random"
AUDIT_SAMPLING_FRAME = "current-and-retained-fallback"
AUDIT_WINDOW_DAYS = 35
# Keep a short maturation buffer so late-window audits are not treated as
# negative merely because the user has not acted on them yet.
AUDIT_MATURATION_DAYS = 5
AUDIT_MIN_COVERAGE_DAYS = 28
# A four-week elapsed span must contain more than two isolated openings. One
# exposed audit day per week is the minimum activity floor; the completed-label
# and fold gates remain the stronger sample-quality requirements.
AUDIT_MIN_ACTIVE_DAYS = math.ceil(AUDIT_MIN_COVERAGE_DAYS / 7)
AUDIT_MIN_COMPLETED = 150
AUDIT_MIN_FOLDS = 3
AUDIT_FOLD_DAYS = 7
AUDIT_MIN_COMPLETED_PER_FOLD = 20
AUDIT_MIN_PROSPECTIVE_THRESHOLD_EXPOSURES = 30
AUDIT_MAX_POSITIVE_RATE = 0.10
AUDIT_MAX_POSITIVE_LOSS_RATE = 0.05
AUDIT_WILSON_Z = 1.96


def wilson_upper_bound(positives: int, total: int, z: float = AUDIT_WILSON_Z) -> float:
    if total <= 0:
        return 1.0
    rate = positives / total
    denominator = 1.0 + z * z / total
    center = rate + z * z / (2.0 * total)
    margin = z * math.sqrt((rate * (1.0 - rate) + z * z / (4.0 * total)) / total)
    return min(1.0, (center + margin) / denominator)


def _today(value: str | date | None = None) -> date:
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(str(value)[:10])
    return datetime.now().astimezone().date()


def _latest_feature_snapshot_id(conn: sqlite3.Connection, gallery_url: str) -> int | None:
    created = snapshot_gallery_features(conn, gallery_url, "audit")
    if created is not None:
        return created
    row = conn.execute(
        """
        SELECT id
        FROM gallery_feature_snapshots
        WHERE gallery_url = ?
        ORDER BY captured_at DESC, id DESC
        LIMIT 1
        """,
        (gallery_url,),
    ).fetchone()
    return int(row[0]) if row else None


def prepare_daily_low_interest_audits(
    conn: sqlite3.Connection,
    scored: list[dict],
    daily_count: int,
    *,
    audit_date: str | date | None = None,
) -> dict:
    current_date = _today(audit_date)
    date_text = current_date.isoformat()
    daily_count = max(0, min(10, int(daily_count)))
    conn.execute(
        """
        UPDATE low_interest_audits
        SET status = 'incomplete'
        WHERE status = 'pending'
          AND visible_at IS NOT NULL
          AND audit_date < ?
        """,
        (date_text,),
    )
    frame = [item for item in scored if item.get("low_interest_fallback")]
    scored_by_url = {str(item.get("url") or ""): item for item in scored if item.get("url")}
    recent_start = (current_date - timedelta(days=AUDIT_WINDOW_DAYS - 1)).isoformat()
    frozen = conn.execute(
        "SELECT 1 FROM low_interest_audit_days WHERE audit_date=?", (date_text,)
    ).fetchone() is not None
    existing = conn.execute(
        """
        SELECT *
        FROM low_interest_audits
        WHERE audit_date = ?
        ORDER BY id
        """,
        (date_text,),
    ).fetchall()
    carried = []
    if not frozen:
        candidates = conn.execute(
            """SELECT * FROM low_interest_audits
            WHERE status='pending' AND visible_at IS NULL
              AND audit_date>=? AND audit_date<?
            ORDER BY audit_date, id""", (recent_start, date_text),
        ).fetchall()
        used = {str(row["gallery_url"]) for row in existing}
        for row in candidates:
            url = str(row["gallery_url"])
            if url in scored_by_url and url not in used and len(carried) + len(existing) < daily_count:
                carried.append(row)
                used.add(url)
        conn.execute("INSERT INTO low_interest_audit_days(audit_date) VALUES (?)", (date_text,))
        conn.executemany(
            "INSERT OR IGNORE INTO low_interest_audit_slots(audit_date,audit_id) VALUES (?,?)",
            [(date_text, row["id"]) for row in [*existing, *carried]],
        )
    existing_urls = {str(row["gallery_url"]) for row in existing}
    carried_urls = {str(row["gallery_url"]) for row in carried}
    recent_urls = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT gallery_url
            FROM low_interest_audits
            WHERE audit_date BETWEEN ? AND ?
            """,
            (recent_start, date_text),
        )
    }
    # Each day is a fresh uniform sample from that day's tail. Historical samples
    # remain valid observations and must not change today's inclusion probability.
    fresh_eligible = [
        item
        for item in frame
        if str(item.get("url") or "") not in existing_urls
        and str(item.get("url") or "") not in recent_urls
    ]
    eligible = fresh_eligible
    sampling_frame = "current-and-retained-fallback-unseen-35d"
    needed = max(0, daily_count - len(carried)) if not frozen and not existing else 0
    if needed and len(eligible) < needed:
        # Keep the daily audit cadence when the tail is smaller than the
        # rolling no-repeat pool. Replenishment rows are de-duplicated by
        # gallery in the acceptance report, so they cannot inflate Wilson n.
        eligible = [
            item
            for item in frame
            if str(item.get("url") or "") not in existing_urls
            and str(item.get("url") or "") not in carried_urls
        ]
        sampling_frame = "current-and-retained-fallback-replenishment"
    # The first request of a local day freezes the sample. A later rerank must
    # not top up it with a different frame or a different inclusion probability.
    selected = []
    if needed and eligible:
        seed_material = "|".join(sorted(str(item.get("url") or "") for item in eligible))
        rng = random.Random(f"low-interest-audit|{date_text}|{seed_material}")
        selected = rng.sample(eligible, min(needed, len(eligible)))
    selection_probability = (
        min(1.0, min(needed, len(eligible)) / len(eligible))
        if needed and eligible
        else 0.0
    )
    if existing and not needed:
        # Reuse the probability recorded when this day's frozen sample was
        # first created; a rerank must never report a misleading zero.
        selection_probability = float(existing[0]["selection_probability"] or 0.0)
    score_values = [float(item.get("served_rank_score", item.get("rank_score", item.get("score") or 0.0))) for item in frame]
    score_band_low = min(score_values) if score_values else None
    score_band_high = max(score_values) if score_values else None
    for item in selected:
        gallery_url = str(item["url"])
        feature_snapshot_id = _latest_feature_snapshot_id(conn, gallery_url)
        conn.execute(
            """
            INSERT OR IGNORE INTO low_interest_audits(
                audit_date, gallery_url, source, status, selection_probability,
                sampling_frame, frame_size, score_band_low, score_band_high,
                served_model, served_rank_score, served_rank,
                legacy_score, legacy_rank,
                personalized_model_version, personalized_like_probability,
                personalized_rank_score, personalized_rank, feature_snapshot_id
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_text,
                gallery_url,
                AUDIT_SOURCE,
                selection_probability,
                sampling_frame,
                len(eligible),
                score_band_low,
                score_band_high,
                item.get("served_model"),
                item.get("served_rank_score", item.get("rank_score", item.get("score"))),
                item.get("served_rank"),
                item.get("legacy_score"),
                item.get("legacy_rank"),
                item.get("personalized_model_version"),
                item.get("personalized_like_probability"),
                item.get("personalized_rank_score"),
                item.get("personalized_rank"),
                feature_snapshot_id,
            ),
        )
    if not frozen:
        conn.execute(
            """INSERT OR IGNORE INTO low_interest_audit_slots(audit_date,audit_id)
            SELECT ?,id FROM low_interest_audits WHERE audit_date=?""", (date_text, date_text),
        )
    rows = conn.execute(
        """
        SELECT a.* FROM low_interest_audits a JOIN low_interest_audit_slots s ON s.audit_id=a.id
        WHERE s.audit_date=? AND a.status='pending' ORDER BY a.audit_date,a.id
        """,
        (date_text,),
    ).fetchall()
    audit_by_url = {str(row["gallery_url"]): row for row in rows}
    for gallery_url, row in audit_by_url.items():
        item = scored_by_url.get(gallery_url)
        if item is None:
            continue
        item["audit_id"] = int(row["id"])
        item["audit_source"] = str(row["source"])
        item["audit_sampling_frame"] = str(row["sampling_frame"])
        item["audit_selected_date"] = str(row["audit_date"])
        item["audit_carried"] = str(row["audit_date"]) != date_text
        item["audit_selection_probability"] = float(row["selection_probability"])
        item["audit_original_low_interest_reason"] = item.get("low_interest_reason") or "bottom-percent"
        item["audit_original_low_interest"] = True
        item["low_interest"] = False
        item["very_low_interest"] = False
        item["low_interest_reason"] = None
    original_frames = {row["sampling_frame"] for row in rows}
    original_probabilities = {float(row["selection_probability"]) for row in rows}
    return {
        "date": date_text,
        "source": AUDIT_SOURCE,
        "daily_count": daily_count,
        "frame_size": len(frame),
        "selected_count": len(audit_by_url),
        "sampling_frame": next(iter(original_frames), sampling_frame)
        if len(original_frames) <= 1 else "mixed-original-frames",
        "selection_probability": next(iter(original_probabilities), None)
        if len(original_probabilities) <= 1 else None,
        "carried_count": sum(str(row["audit_date"]) != date_text for row in rows),
        "score_band": [score_band_low, score_band_high],
    }


def complete_low_interest_audit(
    conn: sqlite3.Connection,
    gallery_url: str,
    *,
    outcome_source: str,
    positive: bool,
    feedback_id: int | None = None,
) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM low_interest_audits
        WHERE gallery_url = ? AND status IN ('pending', 'incomplete')
        ORDER BY audit_date DESC, id DESC
        LIMIT 1
        """,
        (gallery_url,),
    ).fetchone()
    if row is None:
        return 0
    cursor = conn.execute(
        """
        UPDATE low_interest_audits
        SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
            outcome_source = ?, outcome_positive = ?, feedback_id = ?
        WHERE id = ?
        """,
        (str(outcome_source or "feedback")[:40], 1 if positive else 0, feedback_id, int(row["id"])),
    )
    return max(0, int(cursor.rowcount or 0))


def mark_low_interest_audit_exposed(conn: sqlite3.Connection, audit_id: int) -> None:
    conn.execute(
        """
        UPDATE low_interest_audits
        SET exposed_at = COALESCE(exposed_at, CURRENT_TIMESTAMP)
        WHERE id = ?
        """,
        (int(audit_id),),
    )


def record_visible_impressions(conn: sqlite3.Connection, request_id: str, surface: str, items: list) -> int:
    """Confirm only existing, instrumented deliveries; never trust an audit ID from the client."""
    if not isinstance(request_id, str) or not request_id or len(request_id) > 120:
        raise ValueError("valid request_id is required")
    if not isinstance(surface, str) or not isinstance(items, list):
        raise ValueError("surface and items are required")
    updated = 0
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        url = item.get("gallery_url")
        if not isinstance(url, str):
            continue
        row = conn.execute(
            """SELECT id, audit_id FROM recommendation_impressions
            WHERE request_id=? AND gallery_url=? AND surface=?
              AND visibility_protocol='viewport-v1' AND visible_at IS NULL""",
            (request_id, url, surface),
        ).fetchone()
        if row is None:
            continue
        cursor = conn.execute(
            "UPDATE recommendation_impressions SET visible_at=CURRENT_TIMESTAMP WHERE id=? AND visible_at IS NULL",
            (row["id"],),
        )
        updated += cursor.rowcount
        if row["audit_id"] is not None:
            conn.execute(
                """UPDATE low_interest_audits SET visible_at=COALESCE(visible_at,CURRENT_TIMESTAMP)
                WHERE id=? AND gallery_url=?""", (row["audit_id"], url),
            )
    return updated


def _outcome_events(conn: sqlite3.Connection) -> dict[str, list[tuple[str, bool]]]:
    events: dict[str, list[tuple[str, bool]]] = {}
    for row in conn.execute(
        """
        SELECT gallery_url, created_at, vote, score
        FROM feedback
        WHERE vote != 0 OR score IS NOT NULL
        ORDER BY created_at, id
        """
    ):
        vote = float(row["vote"] or 0.0)
        score = row["score"]
        positive = int(score) > 3 if score is not None else vote > 0
        if score is not None and int(score) == 3:
            continue
        events.setdefault(str(row["gallery_url"]), []).append((str(row["created_at"]), positive))
    for row in conn.execute(
        "SELECT gallery_url, updated_at AS created_at, kind FROM gallery_marks ORDER BY updated_at"
    ):
        events.setdefault(str(row["gallery_url"]), []).append(
            (str(row["created_at"]), str(row["kind"]) == "favorite")
        )
    for gallery_events in events.values():
        gallery_events.sort()
    return events


def _prospective_outcomes(
    conn: sqlite3.Connection,
    window_start: str,
    window_end: str | None = None,
    *,
    visible_only: bool = False,
) -> list[dict]:
    timestamp = "visible_at" if visible_only else "created_at"
    impressions_by_gallery: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        f"""
        SELECT gallery_url, {timestamp} AS created_at, ranking_context_json
        FROM recommendation_impressions
        WHERE {timestamp} >= ?
          AND (? IS NULL OR {timestamp} < ?)
        ORDER BY gallery_url, {timestamp}, id
        """,
        (window_start, window_end, window_end),
    ):
        impressions_by_gallery.setdefault(str(row["gallery_url"]), []).append(row)
    outcomes = []
    for gallery_url, events in _outcome_events(conn).items():
        impressions = impressions_by_gallery.get(gallery_url) or []
        if not impressions:
            continue
        impression_times = [str(row["created_at"]) for row in impressions]
        for event_at, positive in events:
            if window_end is not None and event_at >= window_end:
                continue
            index = bisect.bisect_right(impression_times, event_at) - 1
            if index < 0:
                continue
            row = impressions[index]
            try:
                context = json.loads(row["ranking_context_json"] or "{}")
            except json.JSONDecodeError:
                context = {}
            outcomes.append(
                {
                    "gallery_url": gallery_url,
                    "impression_at": str(row["created_at"]),
                    "outcome_at": event_at,
                    "positive": bool(positive),
                    "context": context if isinstance(context, dict) else {},
                }
            )
            break
    return outcomes


def _threshold_cohort(context: dict) -> tuple[str, float] | None:
    version = context.get("prospective_threshold_model_version")
    threshold = context.get("prospective_threshold")
    if (
        not isinstance(version, str) or not version.strip()
        or context.get("personalized_model_version") != version
        or type(context.get("prospective_threshold_triage")) is not bool
        or type(threshold) not in (int, float)
        or not math.isfinite(threshold) or not 0 <= threshold <= 1
    ):
        return None
    return version, float(threshold)


def low_interest_audit_report(
    conn: sqlite3.Connection,
    *,
    as_of: str | date | None = None,
) -> dict:
    current_date = _today(as_of)
    window_start_date = current_date - timedelta(days=AUDIT_WINDOW_DAYS - 1)
    rows = conn.execute(
        """
        SELECT *
        FROM low_interest_audits
        WHERE audit_date BETWEEN ? AND ?
        ORDER BY audit_date, id
        """,
        (window_start_date.isoformat(), current_date.isoformat()),
    ).fetchall()
    matured_end_date = current_date - timedelta(days=AUDIT_MATURATION_DAYS)
    matured_rows = [
        row
        for row in rows
        if date.fromisoformat(str(row["audit_date"])[:10]) <= matured_end_date
    ]
    completed_by_gallery: dict[str, sqlite3.Row] = {}
    report_end = (current_date + timedelta(days=1)).isoformat() + " 00:00:00"
    for row in matured_rows:
        if row["status"] != "completed" or row["outcome_positive"] is None:
            continue
        if not row["completed_at"] or str(row["completed_at"]) >= report_end:
            continue
        completed_by_gallery.setdefault(str(row["gallery_url"]), row)
    completed = list(completed_by_gallery.values())
    positives = sum(int(row["outcome_positive"]) for row in completed)
    completed_count = len(completed)
    upper_95 = wilson_upper_bound(positives, completed_count)
    dates = [date.fromisoformat(str(row["audit_date"])[:10]) for row in matured_rows]
    distinct_audit_days = len(set(dates))
    active_audit_days = len(
        {
            date.fromisoformat(str(row["audit_date"])[:10])
            for row in matured_rows
            if row["visible_at"] is not None and str(row["visible_at"]) < report_end
        }
    )
    elapsed_span_days = (max(dates) - min(dates)).days + 1 if dates else 0
    folds = []
    if dates:
        origin = min(dates)
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in completed:
            audit_day = date.fromisoformat(str(row["audit_date"])[:10])
            grouped.setdefault((audit_day - origin).days // AUDIT_FOLD_DAYS, []).append(row)
        for index in sorted(grouped):
            fold_rows = grouped[index]
            fold_positives = sum(int(row["outcome_positive"]) for row in fold_rows)
            folds.append(
                {
                    "index": index,
                    "start": (origin + timedelta(days=index * AUDIT_FOLD_DAYS)).isoformat(),
                    "end": (origin + timedelta(days=(index + 1) * AUDIT_FOLD_DAYS - 1)).isoformat(),
                    "completed": len(fold_rows),
                    "positives": fold_positives,
                    "positive_rate_upper_95": round(wilson_upper_bound(fold_positives, len(fold_rows)), 6),
                    "eligible": len(fold_rows) >= AUDIT_MIN_COMPLETED_PER_FOLD,
                }
            )
    eligible_fold_count = sum(bool(fold["eligible"]) for fold in folds)
    prospective_window_end = (matured_end_date + timedelta(days=1)).isoformat() + " 00:00:00"
    prospective = _prospective_outcomes(
        conn,
        f"{window_start_date.isoformat()} 00:00:00",
        prospective_window_end,
    )
    threshold_exposures: dict[tuple[str, float], set[str]] = {}
    for row in conn.execute(
        """
        SELECT gallery_url, ranking_context_json
        FROM recommendation_impressions
        WHERE created_at >= ? AND created_at < ?
        """,
        (f"{window_start_date.isoformat()} 00:00:00", prospective_window_end),
    ):
        try:
            context = json.loads(row["ranking_context_json"] or "{}")
        except json.JSONDecodeError:
            context = {}
        cohort = _threshold_cohort(context) if isinstance(context, dict) else None
        if cohort is not None:
            galaxies = threshold_exposures.setdefault(cohort, set())
            if context["prospective_threshold_triage"]:
                galaxies.add(str(row["gallery_url"]))
    positive_outcomes = [item for item in prospective if item["positive"]]
    threshold_outcomes = [
        item for item in positive_outcomes if _threshold_cohort(item["context"]) is not None
    ]
    cohort_keys = set(threshold_exposures) | {
        _threshold_cohort(item["context"]) for item in threshold_outcomes
    }
    # Version/threshold cohorts are diagnostic only. Do not pool different
    # decisions, or treat unknown decisions as known non-triaged positives.
    single_cohort = len(cohort_keys) == 1
    prospective_threshold_exposures = (
        len(next(iter(threshold_exposures.values()), set())) if single_cohort else 0
    )
    threshold_lost = sum(
        item["context"]["prospective_threshold_triage"] for item in threshold_outcomes
    )
    positive_loss_rate = (
        threshold_lost / len(threshold_outcomes) if single_cohort and threshold_outcomes else None
    )
    paired_outcomes = [
        item for item in positive_outcomes
        if all(type(item["context"].get(field)) is bool
               for field in ("legacy_bottom_20", "personalized_bottom_20"))
    ]
    comparison = {}
    for model, field in (("legacy", "legacy_bottom_20"), ("personalized", "personalized_bottom_20")):
        lost = sum(item["context"][field] for item in paired_outcomes)
        comparison[model] = {
            "positive_count": len(paired_outcomes),
            "bottom_positive_count": lost,
            "positive_loss_rate": round(lost / len(paired_outcomes), 6) if paired_outcomes else None,
        }
    visible_outcomes = _prospective_outcomes(
        conn, f"{window_start_date.isoformat()} 00:00:00",
        prospective_window_end, visible_only=True,
    )
    visible_positives = [item for item in visible_outcomes if item["positive"]]
    visible_paired = [
        item for item in visible_positives
        if all(type(item["context"].get(field)) is bool
               for field in ("legacy_bottom_20", "personalized_bottom_20"))
    ]
    visible_comparison = {
        model: {
            "positive_count": len(visible_paired),
            "bottom_positive_count": sum(item["context"][field] for item in visible_paired),
            "positive_loss_rate": (
                round(sum(item["context"][field] for item in visible_paired) / len(visible_paired), 6)
                if visible_paired else None
            ),
        }
        for model, field in (("legacy", "legacy_bottom_20"), ("personalized", "personalized_bottom_20"))
    }
    visibility_counts = dict(conn.execute(
        """SELECT COUNT(*) AS delivered,
        COALESCE(SUM(visibility_protocol IS NULL),0) AS legacy_visibility_unknown,
        COALESCE(SUM(visibility_protocol='viewport-v1'),0) AS instrumented_delivered,
        COALESCE(SUM(visible_at IS NOT NULL AND visible_at<?),0) AS confirmed_visible
        FROM recommendation_impressions WHERE created_at>=? AND created_at<?""",
        (prospective_window_end, f"{window_start_date.isoformat()} 00:00:00", prospective_window_end),
    ).fetchone())
    checks = {
        "elapsed_span_days": elapsed_span_days >= AUDIT_MIN_COVERAGE_DAYS,
        "active_audit_days": active_audit_days >= AUDIT_MIN_ACTIVE_DAYS,
        "completed_samples": completed_count >= AUDIT_MIN_COMPLETED,
        "positive_rate_upper_95": upper_95 <= AUDIT_MAX_POSITIVE_RATE,
        "positive_loss_rate": positive_loss_rate is not None and positive_loss_rate <= AUDIT_MAX_POSITIVE_LOSS_RATE,
        "single_threshold_cohort": single_cohort,
        # A fallback-tail audit does not validate the incremental absolute
        # threshold region. No current collector implements that protocol.
        "candidate_protocol_validated": False,
        "non_overlapping_folds": eligible_fold_count >= AUDIT_MIN_FOLDS,
        # Without a minimum number of candidate-threshold exposures, an active
        # threshold could appear safe simply because suppressed items stopped
        # receiving impressions.
        "prospective_threshold_exposures": (
            prospective_threshold_exposures >= AUDIT_MIN_PROSPECTIVE_THRESHOLD_EXPOSURES
        ),
    }
    # Preserve the pre-existing check name for older API consumers.
    checks["coverage_days"] = checks["elapsed_span_days"]
    reason_order = (
        ("elapsed_span_days", "audit-insufficient-duration"),
        ("active_audit_days", "audit-insufficient-active-days"),
        ("completed_samples", "audit-insufficient-samples"),
        ("non_overlapping_folds", "audit-insufficient-temporal-folds"),
        ("positive_rate_upper_95", "audit-wilson-limit-exceeded"),
        ("single_threshold_cohort", "prospective-threshold-cohort-unavailable"),
        ("positive_loss_rate", "prospective-positive-loss-exceeded"),
        ("prospective_threshold_exposures", "prospective-threshold-insufficient-exposure"),
        ("candidate_protocol_validated", "prospective-candidate-protocol-unavailable"),
    )
    status = "ready"
    for check, reason in reason_order:
        if not checks[check]:
            status = reason
            break
    counts = {name: sum(row["status"] == name for row in rows) for name in ("pending", "completed", "incomplete")}
    matured_counts = {
        name: sum(row["status"] == name for row in matured_rows)
        for name in ("pending", "completed", "incomplete")
    }
    return {
        "ready": all(checks.values()),
        "status": status,
        "failed_reasons": [reason for check, reason in reason_order if not checks[check]],
        "diagnostic_only": True,
        "comparison_exposure_basis": "delivered",
        "window_days": AUDIT_WINDOW_DAYS,
        "maturation_days": AUDIT_MATURATION_DAYS,
        "window_start": window_start_date.isoformat(),
        "window_end": current_date.isoformat(),
        "matured_end": (current_date - timedelta(days=AUDIT_MATURATION_DAYS)).isoformat(),
        # Keep coverage_days as a compatibility alias; new callers should use
        # elapsed_span_days to distinguish elapsed time from active days.
        "coverage_days": elapsed_span_days,
        "elapsed_span_days": elapsed_span_days,
        "distinct_audit_days": distinct_audit_days,
        "active_audit_days": active_audit_days,
        "counts": counts,
        "matured_counts": matured_counts,
        "completed_samples": completed_count,
        "positive_count": positives,
        "positive_rate": round(positives / completed_count, 6) if completed_count else None,
        "positive_rate_upper_95": round(upper_95, 6),
        "prospective_labeled": len(prospective),
        "prospective_positive_count": len(positive_outcomes),
        "paired_positive_count": len(paired_outcomes),
        "paired_excluded_positive_count": len(positive_outcomes) - len(paired_outcomes),
        "threshold_known_positive_count": len(threshold_outcomes),
        "threshold_unknown_positive_count": len(positive_outcomes) - len(threshold_outcomes),
        "threshold_cohort_count": len(cohort_keys),
        "prospective_threshold_positive_count": threshold_lost if single_cohort else None,
        "prospective_threshold_exposures": prospective_threshold_exposures,
        "positive_loss_rate": round(positive_loss_rate, 6) if positive_loss_rate is not None else None,
        "eligible_fold_count": eligible_fold_count,
        "folds": folds,
        "model_comparison": comparison,
        "visible_model_comparison": visible_comparison,
        "visible_paired_excluded_positive_count": len(visible_positives) - len(visible_paired),
        "visibility_counts": visibility_counts,
        "checks": checks,
        "targets": {
            "min_coverage_days": AUDIT_MIN_COVERAGE_DAYS,
            "min_elapsed_span_days": AUDIT_MIN_COVERAGE_DAYS,
            "min_active_audit_days": AUDIT_MIN_ACTIVE_DAYS,
            "min_completed_samples": AUDIT_MIN_COMPLETED,
            "min_non_overlapping_folds": AUDIT_MIN_FOLDS,
            "max_positive_rate_upper_95": AUDIT_MAX_POSITIVE_RATE,
            "max_positive_loss_rate": AUDIT_MAX_POSITIVE_LOSS_RATE,
            "min_prospective_threshold_exposures": AUDIT_MIN_PROSPECTIVE_THRESHOLD_EXPOSURES,
        },
    }
