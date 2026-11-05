"""Read-only, exactly attributed observational ranking reports.

Delivery cohorts and outcomes deliberately have different denominators. A gallery
can occur in several delivery cohorts, but only its latest feedback as of the
report end can be an outcome, and only its exact linked impression gets credit.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3


SURFACES = frozenset({"all", "review", "low-interest", "discovery", "preview", "updates",
                      "audit", "history", "favorite", "ban", "api", "unknown"})


def _text(value, default="unknown"):
    return value if isinstance(value, str) and value else default


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _context(raw):
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _identity(row):
    context = _context(row["ranking_context_json"])
    policy = context.get("ranking_policy") or context.get("served_model")
    if policy == "personalized-trial":
        policy = "personalized_trial"
    return (_text(row["surface"]), _text(policy), _text(row["model_version"]),
            _text(context.get("ranking_artifact_id")), _text(context.get("ranking_trained_at")))


def _new_cohort(identity):
    surface, policy, version, artifact, trained = identity
    return {"surface": surface, "policy": policy, "model_version": version,
            "artifact_id": artifact, "trained_at": trained, "delivered_events": 0,
            "visible_events": 0, "fallback_events": 0, "visibility_uninstrumented_events": 0,
            "positive": 0, "negative": 0, "neutral": 0, "excluded_duplicate_update": 0,
            "invalid_outcome": 0, "visible_positive": 0, "visible_negative": 0,
            "visible_neutral": 0, "negative_reasons": Counter(), "_galleries": set(),
            "_visible": set(), "_outcomes": set(), "_first": None, "_last": None}


def ranking_insights(conn: sqlite3.Connection, *, days: int = 30,
                     surface: str = "all", now: datetime | None = None) -> dict:
    """Return UTC delivery-window cohorts without writes, training or site calls.

    ``now`` is injectable for deterministic tests; endpoints should omit it.
    Valid days are 1..365. Unknown outcomes are never converted to negatives.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    if surface not in SURFACES:
        raise ValueError("unsupported insights surface")
    if not conn.in_transaction:
        conn.execute("BEGIN")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    # SQLite's CURRENT_TIMESTAMP has second precision. Use a fractional end so
    # records in the current second are included without accepting future rows.
    end = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")
    impressions = conn.execute(
        """SELECT id, gallery_url, surface, model_version, ranking_context_json,
                  visibility_protocol, visible_at, created_at
           FROM recommendation_impressions WHERE julianday(created_at)>=julianday(?)
               AND julianday(created_at)<julianday(?)
           ORDER BY id""", (start, end)).fetchall()
    cohorts = {}
    deliveries = {}
    all_galleries, all_visible = set(), set()
    for row in impressions:
        identity = _identity(row)
        if surface != "all" and identity[0] != surface:
            continue
        cohort = cohorts.setdefault(identity, _new_cohort(identity))
        cohort["delivered_events"] += 1
        gallery = row["gallery_url"]
        cohort["_galleries"].add(gallery)
        all_galleries.add(gallery)
        context = _context(row["ranking_context_json"])
        cohort["fallback_events"] += bool(context.get("ranking_fallback"))
        cohort["visibility_uninstrumented_events"] += row["visibility_protocol"] != "viewport-v1"
        created_at, visible_at = _timestamp(row["created_at"]), _timestamp(row["visible_at"])
        visible = (row["visibility_protocol"] == "viewport-v1" and visible_at is not None
                   and created_at <= visible_at < now)
        if visible:
            cohort["visible_events"] += 1
            cohort["_visible"].add(gallery)
            all_visible.add(gallery)
        cohort["_first"] = min(cohort["_first"] or row["created_at"], row["created_at"])
        cohort["_last"] = max(cohort["_last"] or row["created_at"], row["created_at"])
        deliveries[row["id"]] = (row, cohort, visible)

    # Pick the latest feedback as of the end before filtering the date window.
    # In particular, an unlinked correction must not leave an older linked vote
    # credited to a model. Deleting an undo row naturally restores its predecessor.
    feedback = conn.execute(
        """SELECT f.id, f.gallery_url, f.vote, f.reason_code, f.surface, f.created_at,
                  l.impression_id
           FROM feedback f JOIN (
               SELECT gallery_url, MAX(id) AS latest_id FROM feedback
               WHERE julianday(created_at)<julianday(?) GROUP BY gallery_url
           ) latest ON latest.latest_id=f.id
           LEFT JOIN feedback_impression_links l ON l.feedback_id=f.id
           WHERE julianday(f.created_at)>=julianday(?)
               AND julianday(f.created_at)<julianday(?)""", (end, start, end)).fetchall()
    unknown = Counter()
    feedback_count = 0
    review_explicit = 0
    for row in feedback:
        feedback_surface = _text(row["surface"])
        if surface != "all" and feedback_surface != surface:
            continue
        feedback_count += 1
        delivery = deliveries.get(row["impression_id"])
        if delivery is None:
            unknown["no_link" if row["impression_id"] is None else "outside_delivery_window_or_surface"] += 1
            continue
        impression, cohort, visible = delivery
        if (impression["gallery_url"] != row["gallery_url"] or
                _text(impression["surface"]) != feedback_surface or
                _timestamp(impression["created_at"]) > _timestamp(row["created_at"])):
            unknown["mismatched_link"] += 1
            continue
        cohort["_outcomes"].add(row["gallery_url"])
        if row["reason_code"] == "duplicate_update":
            cohort["excluded_duplicate_update"] += 1
            continue
        vote = row["vote"]
        if not isinstance(vote, (int, float)) or not math.isfinite(vote):
            cohort["invalid_outcome"] += 1
            continue
        outcome = "positive" if vote > 0 else "negative" if vote < 0 else "neutral"
        cohort[outcome] += 1
        if visible and _timestamp(impression["visible_at"]) <= _timestamp(row["created_at"]):
            cohort["visible_" + outcome] += 1
        if outcome == "negative":
            cohort["negative_reasons"][_text(row["reason_code"], "unspecified")] += 1
        if feedback_surface == "review" and vote != 0 and cohort["policy"] != "unknown":
            review_explicit += 1

    result = []
    for identity, cohort in sorted(cohorts.items()):
        delivered_count = len(cohort.pop("_galleries"))
        visible_count = len(cohort.pop("_visible"))
        known_count = len(cohort.pop("_outcomes"))
        explicit = cohort["positive"] + cohort["negative"]
        cohort.update(delivered_galleries=delivered_count, visible_galleries=visible_count,
                      linked_outcome_galleries=known_count,
                      no_linked_latest_outcome_galleries=delivered_count - known_count,
                      unknown_preference_galleries=delivered_count - explicit - cohort["neutral"],
                      explicit_outcomes=explicit,
                      positive_fraction_of_explicit=cohort["positive"] / explicit if explicit else None,
                      observed_positive_per_100_visible=100 * cohort["visible_positive"] / visible_count
                      if visible_count else None,
                      first_delivery_at=cohort.pop("_first"), last_delivery_at=cohort.pop("_last"))
        cohort["negative_reasons"] = dict(sorted(cohort["negative_reasons"].items()))
        result.append(cohort)
    return {
        "schema": "ranking-insights-v1", "read_only": True, "observational": True,
        "window": {"days": days, "start_utc": start, "end_utc": end, "surface": surface},
        "summary": {"delivered_events": len(deliveries), "delivered_galleries": len(all_galleries),
                    "visible_galleries": len(all_visible),
                    "visible_events": sum(c["visible_events"] for c in result),
                    "latest_feedback_galleries": feedback_count,
                    "unknown_attribution_galleries": sum(unknown.values()),
                    "unknown_attribution_reasons": dict(unknown), "cohort_count": len(result)},
        "review_milestone": {"window_attributed_explicit_galleries": review_explicit,
                             "target": 100, "review_due": review_explicit >= 100,
                             "automatic_promotion": False},
        "cohorts": result,
        "definitions": [
            "The window selects delivered impressions and latest feedback recorded within it (UTC).",
            "Only each gallery's latest feedback as of the report end is counted, through its exact impression link.",
            "Visible means a viewport-v1 confirmation. Delivered, unconfirmed and unrated are not negative feedback.",
            "Neutral outcomes and duplicate/update exclusions are separate from explicit preference outcomes.",
            "Positive per 100 visible uses only positives linked to an impression confirmed visible by feedback time, divided by distinct visible galleries; unjudged galleries remain unknown.",
            "Gallery counts across cohorts overlap and must not be added. One latest feedback outcome belongs to at most one cohort.",
            "Artifact and training time are recorded serving context; legacy context can identify the challenger rather than a separate legacy artifact.",
            "These observations are not randomized A/B results, calibrated probabilities, or formal model acceptance.",
            "The 100-label check is a selected-window review reminder, not a count since the previous assessment or an activation gate.",
        ],
    }
