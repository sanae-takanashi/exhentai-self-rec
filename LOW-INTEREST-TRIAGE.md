# Low-Interest Review Triage

Date: 2026-09-03

## Goal

Reduce the number of galleries that require full manual review by combining a rank-percentile fallback with a validated, automatically learned probability threshold.

## Decision

The app uses dynamic triage instead of automatically recording negative feedback:

- `Review` shows the primary ranking band.
- `Low Interest` shows the configured bottom percentage, defaulting to 20%.
- Percentage membership is sticky for the same eligibility policy. Removing high-ranked galleries through feedback, marks, or downloads cannot push an earlier tail assignment back into normal Review merely because the remaining denominator became smaller.
- The current tail can add new memberships. Retained items use `low_interest_reason=retained-percentile`; they leave the active queues normally after an action.
- Each opening day has a frozen budget of 5-10 audit slots. Still-eligible, unexposed pending items within the 35-day horizon are resumed first; remaining slots sample the current and retained Low Interest union. Resumed items retain their original audit ID, selection probability, score snapshot and date. No missed-day catch-up or same-day completion top-up occurs.
- Periodic personalized-model training also searches temporal out-of-fold predictions for a safe absolute `like_probability` threshold.
- A learned threshold can add galleries outside the bottom percentage, but only when the personalized model is accepted, calibrated, probability-scaled, the threshold belongs to the same model version, and the prospective audit gate passes.
- Learned-threshold additions are capped at 35% by default. Retained percentage memberships may exceed that share while a backlog is drained, and the implementation still retains at least one Review item.
- New percentile membership is calculated from the active `rank_score` before pagination; earlier memberships for the same policy are then retained.
- Percentile cards show `Bottom N%`, retained cards show `Kept Low`, and learned-threshold cards show `Very Low` with the probability cutoff.
- Ignoring the Low Interest tab does not alter training data.
- Retraining and metadata enrichment can add newly low-ranked galleries without returning retained tail assignments to normal Review. An explicit action, audit promotion, or eligibility-policy change can move a retained gallery out of the active Low Interest view.
- Setting `Low-interest %` to `0` restores one unsplit Review queue.

This avoids permanently losing false negatives while still letting the model remove low-priority work from the normal review flow.

## Learned Threshold Safety Gate

Threshold fitting runs after every successful personalized-model retrain, including batched background retraining. It does not use the final model's training predictions. Each candidate is evaluated on chronological out-of-fold predictions with fold-local feature fitting and calibration.

The largest threshold is retained only when all of these checks pass:

- At least 80 distinct temporal OOF predictions.
- Every OOF fold has an independent calibration model.
- At least 30 threshold-triaged validation samples.
- Observed positives inside the threshold band are at most 10%.
- The Wilson 95% upper confidence bound for that positive rate is also at most 10%.
- Threshold-triaged positives are at most 5% of all OOF positives.
- The probability threshold is at most 0.50.

The policy is persisted inside `personalized-model.joblib` with its `model_version`. Failed candidates are also persisted with a reason such as `insufficient-oof-data`, `oof-calibration-unavailable`, or `safety-target-not-met`. Runtime activation has a second gate and falls back to percentage-only splitting on any mismatch.

Settings:

- `Low-interest %`: percentile fallback, default 20%.
- `Low-interest max %`: maximum share for learned-threshold additions, default 35%; it does not evict retained tail assignments while a backlog shrinks.
- `Auto low-interest threshold`: enables or disables use of a validated learned threshold.
- `Random tail audit`: enables the bottom-20% audit on recommender opening days.
- `Audit items per opening day`: selects 5-10 items per active local day, default 6.

## Prospective Random Audit Gate

Historical OOF predictions can propose a threshold, but they cannot activate it.
**Automatic activation currently fails closed** with
`prospective-candidate-protocol-unavailable`: the collector does not yet validate
the candidate's incremental region, frozen cohorts and nonresponse sensitivity.
Even a synthetic dataset passing every numerical check cannot bypass this guard.
The rolling 35-day report is diagnostic, not an acceptance certificate:

- At least 28 elapsed days from the first to the latest matured audit in the window; audits from the most recent five days are held out for outcome maturation.
- At least four active audit days with an exposed sample. This prevents two isolated openings from masquerading as four weeks of evidence while still allowing the recommender to be used only on some days each week.
- At least 150 completed random-audit labels.
- At least three non-overlapping seven-day folds with at least 20 completed labels each.
- The completed audit positives' Wilson 95% upper bound is at most 10%.
- Positive actions that would have entered the candidate learned threshold are at most 5% of all prospectively observed positive actions.
- At least 30 distinct galleries with prospectively exposed candidate-threshold decisions are required, so suppressing candidates cannot make the loss metric pass by removing their observations or replaying one gallery repeatedly.

For the exact 150-sample boundary, 7 positives passes the Wilson requirement and 8 positives fails it. A neutral skip does not complete an audit. A truly visible audit becomes `incomplete` on a later opening day without an explicit action. Never-visible items may resume within the horizon; older records remain historical pending assignments, outside the active report and carry-forward pool, never implicit negatives.

Each audit row stores the sampling probability, eligible frame size, score interval, served score/rank, Legacy score/rank, Personalized shadow probability/rank, feature snapshot, selection time, exposure time, completion state, and outcome. A gallery audited within the rolling window is excluded from later daily samples, and the first sample for a local day is frozen across reranks. Recommendation impressions store both ranking results and the candidate-threshold decision even when Legacy remains the served model.

`GET /api/low-interest-audit` returns all `failed_reasons`, known/unknown positive denominators and threshold-cohort counts. Missing decisions are excluded from diagnostic rates, not treated as false. Missing or mixed threshold versions make aggregate threshold loss unavailable. Delivered and confirmed-visible comparisons are separate; neither certifies safety. `active_audit_days` counts original selection dates with confirmed visible records, not refreshes or legacy delivery flags.

`POST /api/impressions` preserves page delivery. Instrumented deliveries opt into
`visibility_protocol=viewport-v1`; `POST /api/impressions/visible` confirms an
existing request/gallery/surface only once. Browser visibility requires at least
50% of the card in the viewport for one continuous foreground second. Leaving
the viewport, detaching the card, or backgrounding the tab resets dwell.
Old rows retain NULL visibility/protocol and are never backfilled as visible.
This measures browser visibility, not attention; failed scripts and network
requests remain missing evidence.

The calendar window has deliberately not been enlarged merely to pass gates.
At intermittent usage, evidence may remain insufficient indefinitely; a future
activity-aware, bounded-age protocol needs replay and stratified validation.
See `AUDIT-IMPLEMENTATION.md` for scope, tests and remaining work.

## Why It Is Not Automatic Rejection

Low-score positives can still exist when historical feature coverage or calibration evidence is incomplete. Automatically downvoting or permanently hiding the bottom band would contaminate feedback and make recovery difficult. Dynamic separation provides most of the review reduction without that failure mode.

Local evaluation reports are intentionally stored under the ignored `data/` directory because they can reveal library size, preference distribution, search terms, and model behavior for a specific installation. Only the reusable acceptance criteria and commands belong in the repository.

## Runtime Behavior When The Gate Fails

When no candidate satisfies all false-omission constraints, the generated policy is:

```text
status: safety-target-not-met
threshold: null
```

Production behavior then remains the configured percentile fallback. This is intentional: later batched retraining can propose another threshold, but the app will not route additional galleries until current prospective evidence supports doing so.

## Ranking Semantics

The split follows the score actually serving the queue:

- Accepted personalized predictions use `rank_score` derived from `like_probability` plus ranking offsets.
- Visual-only mode uses visual similarity.
- When the personalized acceptance gate is not met, Hybrid uses the existing additive fallback score.

Raw Hybrid and visual scores are never used for the absolute threshold. Normalizing those scores into a quantile would only reproduce the percentile split.

## Verification

Core targeted tests:

```bash
.venv-rocm/Scripts/python.exe -m unittest \
  tests.test_audit tests.test_personalized tests.test_recommender tests.test_app
```

The Wilson boundary is covered explicitly with 7/150 and 8/150 assertions. Run the full current suite before deployment; old pass counts in this document are not release evidence.

Additional checks:

```bash
python3 -m py_compile exh_rec/audit.py exh_rec/personalized.py exh_rec/recommender.py exh_rec/app.py
node.exe --check static/app.js
```

Headless browser checks should cover desktop and mobile viewports: the three Low Interest settings must be visible, the fallback status and card reason must render, and neither viewport may have horizontal overflow.

Read-only evaluation commands:

```bash
.venv-rocm/Scripts/python.exe scripts/evaluate_recommender.py \
  --db data/recommender.sqlite3 \
  --output data/recommendation-evaluation-low-interest-strict-20260831.json

.venv-rocm/Scripts/python.exe scripts/evaluate_recommender.py \
  --db data/recommender.sqlite3 \
  --allow-current-features \
  --output data/recommendation-evaluation-low-interest-diagnostic-20260831.json
```

The diagnostic report is useful for development only. It does not replace the strict production acceptance gate.

Persisted reports:

- `data/recommendation-evaluation-low-interest-strict-20260831.json`
- `data/recommendation-evaluation-low-interest-diagnostic-20260831.json`

## Deployment

The frontend capability-checks the new settings so an older backend cannot be mistaken for threshold support. Back up the SQLite database, stop the existing launcher, configure the H@H credential file locations outside the repository, and start the supervised launcher:

```powershell
$env:EXH_REC_HATH_TOKEN_FILE = "C:\path\to\hath-token.secret"
$env:EXH_REC_HATH_SSH_CONFIG = "C:\path\to\ssh-config"
$env:EXH_REC_HATH_SSH_HOST = "hath-host"
.\scripts\run.ps1 -VenvPath .venv-rocm
```

After restart, `GET /api/settings` must include:

```json
{
  "review_low_interest_percent": 20,
  "review_low_interest_auto_threshold": true,
  "review_low_interest_max_percent": 35,
  "review_low_interest_audit_enabled": true,
  "review_low_interest_audit_daily_count": 6
}
```

`GET /api/recommendations` must also include `low_interest_policy`. Until validation passes, it should report `active: false` and the Review split must remain percentage-only.
