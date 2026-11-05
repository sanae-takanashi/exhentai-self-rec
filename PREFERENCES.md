# Preference Evidence And Editing

`/static/preferences.html` separates explicit bootstrap preferences from learned feature
evidence. The learned table shows at most 40 features ordered by absolute weight;
its counts are training evidence, not probabilities or causal explanations.
Opening this page does not train a model, fetch galleries, or change preferences.

Select an existing preference or add a name, choose a weight from -10 to 10,
and preview the change. The preview counts stored metadata matches using the
same exact namespaced/plain-text matching as the legacy scorer. Latest feedback
is split into positive, negative, neutral, unrated, and duplicate/update outcomes.
Up to 12 recent matching examples are shown. Previous and proposed contribution
refer only to this preference's contribution per matching gallery in the legacy
score; they do not predict final rank, probability, or Review membership.

Apply writes only the selected bootstrap preference. It checks the preview
binding while holding SQLite's write lock. Changing the proposed name, weight,
or action invalidates the preview. Changes to preferences, gallery metadata,
feedback, or marks after preview return HTTP 409 and require a fresh preview.
Revision counters also catch a weight changed away and then back again.
After applying, the browser performs an independent GET and compares the saved
weight (or absence, for removal) before reporting verification.

Weight 0 pauses the explicit bootstrap contribution and its positive-search
role. It does not disable learned features with similar names. Positive bootstrap
preferences also affect future fetch query planning and the existing positive
bootstrap-match Review filter. A preference change does not switch ranking mode
or widen the personalized trial. Removing a preference is distinct from retaining
a paused preference.

After a verified apply, Restore previous weight prepares the prior value (or
removal for a newly added preference). A new preview and Apply are still required.
If an apply request has an uncertain result or its readback fails, the page does
not retry that mutation automatically. Refresh the evidence before deciding on
the next change.

## API

- `GET /api/preferences`: revision, explicit `preferences`, bounded
  `learned_features`, and scope notes. Reads use a consistent SQLite snapshot.
- `POST /api/preferences/preview`: `{tag, action: "set" | "remove", weight}`.
  Returns the normalized `proposal`, `preview_id`, `revision`, `previous_weight`,
  outcome `counts`, `examples`, previous/proposed legacy matching contribution,
  and `legacy_matching_score_delta`. Weight is ignored for removal.
- `POST /api/preferences/apply`: the normalized proposal plus `preview_id`.
  Returns the saved tag/weight and previous weight. HTTP 400 rejects invalid
  proposals; HTTP 409 rejects absent, changed, or stale preview bindings.

The endpoint handlers own commit/rollback through `db.connect()`. Apply should
receive a fresh connection so `BEGIN IMMEDIATE` acquires the write lock before
validation. Report and preview execute only reads and a read transaction.

## Verification

Run `.venv-rocm/Scripts/python.exe -B -m unittest discover -s tests -p test_preference_tools.py`.
Tests use synthetic in-memory SQLite databases with `db.SCHEMA`, with no live
database, external requests, or training. Browser checks intercept API traffic
and exercise preview, invalidation, apply, conflict handling, and readback.
