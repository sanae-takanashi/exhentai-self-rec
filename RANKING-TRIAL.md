# Review Ranking Trial

The `review_ranking_mode` setting defaults to `legacy`. The Review toolbar
offers Legacy and v5 Trial; switching clears the current queue and undo slot
and reloads from page one. Select Legacy to roll back immediately.

The trial uses v5 rank scores only for unrated primary Review ordering, after
candidate filtering, Low Interest membership and audit selection. Low Interest,
Preview, Discovery, downloads and formal evaluation retain their existing
behavior and gates. Missing or invalid predictions fall back to Legacy for the
whole Review pool. Visual-only mode keeps its original ranking.

Trial status is not formal model acceptance. No match probability or confidence
claim is displayed. `evaluation_acceptance()` and learned Low Interest activation
are unchanged. Existing automatic retraining is unchanged too: this is not a
frozen model experiment.

Each delivered item records the requested and actual ranking policy, fallback,
model version, training timestamp and artifact identity in impression context.
Feedback links to an exact acknowledged request/gallery/surface impression;
missing or mismatched context remains unknown. Neutral ratings remain neutral,
and undo removes the corresponding link with the feedback row.

Review experience and regression results after roughly one week or 100 new
explicit labels. This is a review trigger, not automatic promotion. Separate
cohorts by actual model/training revision and inspect positive, negative and
neutral outcomes plus visibility and unknown attribution. Sequential experience
is observational, not a randomized A/B test, and does not establish causality.

Focused coverage: `tests/test_ranking_trial.py` and
`node tests/browser_ranking_trial.cjs` (Playwright dependency follows existing
browser tests). Browser fixtures intercept all network requests and mutations.
