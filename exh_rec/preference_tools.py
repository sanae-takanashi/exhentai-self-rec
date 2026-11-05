"""Read-only preference evidence and preview-bound, single-preference edits."""
from __future__ import annotations

import hashlib
import json
import math

from .recommender import (
    bootstrap_exact_values, bootstrap_matches, bootstrap_search_text,
    get_bootstrap_tags, normalize_bootstrap_value,
)


class PreferenceConflict(ValueError):
    pass


def _revision(conn):
    revisions = [tuple(row) for row in conn.execute(
        "SELECT name, revision FROM cache_revisions WHERE name IN "
        "('__identity__', 'bootstrap_tags', 'galleries', 'feedback', 'gallery_marks') ORDER BY name"
    )]
    if len(revisions) != 5:
        raise ValueError("Preference revision tracking is unavailable; restart after database initialization")
    return hashlib.sha256(json.dumps([get_bootstrap_tags(conn), revisions],
                                    sort_keys=True).encode()).hexdigest()


def preferences_report(conn):
    if not conn.in_transaction:
        conn.execute("BEGIN")
    return {
        "revision": _revision(conn), "preferences": get_bootstrap_tags(conn),
        "learned_features": [dict(row) for row in conn.execute(
            "SELECT feature, weight, positive_count, negative_count FROM feature_weights "
            "ORDER BY ABS(weight) DESC, feature LIMIT 40")],
        "scope": "explicit-bootstrap-preferences",
        "learned_features_limit": 40,
        "note": "Learned feature counts describe training evidence, not causal explanations or match probabilities.",
    }


def _proposal(payload):
    if not isinstance(payload, dict):
        raise ValueError("A preference object is required")
    raw = payload.get("tag")
    if (not isinstance(raw, str) or not raw.strip() or len(raw) > 300
            or any(ord(char) < 32 for char in raw)):
        raise ValueError("A preference name of 1 to 300 characters is required")
    tag = normalize_bootstrap_value(raw.strip()).lower()
    if not tag or len(tag) > 300 or any(ord(char) < 32 for char in tag) or "," in tag:
        raise ValueError("Invalid preference name")
    action = payload.get("action", "set")
    if not isinstance(action, str) or action not in {"set", "remove"}:
        raise ValueError("Invalid preference action")
    weight = payload.get("weight")
    if action == "set":
        if isinstance(weight, bool) or not isinstance(weight, (float, int)):
            raise ValueError("Weight must be a number")
        try:
            weight = float(weight)
        except OverflowError:
            raise ValueError("Weight must be finite and between -10 and 10") from None
        if not math.isfinite(weight) or abs(weight) > 10:
            raise ValueError("Weight must be finite and between -10 and 10")
    else:
        weight = None
    return tag, action, weight


def preview_preference(conn, payload):
    tag, action, weight = _proposal(payload)
    if not conn.in_transaction:
        conn.execute("BEGIN")
    revision = _revision(conn)
    old = conn.execute("SELECT weight FROM bootstrap_tags WHERE tag=?", (tag,)).fetchone()
    old_weight = float(old[0]) if old else None
    counts = {"matched": 0, "positive": 0, "negative": 0, "neutral": 0,
              "unknown": 0, "duplicate_update": 0}
    examples = []
    rows = conn.execute("""SELECT g.url, g.title, g.title_jpn, g.category, g.uploader,
        g.tags_json, f.vote, f.reason_code FROM galleries g LEFT JOIN feedback f
        ON f.id=(SELECT MAX(id) FROM feedback WHERE gallery_url=g.url)
        ORDER BY g.last_seen_at DESC, g.url""")
    for row in rows:
        gallery = dict(row)
        try:
            gallery["tags"] = json.loads(gallery.pop("tags_json") or "[]")
        except (ValueError, TypeError):
            gallery["tags"] = []
        if not isinstance(gallery["tags"], list):
            gallery["tags"] = []
        if not bootstrap_matches(tag, bootstrap_search_text(gallery), bootstrap_exact_values(gallery)):
            continue
        counts["matched"] += 1
        vote = gallery["vote"]
        outcome = ("duplicate_update" if gallery["reason_code"] == "duplicate_update" else
                   "unknown" if vote is None else "positive" if vote > 0 else
                   "negative" if vote < 0 else "neutral")
        counts[outcome] += 1
        if len(examples) < 12:
            examples.append({"title": gallery["title"], "outcome": outcome,
                             "previous_contribution": old_weight or 0,
                             "proposed_contribution": weight or 0})
    proposal = {"tag": tag, "action": action, "weight": weight}
    preview_id = hashlib.sha256(json.dumps([revision, proposal], sort_keys=True).encode()).hexdigest()
    return {"revision": revision, "preview_id": preview_id, "proposal": proposal,
            "previous_weight": old_weight, "counts": counts, "examples": examples,
            "previous_legacy_matching_contribution": old_weight or 0,
            "proposed_legacy_matching_contribution": weight or 0,
            "legacy_matching_score_delta": (weight or 0) - (old_weight or 0),
            "note": "Matches cover stored metadata, including older and rated galleries. "
                    "This is not a prediction of final ranks or Review membership. "
                    "Weight 0 pauses this explicit preference; learned features remain active. "
                    "Positive search preferences also affect future fetch plans."}


def apply_preference(conn, payload):
    # Acquire the write lock before checking the preview: another tab must never
    # overwrite a preference changed after this user's preview.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    preview = preview_preference(conn, payload)
    if payload.get("preview_id") != preview["preview_id"]:
        raise PreferenceConflict("Preferences or evidence changed. Preview again before applying.")
    proposal = preview["proposal"]
    if proposal["action"] == "remove":
        conn.execute("DELETE FROM bootstrap_tags WHERE tag=?", (proposal["tag"],))
    else:
        conn.execute("""INSERT INTO bootstrap_tags(tag, weight) VALUES (?, ?)
            ON CONFLICT(tag) DO UPDATE SET weight=excluded.weight, updated_at=CURRENT_TIMESTAMP""",
                     (proposal["tag"], proposal["weight"]))
    return {"ok": True, "tag": proposal["tag"], "weight": proposal["weight"],
            "previous_weight": preview["previous_weight"], "revision": _revision(conn)}
