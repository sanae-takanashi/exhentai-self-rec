# Low-Interest Review Triage

Date: 2026-08-31

## Goal

Reduce the number of galleries that require full manual review by combining a rank-percentile fallback with a validated, automatically learned probability threshold.

## Decision

The app uses dynamic triage instead of automatically recording negative feedback:

- `Review` shows the primary ranking band.
- `Low Interest` shows the configured bottom percentage, defaulting to 20%.
- Periodic personalized-model training also searches temporal out-of-fold predictions for a safe absolute `like_probability` threshold.
- A learned threshold can add galleries outside the bottom percentage, but only when the personalized model is accepted, calibrated, probability-scaled, and the threshold belongs to the same model version.
- The combined Low Interest queue is capped at 35% by default and always retains at least one Review item.
- Membership is recalculated from the active `rank_score` before pagination.
- Percentile cards show `Bottom N%`; learned-threshold cards show `Very Low` and the probability cutoff.
- Ignoring the Low Interest tab does not alter training data.
- A later retrain, metadata enrichment, setting change, or new feedback can move a gallery between bands.
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
- `Low-interest max %`: maximum combined queue, default 35%.
- `Auto low-interest threshold`: enables or disables use of a validated learned threshold.

## Why It Is Not Automatic Rejection

Low-score positives can still exist when historical feature coverage or calibration evidence is incomplete. Automatically downvoting or permanently hiding the bottom band would contaminate feedback and make recovery difficult. Dynamic separation provides most of the review reduction without that failure mode.

Local evaluation reports are intentionally stored under the ignored `data/` directory because they can reveal library size, preference distribution, search terms, and model behavior for a specific installation. Only the reusable acceptance criteria and commands belong in the repository.

## Runtime Behavior When The Gate Fails

When no candidate satisfies all false-omission constraints, the generated policy is:

```text
status: safety-target-not-met
threshold: null
```

Production behavior then remains the configured percentile fallback. This is intentional: later batched retraining will reconsider the threshold, but the app will not hide additional galleries until current evidence supports doing so.

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
  tests.test_personalized tests.test_recommender tests.test_app
```

Result: 256 tests passed.

Reverified on 2026-09-01. Full suite result: 373 tests ran, 369 passed, 1 skipped, and 3 pre-existing H@H archive tests failed only because Windows produced `\\` where those tests assert `/`. The failures are outside the recommender and Low Interest paths.

Additional checks:

```bash
python3 -m py_compile exh_rec/personalized.py exh_rec/recommender.py exh_rec/app.py
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
  "review_low_interest_max_percent": 35
}
```

`GET /api/recommendations` must also include `low_interest_policy`. Until validation passes, it should report `active: false` and the Review split must remain percentage-only.
