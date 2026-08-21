# Recommendation model review

A design and code audit of the ranking stack in `exhentai-self-rec`, at commit `fc7c293`.

## Scope and method

This is a **static code and design review**. The environment it ran in has no Python interpreter
and `data/recommender.sqlite3` is 0 bytes, so nothing here comes from executing the model or from
measured metrics. Every claim is anchored to `file:line`.

Each finding below was written as an assertion and then handed to an independent pass instructed to
**refute** it. Verdicts and corrections from those passes are folded in; where a claim was narrowed,
the narrowing is stated rather than hidden. Claims that could not be settled from source are marked
*unverified*, and a check you can run appears in [Verify on your machine](#verify-on-your-machine).

| File | Lines | Role |
| --- | --- | --- |
| `exh_rec/recommender.py` | 2954 | Legacy ranker, visual direction, ranking pipeline, queue gates |
| `exh_rec/personalized.py` | 762 | Calibrated logistic-regression content model |
| `exh_rec/classification.py` | 252 | Continuing-gallery ("Updates") classifier |
| `exh_rec/visual.py` | 362 | DINOv2 / canvas-fallback embedding encoders |
| `scripts/evaluate_recommender.py` | 255 | Rolling temporal evaluation harness |

## What the system actually is

There is no single recommender. There are **five models and a rule layer**, and which one decides
your queue depends on runtime conditions the UI never shows.

| Model | Entry point | Kind | Serves when |
| --- | --- | --- | --- |
| Legacy accumulator | `recommender.py:909`, `2755` | Additive feature-weight sum | Default, and whenever the acceptance gate is closed |
| Visual direction | `recommender.py:968`, `2808` | Uncentred Rocchio centroid on embeddings | Folded into the legacy score; sole ranker in Visual-only mode |
| Personalized content | `personalized.py:531` | Calibrated logistic regression over 4 feature blocks | ≥50 labels, ≥15/class, sklearn present, **and** a passing offline report on disk |
| Continuing classifier | `classification.py:132` | Logistic regression trained on manual overrides | ≥20 overrides, ≥5/class, CV balanced accuracy ≥0.65 |
| Rule layer | `recommender.py:1373-1431`, `1822`, `2076` | Prior, freshness, diversity, queue gates | Always |

`scikit-learn` is a hard dependency (`requirements.txt:2`), so the personalized model normally
trains. Whether it *ranks* is a separate question — see F3.

## My assessment

The statistical intent here is better than in most personal recommenders: rolling-origin
validation, out-of-fold calibration, an explicit accept/shadow gate, and a deliberate refusal to
treat impressions or skips as negatives. Somebody thought carefully about how feedback data differs
from ordinary supervised data, and got the shape right.

The execution has one systematic failure and one structural blind spot.

**The failure is a units problem.** The personalized model publishes a calibrated probability into
the same `score` field the legacy model filled with an unbounded sum. Constants downstream of that
field were tuned against the legacy scale and were never rescaled. Two mechanisms break on it: the
diversity penalty (0.45 per repeat, cap 1.5, applied to a value in `[0,1]`) and the exploration
mixer's `score >= 1.0` admission test. Both fail silently, in the direction of "the feature stops
working."

**The blind spot is that nothing measures the thing that is served.** The evaluation harness scores
`score_personalized_galleries` output directly and never calls `recommend_page`. Everything between
the model and the user — the post-hoc bootstrap prior, diversification, exploration, freshness, and
the gates that delete candidates — is outside every metric in the repository. The `ranking_scores`
argument that would let the harness measure a different ordering than the probability is passed only
for the legacy comparison; for the personalized model it is `None`
(`scripts/evaluate_recommender.py:167`), so ROC-AUC, NDCG@20, and precision@10 all rank by `p`
itself.

Those two combine into the practical risk. The scale bugs sit **behind** the acceptance gate, and
the gate is closed unless you manually ran a script. So today they are latent; the day the gate
opens, ranking activates already broken, and no number in the repo would move.

The single most valuable change in this document is not a bug fix. It is making the harness evaluate
`recommend_page`.

## Findings

Severity is impact on one person running this tool. Ranked.

### F1 — The probability-aware diversifier is unreachable — `high` · CONFIRMED

`personalized.py:14` sets `MODEL_SCHEMA = "personalized-content-v2"`; the version string is built at
`personalized.py:557`, assigned at `:565`, and reaches ranked items via `:680` and
`recommender.py:1411`. The dispatch at `recommender.py:2662` tests for `v1`:

```python
if any(item.get("model_version", "").startswith("personalized-content-v1") for item in scored):
    return diversify_probability_ranked_galleries(scored)
```

No v2 version can satisfy it. `git log -S 'personalized-content-v'` returns only `fc7c293`, so a v1
model never existed — the string was wrong from the start. Every personalized ranking therefore
falls through to the legacy greedy path (`2664-2686`), which subtracts
`min(1.5, repeats * 0.45)` (`recommender.py:39`, `2719-2721`) from `item["score"]`, which
`recommend_page:1412` has just set to the calibrated probability. The prior's clamp
(`recommender.py:1453`) keeps that value strictly inside `(0,1)`.

`repeats` is the **sum of prior occurrences across the item's keys**, not a count of distinct shared
keys — so one uploader already shown three times contributes 3. Domination therefore begins at
**3 repeats** (1.35 > the maximum possible probability), not 4; 4 merely saturates the cap. Even one
repeat costs 0.45, more than the useful spread of a calibrated model.

The correct function, `diversify_probability_ranked_galleries` (`2689`), scales the same penalty by
`0.03` and confines reordering to a 0.08 probability window. It is unreachable from production; its
only caller is the dispatch that can never fire.

**Precondition:** hybrid mode (the default, `app.py:2988`) with an artifact that is both `ready` and
`accepted` (`recommender.py:1404`). Until then items carry `legacy-linear-v1` and legacy-scale
scores, where 0.45 is proportionate and the dead branch is harmless. The bug is latent, and
guaranteed on activation.

**Why it survived:** `tests/test_personalized.py:144-146` pins the literal string
`"personalized-content-v1-x"` and calls the inner function directly. No test anywhere calls
`diversify_ranked_galleries`, the dispatcher. The suite exercises and asserts the dead branch.

### F2 — The evaluation harness measures a quantity nobody is served — `high`

`evaluate` (`scripts/evaluate_recommender.py:130`) calls `score_personalized_galleries` and computes
metrics from `like_probability`. It never calls `recommend_page`. So the post-hoc bootstrap prior
(F12), the diversity stage (F1), the exploration mixer (F7), the freshness bonus, and the
candidate-removing gates (F13) are all outside the measured system. F1 destroys the served ordering
and would not move a single number in this report.

Compounding problems, each verified:

- **`ranking_scores` is only passed for legacy.** At `:167` the personalized call omits it, so
  ranking is `p` itself. The three ranking gates in `acceptance()` — ROC-AUC gain ≥ 0.05, NDCG@20
  not lower, precision@10 not lower — plus `ece <= 0.10` are all computed on an ordering the user
  never receives. (`brier` is computed at `:89` but is not a gate.)
- **Leakage through gallery state.** `restrict_temporal_training_data` (`:54`) truncates `feedback`,
  `gallery_marks`, and `hath_downloads` — not `galleries`. Every fold trains and scores against
  present-day metadata: current `rating`, current tags, current `detail_fetched_at`, current
  embeddings. A gallery that was list-only when you rated it now presents a full tag set. This
  inflates both models relative to live behavior and specifically invalidates the `detail_ready` /
  `visual_ready` segment breakdown (`:181`), whose entire purpose is to measure that difference.
  `tag_corpus_strengths` likewise computes IDF over the full present-day corpus.
- **It authorizes a different model than it measures.** Marks are deleted outright rather than
  time-filtered, so the evaluated model trains without the `±3.0` mark signal production uses.
- **Selection bias in the test set.** `latest_feedback` (`:32`) selects only galleries with
  `vote != 0` — items you chose to rate, which are largely items the ranker already put near the
  top. `precision_at_10` over that window is not the precision of the queue.
- **Correlated folds reported as independent.** Starts at 45/55/65/75/85% with `window = 100`
  overlap heavily; `summarize` (`:199`) takes a plain mean with no variance or interval, so five
  near-duplicate folds read as five observations.
- **One weak baseline.** Legacy is F9's saturating accumulator, so calibration comparisons against
  it are trivially won. There is no random, recency, site-rating, or tags-only baseline — nothing
  that would reveal a model beating a broken incumbent while losing to `ORDER BY rating`.
- **No ranking-side metrics.** Diversity, novelty, coverage, and catalog concentration are core
  mechanisms here and are entirely unmeasured.

### F3 — Nothing tells you which model is ranking, and the gate cannot be tested — `high`

`personalized.py:558`:

```python
accepted, acceptance = evaluation_acceptance() if _is_file_database(conn) else (True, {"source": "test"})
```

`evaluation_acceptance` (`:688`) reads `data/recommendation-evaluation.json` (`:36-41`) and returns
`False` on `OSError`. The only writer is `scripts/evaluate_recommender.py --output`, a manual
command; `.gitignore:8` excludes `data/`. `recommender.py:1404` and `1504` both require `accepted`.

Three distinct problems, in order of how much they cost:

1. **The state is unobservable.** `grep` over `static/app.js` finds no reference to
   `personalized_model`, `model_fallback`, `validation`, `mean_auc`, or `folds`; the classifier's
   status (`continuing_classifier`, `balanced_accuracy`) is equally absent. The API returns all of
   it (`app.py:278`); the shipped frontend renders none of it. The only model-derived thing on a
   card is `match N%` (`static/app.js:2114`), which is displayed identically whether it came from a
   calibrated classifier or from `sigmoid(unbounded sum)`. **You cannot tell which of five models
   ranked your queue, or that a fallback happened at all.**
2. **The gate is untestable by construction.** On in-memory databases `accepted` is forced to
   `True`. Every test uses an in-memory database, so no test exercises the gate in either state —
   and the ECE metric that produces the gate has no test either. Zero coverage on both sides.
3. **A pass never expires.** `evaluation_acceptance` compares only `report["model_schema"]`. Not the
   data signature, not the sample count, not the age or mtime. An artifact reloaded from
   `MODEL_PATH` also keeps its stored `accepted=True` with no re-check until the signature changes
   (`personalized.py:638-644`).

Wasted compute is real but conditional: `score_personalized_galleries` returns early only when the
artifact is *not ready* (`:654`). When it is ready-but-unaccepted it runs the full transform, the
classifier, all 8 bootstrap models, per-row explanations, and the modality-disagreement calculation
before `recommender.py:1404` discards the result — on every hybrid request, including
`app.py:3133 queue_counts_payload`, which passes `limit=1` and still scores the whole pool.

*Narrowed by verification:* the fallback itself is graceful — `legacy_prediction_fields`
(`recommender.py:1462`) fills every field, discovery returns items tagged `"legacy fallback"`, and
nothing crashes. In a **fresh** checkout the gate is not even reached: with 0 labels the artifact is
not `ready`, so `:1404` fails on `ready`, not `accepted`. The gate only bites in a populated
install whose operator never ran the script. And the report *is* re-read on every retrain, so
deleting it does revoke acceptance — what is missing is any age or size check, not re-reading.

The gate is the right idea, and `classification.py:168-175` shows the project already knows how to
do it better: it computes acceptance **in-process** from cross-validated accuracy and marks the
model `"shadow"` when it fails. The personalized model's dependence on a manually produced file is
the defect, not the gate.

### F4 — The model's trained surface is effectively untested — `high`

- `tests/test_recommender.py` (2012 lines) contains **zero** references to `personalized`,
  `model_version`, `like_probability`, `rank_score`, or `uncertainty`. It tests only the legacy
  scorer.
- `tests/test_app.py` (2586 lines) contains **zero** references to the same set. Its only
  model-related lines are a `recommend_model_mode` setting round-trip at `:2330-2349`.
  `like_probability` appears in no test file except `tests/test_personalized.py`.
- The entire trained-artifact surface — calibration, C selection, bootstrap uncertainty,
  `_explain_row`, `_modality_disagreement`, the `accepted` gate, and
  `apply_bootstrap_probability_prior` — rests on **one** test,
  `test_sklearn_model_outputs_calibrated_fields_when_available`
  (`tests/test_personalized.py:160`). It self-skips when sklearn is missing, it trains at exactly
  `count = 50` — the one label count where the calibrator is `None` (F6) — and it asserts only that
  three probabilities lie in `[0,1]` and that a string field is non-empty. **The test named
  "calibrated" tests an uncalibrated model.**
- `apply_bootstrap_probability_prior` has exactly two references in the repo: its definition and
  its single call site. No test.
- Guarding is inconsistent: `tests/test_classification.py:46` calls `train_continuing_classifier`
  with no skip guard, so a sklearn-less run produces a **red failure** there and a silent skip in
  `test_personalized.py`.
- `joblib`, `numpy`, and `scipy` appear in neither requirements file — they are relied on as
  transitive dependencies of scikit-learn. There is no `.github/`, `conftest.py`, `pytest.ini`,
  `pyproject.toml`, or `setup.cfg` anywhere, so nothing pins or verifies the environment.

This is the root enabler for F1: a wrong version string survived because nothing ranks a real
trained model end to end.

### F5 — Negative-reason feature scaling leaks the label and skews train/serve — `medium`

`training_examples` (`personalized.py:207`) is the only writer of `active_reason_code`, and every
code in `NEGATIVE_REASON_CODES` (`:20-29`) describes a reason for disliking. The flag then multiplies
feature values by `1.35` in three places: tag and creator metadata (`:304-308`), four numeric
features (`:323-341`), and the **whole L2-normalized visual vector** (`:359-360`). At inference no
candidate carries a reason, so the scale is always `1.0`.

The harm is sharpest where the verifiers found it: `_select_c` (`:447`) and `_fit_calibrator`
(`:470`) both fit on the scaled matrix, so **the C hyperparameter and the logit→probability map are
estimated on a distribution the serving path never produces**, then applied to unscaled serving
logits. `_explain_row` (`:709`) multiplies those coefficients by unscaled values, so the displayed
reason magnitudes inherit the distortion.

*Narrowed by verification, in four ways:*

- **It is gated.** `:240` sets the flag only when that specific reason has ≥20 negative feedback
  events. Below the threshold there is no scaling and no skew at all. (The README documents this.)
- **Only 6 of the 8 codes can scale anything.** `duplicate_update` rows are dropped before becoming
  examples (`:232`), and `other` matches no scaling site.
- **The `visual_image_*` scaling is harmless.** At `:340-341` the multiply happens *inside* the
  clamp, so values stay in `[0,1]` — a range serving rows occupy. The genuinely
  unreachable-at-serving values are tag/uploader entries of exactly `1.35` and a visual block of
  norm `1.35`.
- **It does not inflate reported metrics.** The evaluator builds test rows via `gallery_payload`
  (`scripts/evaluate_recommender.py:43`), which never sets the flag. The harm is a distorted model,
  not fake-good numbers.

**A separate defect surfaced here:** the "reason codes are negative-only" invariant is a
*client-side convention*, not enforced. `static/app.js:1615` sends a reason only when the vote is
negative, but `record_feedback` (`recommender.py:453`) applies no sign gate, and the import path
(`:762-775`) accepts arbitrary `vote`/`reason_code` pairs. A single positive vote carrying `quality`
becomes a scaled **label-1** row once that code clears 20 negatives.

### F6 — The calibrator is absent for the first 30 labels past the threshold — `medium`

```python
window = max(20, min(100, count // 5))
starts = sorted({max(MIN_LABELED, int(count * f)) for f in (0.5, 0.65, 0.8)})
starts = sorted({*starts, max(MIN_LABELED, count - window)})
return [(start, min(count, start + window)) for start in starts if start < count]
```

At `count == MIN_LABELED == 50` — the exact point training becomes eligible — every candidate start
evaluates to 50 and the `start < count` filter empties the list. Then:

- `_select_c` records `mean_auc: 0.0, folds: 0` for all four C values and returns **`0.05`**, not
  the `0.2` initializer, because `best_auc` starts at `-1.0` so the first grid value wins. The blind
  fallback is the *strongest* regularization in the grid, and the `0.2` default is dead code on this
  path.
- `_fit_calibrator` collects nothing, returns `None`, and `_calibrated` falls back to a raw sigmoid
  of an untuned decision function.

The verification pass established something stronger than the original claim: **the calibrator is
deterministically `None` for every count from 50 through 79.** For `count < 100` the start `50` is
always present and all test indices lie in `[50, count)`, so the pooled calibration set has at most
`count - 50` members; at 79 it is exactly 29, one short of the `< 30` guard. **The first calibrated
model appears at 80 labels.** Between 50 and 79 the served `like_probability` is an uncalibrated
sigmoid.

Fold structure in that band: 0 folds at 50 and 51 (the single `(50, 51)` pair is dropped by the
two-class guard), exactly one fold with 2–13 test examples from 52 to 63, two from 64 up. So the
`validation` array is a best-of-four selection statistic on a handful of points.

*Narrowed:* `folds: 0` **is** recorded in the artifact and persisted to `model_training_runs`
(`:625`), so the degeneracy is written down — it is just never rendered (F3). And the affected band
self-heals as labels accumulate.

### F7 — Two more legacy-scale constants misread probabilities — `medium`

F1 is not isolated; the same units error appears twice more.

- **Exploration goes inert.** `mix_bootstrap_exploration` (`:1700-1701`) admits an item only if
  `score >= MIN_BOOTSTRAP_EXPLORE_SCORE`, and that constant is `1.0` (`:49`). In hybrid mode `score`
  is a probability strictly below 1.0, so the pool is always empty and the function returns `scored`
  unchanged. The companion predicate passes — `personalized_reasons` appends `"bootstrap prior"` —
  so the score threshold is the sole blocker. *Narrowed:* observable only when
  `bootstrap_explore_count > 0`, and the default is `0` (`app.py:198`), so this is a feature that
  cannot be turned on rather than one that breaks.
- **Recency leaves the ranking entirely.** `recommender.py:1412` overwrites `score` with
  `rank_score`, discarding the freshness bonus computed at `1385-1393`. The personalized feature set
  has **no recency or age feature** (`_numeric_features`, `personalized.py:312-342`), so freshness is
  not relocated into the model — it is simply gone. *Corrected:* the ignored user-tunable setting is
  `preview_freshness_weight` (default **8.0**, worth up to +2.0 of legacy score); the review view
  hardcodes `freshness_weight=1`.

*Corrected:* the mark and vote adjustments at `1373-1384` are **not** discarded in the default path
— they are unreachable there, because `include_rated=False` filters rated, marked, and downloaded
rows out in SQL (`:1290`) and skips any survivor at `:1362`. The discard only matters in the
`include_rated` preview view.

A related smell: `score` changes both meaning and precision depending on branch — 3 decimals of an
unbounded sum at `:1394`, 6 decimals of a probability at `:1412` — and the UI falls back to
displaying raw `score` when `like_probability` is absent, mixing two scales across modes.

### F8 — Discovery samples from the top 100 — `medium` · CONFIRMED

`discovery_page` (`:1477`) computes `pool_limit` up to 10000 (`:1488`) and passes it as **`limit`**.
`recommend_page:1267` clamps `limit = max(1, min(100, int(limit)))` and slices
`scored[offset:offset+limit]` at `:1431`. So `candidates` is at most **100 items, and they are the
first 100 of the post-diversification order**.

The uncertainty sort (`:1514`), disagreement sort (`:1522`), and coverage sort (`:1526`) all run over
that set. With the default `recommend_candidate_limit` of 2000, the exploration reranking sees 100 of
2000 rows, and inclusion in the window is uncorrelated with uncertainty. *Narrowed:* a mid-probability
item can still enter as its diversity cluster's champion — precisely because of F1 — so "genuinely
uncertain items can never be surfaced" is too absolute; ~1900 of 2000 are simply unreachable.

The coverage third is worse than stated. Its key is
`min(seen_interest_count(item, other) for other in candidates if other is not item)` (`:1529`) — a
minimum over ~99 others. It is `0` unless an item shares a diversity key with *every* other
candidate, and trivially `0` for any item with no uploader and no artist/group/parody/character tag.
So the key is ~always 0 and that third of the quota is the seeded daily tie-break alone. Diversity
is not the cause; the `min` is.

Two further defects in the same function, both surfaced by verification:

- **Hard-capped at 100 with no signal.** `/api/discovery` accepts `offset` up to 10000
  (`app.py:224`), but `len(selected) <= 100`, so any offset ≥ 100 returns an **empty page** while
  `total` and `has_more` never exceed 100. Worse, `pool_limit = max(candidate_limit, limit+offset,
  100)` means a large offset inflates the SQL scan up to 10000 rows and scores all of them to return
  nothing.
- **Pagination is not prefix-stable.** `total_needed` and `quotas` (`:1533-1534`) both depend on
  `offset`, so the list built for `offset=40` is not an extension of the one built for `offset=0`.
  Items shown on page 1 can reappear on page 2 and others can be skipped, even though the daily seed
  keeps the underlying sorts stable.

The only test (`tests/test_personalized.py:178`) passes `candidate_limit=100`, so the truncation
never manifests and nothing pins it.

### F9 — The legacy accumulator's ratchet and tag-count bias — `medium`

`apply_feedback_features` (`:1126`) accumulates
`weight += signal * 0.35 * multiplier * strength` with no division by example count, no decay, and no
bound; the only writes to `feature_weights` are that upsert and the `DELETE` at `:910`. `score_gallery`
(`:2773-2779`) then sums matched weights over every feature a gallery has. The signal is itself
pre-multiplied by `feedback_confidence` (up to 1.45× for a consistent streak), so the per-update
magnitude is larger than the formula suggests.

*Substantially narrowed by verification — three of my four sub-claims needed correction:*

- **Uploader is not ratcheted.** `is_identity_feature` matches `uploader:` (`:1157-1163`), so a bare
  thumbs-down *does* decrement uploader weights, pinned by `tests/test_recommender.py:619`. Only
  `category:*` and non-identity tags escape a bare thumbs-down.
- **The ratchet requires a specific usage pattern.** It holds only under thumbs-only voting with no
  flips and no bans. A star score ≤2 decrements broad tags and category; a ban mark decrements
  everything (retrain passes `score=1` with `BAN_MARK_SIGNAL = -3.0`); and because weights are
  rebuilt from scratch over only the latest row per gallery, flipping or clearing a vote removes the
  earlier positive contribution. All three are test-pinned.
- **The IDF correction is stronger than it looks.** `tag_corpus_strengths` clamps per-tag strength to
  `[0.55, 1.2]`, but that strength multiplies **both** the learning update (`:1126`) and the scoring
  term (`:2777`), so effective contribution scales with strength² — 0.30 for a common tag versus 1.44
  for a rare one, a **4.7× spread**, not 2.2×. It is inactive below 10 galleries.
- **Saturation is a display bug, not a ranking bug.** `legacy_prediction_fields` sets
  `rank_score` to the *raw* score and the sort keys on `rank_score`, and the sigmoid is monotone, so
  ordering is unaffected. What breaks is what you see: `static/app.js:2114` renders
  `match ${round(p*100)}%`, so in the legacy regime the user sees **"match 100%" on essentially
  every card**. Meanwhile the short-repeat, continuing-update, reaction-history, and marked pages
  never attach `like_probability` at all and show raw scores — two incompatible numbers under the
  same visual treatment.

What survives intact is the **tag-count direction plus the enrichment loop.** No normalization by
feature count exists anywhere, and `select_detail_candidates` (`app.py:2696-2726`) spends its ≤8
detail fetches per run on exactly the highest-scoring unenriched galleries under this same
`score_gallery` — with no visual model, so enrichment ignores visual preference entirely. Detail
fetches only ever *add* tags (`exhentai.py:740-743`), which add scoreable features. Being ranked
highly is what makes a gallery rank highly.

A downstream casualty: `select_recommendation_detail_candidates` (`app.py:2745-2753`) ranks by
`0.55*like_probability + 0.30*uncertainty + 0.15*text_visual_disagreement`, but legacy sets
`uncertainty` to `None` and `text_visual_disagreement` to `0.0`. Once legacy probabilities saturate,
**all three terms are constant and Enrich prioritization collapses to an alphabetical URL
tiebreak.**

### F10 — Per-vote and per-request cost — `medium`

A single `/api/feedback` POST does all of this before the response is sent:

- `model_snapshot` **before** the vote (`app.py:389`), which reaches `load_personalized_model` with
  `train_if_needed=True` — so on a cold cache or stale signature **it can run a full train before
  the vote is even inserted**, discarded moments later.
- `retrain_model` (`recommender.py:479`) — `DELETE FROM feature_weights`, replay of all latest
  feedback, marks, and completed downloads, with `feedback_confidence` issuing **one query per
  labeled row** (N+1), then `train_personalized_model`.
- `model_snapshot` **again** (`app.py:406`), which also calls `visual_preference_model` — repeating
  the same N+1 pattern, twice per vote.
- A full `response_page_payload` re-rank.

`train_personalized_model` performs **9 to 29 `liblinear` fits**, not a fixed number: 0–4 folds ×
4 C values, plus per-fold calibrator refits, plus the final classifier, plus 8 bootstrap models. At
`count = 50` it is exactly 9 (1 + 8). Adding it up, a single vote runs the whole-table
`GROUP_CONCAT` + `galleries` length scan roughly **five to six times**.

Reads are expensive too, and **index coverage is not the problem** — every expected index exists
(`db.py:194-205` plus PK autoindexes). The cost is four unfiltered scans no index can help:

| Work | Site | Note |
| --- | --- | --- |
| `SELECT tags_json FROM galleries` + `json.loads` per row | `recommender.py:1166` | The `total < 10` early-exit is checked *after* the whole table is in memory |
| `SELECT gallery_url, MAX(id) FROM feedback GROUP BY gallery_url` | `recommender.py:1300` | No WHERE; materialized fresh per call |
| The **same** group-by again | `recommender.py:974` | `visual_preference_model` duplicates it in the same request |
| 6 aggregates, 5 unindexed, incl. `GROUP_CONCAT` over all feedback | `personalized.py:90` | Computed **before** the cache is consulted (`:634-635`), so it runs on every cache hit |
| Union-find over every gallery | `recommender.py:1822` | `continuing_update_series_index`, per request |

`feedback_confidence`'s index is also mismatched: `idx_feedback_gallery` is
`(gallery_url, created_at DESC)`, but the query orders by `id DESC`, so the matched rows must be
sorted. And `review_excluded = 0` plus the three `IS NULL` join predicates are unindexed residuals,
so the `LIMIT` cannot prune before the joins are computed.

`GET /api/queue-counts` calls `recommend_page` with `limit=1` and pays this entire cost, because
`recommend_page` scores the whole candidate pool before slicing.

**Concurrency hazard:** the server is a `ThreadingHTTPServer` (`app.py:3787`) with no lock on the
feedback path — `FETCH_LOCK` is held only by fetch and enrich. `retrain_model` opens with
`DELETE FROM feature_weights`, so two overlapping votes can interleave a wipe with the other's
accumulation. `_MODEL_CACHE` (`personalized.py:42`) is likewise an unlocked module dict, so two
concurrent misses can both run the full fit and both call `_atomic_dump`. *Unverified:* whether
SQLite's default busy behavior turns this into an error or a lost update — no busy-timeout is set in
`db.py:228-236`.

Wall-clock cost is unverified; there is no benchmark in the repo.

### F11 — Signature churn forces retrains that cannot change the model — `medium`

`model_data_signature` (`:90`) folds `galleries.count`, `text_size`, `detailed`, `visual`, and the
visual-image count into the key. `training_examples` draws rows only from labeled galleries, marks,
and completed downloads, and `_fit_vectorizers` fits on `examples` only — so no corpus statistic
from unlabeled rows enters the model. Storing one new unlabeled gallery changes the key without
changing anything fittable, missing `_MODEL_CACHE`, rejecting the joblib file on signature, and
falling through to a full retrain.

*Narrowed:* the request that pays is usually the background fetch thread, not a user request — and
`fetch_and_store` retrains only `if enriched`, so a fetch that stores galleries without enrichment
changes the signature without retraining, deferring the cost to whoever loads the model next.
`model_training_runs` therefore grows one row per **spurious** retrain rather than one per genuine
model change. The failure and insufficient-data paths also write to `_MODEL_CACHE` without the
`clear()` the success path performs, so status entries for superseded signatures accumulate for the
process lifetime.

The `GROUP_CONCAT` at `:123-129` has no `ORDER BY`; SQLite scans rowid order in practice, so this is
contractual rather than observed.

### F12 — The post-calibration bootstrap prior — `medium`

`recommend_page:1406-1408` applies `apply_bootstrap_probability_prior` (`:1452`), which adds
`clamp(sum(matched bootstrap weights) * 0.08, ±0.45)` in logit space and overwrites both
`like_probability` and `rank_score`. The evaluator reads `like_probability` straight from the model
without the shift and gates on `ece <= 0.10` from those unshifted values. The shifted value is what
the UI shows, what is logged to `recommendation_impressions`, and what drives diversification and
detail prioritization.

*Bounded, and narrower than it sounds:* it diverges only for galleries matching at least one
configured bootstrap tag — with no match it is an identity within rounding, and `bootstrap_tags` has
no default seed. `±0.45` in logit space is at most ~0.112 absolute probability change (maximum near
p = 0.5), and the clamp is only reached when matched weights sum to ≥5.625, versus a default weight
of ±1.0.

Two related defects: `personalized_reasons` (`:1613-1617`) appends `"bootstrap prior"` whenever any
bootstrap tag matches, **including when the matched weights are negative**, so a gallery the prior
pushed *down* shows a reason string that reads as positive. And the prior is defensible in
principle — bootstrap tags are a real prior and logit space is the right place for one — it just
belongs inside the model, where evaluation can see it.

### F13 — The continuing-gallery gate — `medium`

`classification._examples` (`:66`) draws its entire training set from
`gallery_classification_overrides` — only galleries the user manually relabeled. The UI offers only
"Mark as Updates" from Review and "Move to Review" from Updates, so the two classes are largely the
heuristic's false negatives versus its false positives, and no feature encodes the heuristic's own
output — the sampling bias cannot be conditioned away. The model is then applied to every candidate.

Validation is `StratifiedKFold(shuffle=True)` over rows ordered by `updated_at` — a **random split of
a time-ordered stream**, unlike the rolling temporal validation used everywhere else in the project.

**The concrete leak:** `_fit_matrix` is called on **all** items (`:151`) *before* the splits are made
(`:156`), so the TF-IDF `idf` weights and the `min_df=2` vocabulary are fit on train and test
together in every fold. The 0.65 balanced-accuracy gate reads a number inflated by its own
preprocessing.

*Substantially narrowed by verification:*

- **It does abstain**, at two levels: no decisions at all unless the artifact is `ready` and
  `accepted` (`"shadow"` otherwise), plus the per-item `max(p, 1-p) >= 0.80` gate. The defensible
  claim is that the threshold is **uncalibrated and may rarely bind**, not that abstention is absent.
- **"Nearly everything clears 0.80" is unverified**, and there are countervailing facts: `C=0.5` is
  half sklearn's default and bounds `‖w‖`; TF-IDF's `l2` norm bounds `‖x‖`; `min_df=2` removes every
  n-gram appearing in only one of a handful of titles; the `ValueError` fallback can drop the title
  block entirely; and the intercept plus `bias` feature under balanced weights puts feature-poor
  unseen candidates near p = 0.5, where the gate *does* abstain.
- **It is a visible re-route, not a silent deletion, on the default path.** The same URL is injected
  into the Continuing Updates page with reason `"learned classification"`, the card shows
  `Learned classification: updates · N% confidence` with a **Move to Review** button, and the queue
  counts shift.

The trace *is* genuinely lost in three narrow cases, and these are the real bugs:

1. **Manual overrides are ignored in some views.** `overrides` is set to `{}` at
   `recommender.py:1289` whenever `include_rated=True` or `exclude_short_repeats=False`, so a
   gallery you explicitly marked "review" is still skipped by the learned decision — and
   `continuing_update_page` honors the override and excludes it, so in the preview view it
   disappears with no trace at all.
2. **Rated galleries have no surface.** The skip at `:1350-1353` is not gated on `include_rated`,
   while `continuing_update_page` drops rated galleries.
3. **The two pages truncate different windows.** Review orders by `last_seen_at DESC`; Updates by
   `COALESCE(posted_at, last_seen_at) DESC`. Under the same `candidate_limit`, a gallery inside the
   Review window but outside the Updates window is skipped from Review and never appears in Updates.

No audit trail exists for any of it: `model_training_runs` is written only by the personalized model,
impressions record only items actually shown, and the classifier's status is returned by the API but
rendered nowhere (F3). *Unverified:* a URL-form fragility — decisions are keyed by raw URL but looked
up normalized, and `galleries.url` is built without lowercasing the hex token, so an uppercase token
would break both the training join and the Updates trace.

### F14 — The visual preference direction — `medium`

`normalize_embedding` (`visual.py:31`) divides by the L2 norm and never subtracts a mean;
`grep -n "mean"` over the visual path returns nothing, and no corpus mean is ever computed or stored.
`visual_preference_model` (`:968`) accumulates `vector_sum[i] += value * signal` and divides by the
norm — an uncentred weighted difference of unit vectors.

Writing `x_i = m + r_i`, the sum is `(Σs_i)·m + Σs_i·r_i`: the shared-mean term scales with the
**net** signal while residuals partially cancel, so an upvote-heavy history leaves a mean-dominated
direction. *Corrected, and this matters:* the consequence is **not** a rank-preserving offset.
`x·m` varies with how typical a cover is, so a mean-dominated direction **tilts ranking toward
corpus-typical covers** while attenuating the discriminative component. That is a distortion, not a
constant. The magnitude is *unverified* — the narrow-cone premise cannot be measured here.

- **The `confidence` term cannot do what its name says.** `confidence = min(1.0, max(0.35,
  total_weight / 3.0))` (`:2820`) is a **per-pass global scalar**: it can never reorder embedded
  candidates among themselves, only rebalance the visual term against the other score components.
  *Corrected:* saturation depends on signal magnitude, not vote count — a single favorite or ban
  mark (±3.0) saturates it with zero votes — and it is not flat before that (0.35 at one vote, 0.667
  at two).
- **Mixed-version corpora arise silently, not only from a deliberate encoder switch.**
  `static/app.js:286-292` falls back to the client-side 8×8 canvas embedding on **any** DINOv2
  failure, and `active_visual_version` (`:1077`) picks one version from the **rated rows only**. So
  one rated DINOv2 gallery can pin the model to DINOv2 and zero out the rest of the corpus.
- **Mismatch handling is inconsistent across modes.** `score_gallery` treats a version mismatch as
  `0.0`, i.e. neutral — which, given a mean-dominated direction where same-version cosines are
  broadly positive, is a systematic **relative penalty**, not neutrality. In Visual-only mode the
  opposite happens: `score_visual_gallery` returns `None` and `recommend_page:1334-1336` drops the
  gallery from ranking entirely. In `personalized.py`, `_visual` (`:349`) returns a **zero vector**
  on dimension mismatch while the `visual_ready` feature (`:312`) only checks that a list exists — so
  those rows advertise `visual_ready = 1.0` beside an all-zero vector, and switching encoders creates
  a cohort whose feature pattern encodes *when* it was embedded.

*Narrowed:* the visual term drops out of the main ranking entirely when the personalized model
serves (`:1412` overwrites `score`), and the four other `score_gallery` callers never sort by score,
so there it only changes a displayed number.

### F15 — Positive-only implicit signal and a closed query loop — `medium`

Completed H@H downloads become positive labels for galleries with no feedback and no mark, at weight
up to 2.0 (default 1.25). **"Complete" means a file named `galleryinfo.txt` exists**
(`hath_observer/core.py:418-420`), case-insensitive, never compared against an expected page count.
`status='completed'` is reachable by first observation of a pre-existing directory (including under
`--backfill`), an incomplete→complete transition, a `failed`→`completed` overwrite (`failed_at` is
retained but never consulted), a partial-then-retried job, or **any authenticated POST** carrying
`type: "download.completed"` and a numeric gid on a shared per-installation token. There is no
attribution check of any kind: nothing reads `downloaded_files`, `total_files`, or dwell time. The
only filters are `NOT EXISTS (feedback)` and `NOT EXISTS (gallery_marks)` — de-confliction with
explicit signals, not evidence you liked it.

`hath_download_signal_weight` has **no branch in `save_settings`**, so the channel is on at 1.25 by
default and cannot be turned down through the UI or API — only by a direct database write.
(Duplicate rows are handled: the PK is `(client_id, gid, resolution)` so re-downloads create extra
rows, but training's `GROUP BY d.gallery_url` collapses them.)

There is no negative implicit signal anywhere. Skips map to `0.0` and are excluded from training.

On the other side of the loop, `build_query_plan` (`app.py:2801-2843`) is exactly
`recent` + up to 6 positive bootstrap tags + up to 6 top-positive learned tags, with **no sampling,
jitter, temperature, epsilon, or novelty term**. Nothing measures a query's yield: `fetch_runs` rows
are display-only, and `source_query` is read solely by the exploration mixer. **A learned query that
returns nothing useful keeps being issued every refresh** until the coefficient ranking happens to
shift, and the only removal path is manually adding a negative bootstrap weight.

So the loop closes: model favors tag → fetcher searches tag → more such galleries enter the pool →
you download some → those become positives → coefficient grows. The counterweight is explicit
downvoting, which F9 shows cannot decrement broad content tags under thumbs-only use.

**Inconsistent gating:** `positive_query_tags` checks `ready` and **not** `accepted`
(`personalized.py:749`). A model forbidden from ranking still steers what gets fetched from the site.

### F16 — Smaller items — `low`

- **Marks overwrite richer feedback.** Marks are applied last into the same `examples[url]` dict, so
  a mark replaces a graded score with a hard 1/0 at weight 3.0 — and the mark's `updated_at` becomes
  the example's temporal position, so re-marking an old gallery moves it to the end of the training
  timeline. A gallery with both negative feedback and a mark loses its reason code entirely.
- **Reason activation counts superseded rows.** The ≥20 query counts all historical negative
  feedback, including rows no longer the latest label for their gallery, while `training_examples`
  keeps only the latest. A code can activate on votes that no longer exist in the training set.
- **Double imbalance correction.** `class_weight="balanced"` is computed from *unweighted* class
  counts and then multiplied by per-sample weights spanning 0.75–3.0.
- **No cross-block scaling.** One `C` regularizes one-hot metadata, sublinear-TF char n-grams, and a
  dense unit-norm 384-vector identically. L2 penalties are scale-sensitive; the blocks are not on
  comparable scales.
- **The disagreement measure includes the intercept.** `_modality_disagreement` (`:727`) compares a
  content logit containing the `bias` feature against a visual logit that has none, then divides by
  an arbitrary `6.0` — and it drives a third of the Discovery quota.
- **Impressions are write-only.** `record_impressions` stores position and probability; nothing reads
  the table. The data needed to measure the top of the queue is collected and discarded.
- **`clear_feedback` retrains even when the `DELETE` matched zero rows**, while `clear_gallery_mark`
  is correctly guarded on `rowcount`.
- **The legacy discovery fallback mutates shared dicts** (`:1506-1508`) instead of copying, unlike
  the main path.

## What is good

Worth stating plainly. Several of these are things most personal recommenders never attempt.

**The statistical design is right, even where the code is wrong.**

- **Rolling-origin validation over random k-fold.** Choosing prequential evaluation for feedback data
  shows the problem was understood. F6 breaks it at small `n`; the design is correct.
- **Calibration is fit out-of-fold and applied to the full model** — the standard correct pattern —
  with a recency weight (`CALIBRATION_RECENCY_DECAY`) that privileges recent taste.
- **The accept/shadow gate is production-grade thinking**, and `classification.py:168-175` already
  implements the better in-process version of it.
- **Uncertainty is estimated at all**, via bootstrap ensembles, and drives a dedicated exploration
  surface.

**The label semantics avoid the classic self-poisoning traps.**

- **Impressions are explicitly not negatives**, and the README says so. Treating un-clicked
  recommendations as dislikes is the most common way a personal recommender poisons itself.
- **`duplicate_update` feedback is excluded from preference training** — correctly separating "this
  is a duplicate" from "I dislike this."
- **Neutral means neutral.** A skip records score 3 → signal `0.0`, contributes no weight, and on a
  never-rated gallery does not even trigger a retrain.
- **Feedback is append-only** and training reads the latest row per gallery, so history survives a
  changed mind — and because weights are rebuilt from scratch, a flip genuinely removes the old
  contribution.

**The model is legible, and that is a feature.** In a single-user system the user *is* the labeler.
`feature_weights` is a readable table, `model_snapshot` surfaces top positive and negative weights,
and per-item reasons appear on every card. **Namespace-aware weighting**
(`FEATURE_LEARNING_MULTIPLIERS`) encodes a real domain insight: `artist:` identity is far more
predictive than a broad content tag or a title token. The IDF-style `tag_corpus_strengths` is
applied at both learn and score time, giving a 4.7× effective spread between common and rare tags —
a stronger correction than its `[0.55, 1.2]` clamp suggests.

**The engineering is careful where it counts.** Model artifacts are written atomically via
`mkstemp` + `os.replace`. Index coverage is complete for every equality and ordering predicate the
hot path uses. Degradation is layered and genuinely works: no sklearn → legacy, no torch → canvas
embeddings, too few labels → legacy. `ensure_gallery_exists` 404s rather than inserting stubs. And
the continuing-series heuristic is conservatively guarded — `continuing_series_title_key` demands a
source label, plus an explicit signal or trailing date, plus an identity value, plus a usable
normalized title before it groups anything. That conservatism is why the rule layer has not swallowed
the library.

## What to discard

Not "fix" — remove. These cost complexity and return nothing.

1. **The `active_reason_code` 1.35 multipliers** (F5). Delete all sites. Rebuild the intent as
   per-reason interaction features or per-reason sample weights, which neither leak nor skew.
2. **`score_visual_similarity`'s `confidence` term** (F14). A per-pass global scalar cannot express
   confidence — it cannot reorder anything. Either make it a per-item function of support or delete
   it.
3. **`like_probability` on legacy predictions** (F9). `sigmoid(unbounded sum)` is not a probability,
   and rendering it as "match 100%" on every card is misinformation. Return `None` in legacy mode and
   let the UI show the raw score, as the short-repeat and history pages already do.
4. **`MIN_BOOTSTRAP_EXPLORE_SCORE` as an absolute threshold** (F7). Replace with a quantile of the
   current candidate distribution, or drop the check.
5. **`_modality_disagreement`'s magic `6.0`** and its intercept asymmetry (F16). It drives a third of
   the Discovery quota; make it a normalized comparison or remove that quota.
6. **`recommendation_impressions` as currently used** (F16). Wire it into evaluation — its real value
   — or stop writing it.
7. **The `"personalized-content-v1-x"` fixture** (`tests/test_personalized.py:144-146`). It is worse
   than no test: it asserts the dead branch and reports green.
8. **The offline-file dependency in the acceptance gate** (F3) — not the gate. Compute acceptance
   in-process the way `classification.py` already does.

## How to improve

Ordered by value per unit of work.

### Immediate — correctness

1. **Fix the F1 dispatch by removing the string test, not patching the string.** Dispatch on the
   *scale of the score* — a `score_scale: "probability" | "additive"` field on the artifact, checked
   explicitly. Then add two tests: a **v2** model must reach
   `diversify_probability_ranked_galleries`, and a ranking whose scores all lie in `[0,1]` must never
   be reordered by more than the probability window. Audit every other constant that reads `score`
   at the same time — F7 found two more.
2. **Make the model state observable** (F3). Render which model is ranking, and why the fallback
   happened, on the model panel — the API already returns it. This is a small frontend change and it
   is the single cheapest defense against the whole class of bug in this document.
3. **Compute acceptance in-process** (F3), mirroring `classification.py`. Keep the offline harness as
   the deeper check, but stop making activation depend on a file a user must remember to generate.
   Store the report's sample count and refuse one far below current.
4. **Delete the F5 multipliers**, then re-run the evaluator and record the delta. The calibrator and
   C selection are currently fit on rows the serving path cannot produce.
5. **Fix `_rolling_splits`** (F6) to guarantee at least two folds with a meaningful test window
   whenever `count >= MIN_LABELED`, and lower the calibrator's 30-point floor or pool differently so
   probabilities are calibrated from the first trainable model rather than from the 80th label.
   Refuse to report a `mean_auc` from fewer than ~30 test points — emit `null` and a reason.
6. **Serialize retraining and the model cache** (F10). One lock, or better a dirty flag plus a single
   background trainer with debounce. Remove the pre-vote `model_snapshot` at `app.py:389`; it can
   train a model that the next line invalidates.
7. **Add a server-side sign gate on `reason_code`** (F5), on both the API and the import path.

### Near term — make evaluation able to see the system

8. **Evaluate `recommend_page`, not probabilities** (F2). Add a harness mode that calls the real
   ranking path and computes NDCG and precision on the **served order**, after prior,
   diversification, exploration, and gates. Pass `ranking_scores` for the personalized model too.
   This is what converts "we broke ranking" from invisible to caught, and it would have caught F1,
   F7, and F8 on the day they were written.
9. **Snapshot gallery state for temporal folds** (F2). At minimum, mask features that did not exist
   at label time: null out `detail_fetched_at`, tags beyond the list-time set, and embeddings created
   after the fold boundary. Without this the `detail_ready` and `visual_ready` segment metrics
   measure nothing. Time-filter marks instead of deleting them.
10. **Add real baselines** — random, most-recent, site-rating, tags-only logistic regression.
    Acceptance should require beating the *best* baseline, not the incumbent's worst version of
    itself. Report per-fold values with an interval and use non-overlapping test windows.
11. **Test the model surface end to end** (F4). One test that trains at a label count where the
    calibrator exists, ranks through `recommend_page`, and asserts on the served order. Add a test
    with `accepted=False` on a file-backed database. Make the sklearn guard consistent across test
    files.
12. **Measure and log the queue gates** (F13). Every removal, with its reason, and a "recently
    hidden" view. Then measure precision on the removals — that number does not exist, and it
    governs the most destructive operation in the system. Fix the three trace-loss cases: honor
    manual overrides in all views, give rated "updates" galleries a surface, and align the two
    pages' candidate windows.

### Structural — the model itself

13. **Move the bootstrap prior inside the model** (F12) — as a feature, or as an offset the
    calibrator is fit against. Then the evaluated and served quantities are the same object. Fix
    `personalized_reasons` to distinguish a positive from a negative prior.
14. **Standardize feature blocks and regularize them separately** (F16). Per-block scaling plus at
    least two C values — sparse text versus dense visual — is a small change with real headroom.
15. **Mean-centre the visual space** (F14). Store a corpus mean embedding; subtract it before forming
    the preference direction and before feeding vectors to the classifier. Make the visual block
    *absent* rather than *zero* on dimension mismatch, derive `visual_ready` from whether the vector
    actually matched, and make mismatch handling consistent across the three modes.
16. **Add recency as a model feature** (F7). It is currently the only ranking signal that is
    discarded rather than relocated.
17. **Replace the legacy accumulator rather than tuning it** (F9). It serves most of the time and its
    tag-count bias is structural. The smallest honest replacement is a per-feature log-odds estimate
    with a Beta prior — `log((pos + a) / (neg + b))`, summed, shrunk by support. Still inspectable,
    still runs without scikit-learn, still trains in milliseconds, but it cannot reward tag count.
18. **Break the enrichment loop** (F9). Allocate part of the detail budget by uncertainty or by
    coverage rather than entirely by current score, and give `select_detail_candidates` the visual
    model it currently omits.
19. **Fix the continuing classifier's leakage and validation** (F13). Fit the vectorizer inside each
    fold; validate temporally; train on a labeled *sample* of galleries rather than only on
    corrections; and require agreement between heuristic and classifier before removing anything —
    downrank when they disagree.
20. **Give Discovery a real candidate pool** (F8). Pass the pool through instead of routing it via
    `recommend_page`'s 100-item `limit`; measure coverage against **rated** items, not against the
    candidate set; and make pagination prefix-stable by computing quotas from the full selection
    rather than from `offset`.
21. **Add a negative implicit signal, or stop treating downloads as positives** (F15). Positive-only
    implicit feedback plus a positive-only query loop is a filter bubble with no exit. Cheapest fix:
    treat a skip on a **high-probability** item as a weak negative — the model predicted you would
    like it and you did not, which is real information, unlike an unseen impression. Also compare
    `downloaded_files` against `total_files` before calling a download complete, and expose
    `hath_download_signal_weight` in settings so the channel can be turned down.
22. **Give the query planner exploration and feedback** (F15). Sample learned queries rather than
    taking the top-k deterministically, and use `fetch_runs` yield to demote queries that stop
    producing rated positives.

### Operational

23. **Narrow the model signature to labels plus feature schema** (F11), so fetching unlabeled
    galleries stops invalidating the artifact and stops filling `model_training_runs` with spurious
    rows.
24. **Cache the per-request scans** (F10). `tag_corpus_strengths`, `continuing_update_series_index`,
    and `related_feedback_reference_index` are full library scans per page load; the `feedback`
    group-by runs twice per request; `model_data_signature` runs before the cache is consulted. Cache
    against a cheap library version counter, compute the signature after the cheap checks, and batch
    `feedback_confidence` into one query. Add an index on `feedback(gallery_url, id DESC)` to match
    the query's actual ordering.
25. **Make `queue_counts_payload` cheap** (F10). It passes `limit=1` and pays for scoring the whole
    pool.

## Verify on your machine

This review could not run any of these. Each answers a question the audit had to leave as runtime
state.

**Which model is actually ranking your queue?**

```bash
curl -s localhost:18787/api/recommendations | python3 -m json.tool | grep -A14 personalized_model
```

`accepted: false`, or any `model_fallback` value, means the legacy ranker is serving and the
personalized model is being computed and discarded (F3). Nothing in the UI would have told you.

**Do you have a passing evaluation report?**

```bash
ls -la data/recommendation-evaluation.json && python3 -c \
  "import json;r=json.load(open('data/recommendation-evaluation.json'));print(r['summary']['acceptance'])"
```

**How much label data exists — and is your model calibrated?**

```bash
sqlite3 data/recommender.sqlite3 "
SELECT SUM(vote > 0) pos, SUM(vote < 0) neg, SUM(vote = 0) neutral, COUNT(*) events
FROM (SELECT f.vote FROM feedback f
      JOIN (SELECT gallery_url, MAX(id) id FROM feedback GROUP BY gallery_url) l ON l.id = f.id);"
```

Training needs ≥50 non-neutral labels with ≥15 per class. **Below 80 the calibrator is `None`** and
`match N%` is a raw sigmoid (F6). Confirm with:

```bash
sqlite3 data/recommender.sqlite3 \
  "SELECT model_version, status, sample_count, selected_c, metrics_json
   FROM model_training_runs ORDER BY id DESC LIMIT 5;"
```

`selected_c` of `0.05` together with `"folds": 0` in `metrics_json` is the F6 signature. A long run of
rows with different signatures and identical sample counts is F11.

**Is the legacy score ratcheting?**

```bash
sqlite3 data/recommender.sqlite3 \
  "SELECT feature, weight, positive_count, negative_count FROM feature_weights
   WHERE (feature LIKE 'tag:%' OR feature LIKE 'category:%')
     AND feature NOT LIKE 'tag:artist:%' AND feature NOT LIKE 'tag:group:%'
     AND feature NOT LIKE 'tag:cosplayer:%'
   ORDER BY weight DESC LIMIT 20;"
```

Look for large positive `weight` with **`negative_count = 0`**. A bare thumbs-down never touches
these features at all — `feedback_updates_feature` returns `False` and the upsert is skipped, so
neither the weight nor the count moves. Only a score ≤2 or a ban mark can decrement them. Check
whether your history contains either:

```bash
sqlite3 data/recommender.sqlite3 \
  "SELECT SUM(score IS NULL) AS thumbs_only, SUM(score IS NOT NULL) AS scored
   FROM feedback WHERE vote != 0;
   SELECT kind, COUNT(*) FROM gallery_marks GROUP BY kind;"
```

If `scored` and the ban count are both near zero, nothing in your history can pull a broad tag
down — that is the ratchet regime in F9.

**Is anything disappearing from Review?**

```bash
sqlite3 data/recommender.sqlite3 \
  "SELECT classification, COUNT(*) FROM gallery_classification_overrides GROUP BY classification;"
```

At or just past 20 with ≥5 in the minority class, a classifier trained only on your corrections is
live (F13). Check the Updates page for cards reading `Learned classification: updates` — those are
the visible re-routes. The invisible ones are rated galleries and anything outside the Updates
page's candidate window.

**Confirm F1 without running anything:**

```bash
grep -n 'personalized-content-v' \
  exh_rec/personalized.py exh_rec/recommender.py tests/test_personalized.py
```

`MODEL_SCHEMA` and the dispatch string must match. They do not.
