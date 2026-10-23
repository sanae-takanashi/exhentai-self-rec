# Recommender Reassessment: 2026-10-23

## Decision

Retain legacy serving and the existing Low Interest safeguards. Ship the v5
correctness fixes and forward-looking snapshot instrumentation, but do not
activate the challenger on the strength of this retrospective diagnostic.
No additional model complexity is justified by the current evidence.

This review supersedes the August assessment for current activation decisions.
Historical reports remain useful records, not current acceptance evidence.

## Data And Findings

The isolated database snapshot contains 15,206 galleries and 2,176 feedback
events. Latest gallery feedback includes 528 positive, 905 negative and 715
neutral decisions. Production training has 1,515 examples; strict historical
training has 481. These counts have different denominators and are not additive.
Only 682 of 36,918 delivered impressions were confirmed visible. Delivery and
neutral feedback must not be interpreted as negative preference labels.

The production v4 calibrator has a positive slope; it is not currently inverted.
However, the earliest trainable historical fold reproduces a negative sigmoid
slope, which can reverse ranking. The old replay also uses current candidate
metadata and later auxiliary-classifier outcomes. Its estimates are unsuitable
for deciding whether to switch the live recommender.

## Implemented

- Upgrade to `personalized-content-v5` and `temporal-serving-v2`.
- Fit preprocessing and the base classifier only before the temporal calibration
  holdout. Select hyperparameters on training prefixes and do not refit the
  classifier after calibrating it. Bootstrap classifiers use that same prefix.
- Reject nonfinite, zero or negative calibration slopes. Keep monotone diagnostic
  ranks, expose no likelihood, reject activation and use legacy fallback.
- Refresh acceptance on normal, cached and stale model loads. Require a fresh,
  schema-compatible, explicitly non-diagnostic report with all named checks.
- Replay full feedback prefixes, including neutral resets. Remove later marks,
  downloads, overrides and classification outcomes from isolated fold copies.
- Materialize historical candidate state and freeze all learned auxiliary
  components at the cutoff. Compare actual challenger and legacy serving on
  their shared candidates; never assign fabricated ranks to filtered items.
- Separate rank-only metrics from probability calibration. Report coverage,
  skipped folds, limitations and Student-t uncertainty; one fold has no CI.
- Snapshot schema 2 records structural eligibility, recency and sample metadata.
  Migration adds a current-time baseline per gallery without rewriting or
  backdating schema-1 history. Old unknown fields remain unknown.
- Expose non-sensitive runtime versions in `/api/status` for restart verification.

## Diagnostic Results

Four usable windows contain 397 paired served candidates. Mean metrics:

| Metric | Legacy Serving | Challenger Serving | Model Only |
| --- | ---: | ---: | ---: |
| Precision@10 | 0.650 | 0.800 | 0.850 |
| NDCG@20 | 0.661 | 0.810 | 0.827 |

These are conditional labeled replays under the current policy, not full-library
recall, randomized online results, or a forecast of a 15-point live improvement.
Challenger serving includes legacy fallback in its uncalibrated first fold.
ECE is 0.130 over the three calibrated folds; its 95% upper bound is 0.216.
Gain confidence intervals fail the acceptance checks. Schema-1 snapshots omit
historical structural fields, so the report is explicitly diagnostic-only even
though ordinary feature-snapshot coverage is 92%.

Bootstrap/settings policy has no historical event log, and mutable marks and
classification overrides have only last-write timestamps. Those reconstruction
limits remain disclosed; schema 2 does not retroactively remove them.

Low Interest has only 38 matured audits, including 4 positives, with a 24.13%
Wilson upper bound. This fails the existing 150-label / 10% upper-bound
requirements. Candidate-region prospective validation is still unavailable.
No thresholds, automatic-expansion permissions or visibility semantics changed.

## Verification And Deployment

- Full suite: 467 tests, 3 failures, 1 skipped, no errors. The failures are the
  existing H@H archive tests comparing POSIX download paths with native Windows
  paths. No archive implementation or tests were changed in this work.
- Sandbox migration created schema-2 baselines for all 15,206 galleries and did
  not increase strict historical training eligibility. A full v5 model trained
  on 1,515 examples with 1,212 classifier-fit and 303 calibration examples.
  It remained unaccepted, and all 40 sampled serving results used legacy fallback.
  The production model artifact hash remained unchanged.
- Initial publication did not restart production: its exact original launcher
  arguments/environment were not recoverable, so the original launch window and
  H@H reverse-tunnel configuration were preserved for an owner-controlled restart.

### Verified Production Deployment

The owner restarted the original launcher on 2026-10-23. Independent HTTP and
read-only database verification completed at 18:53 Singapore time:

- `/api/status` reports `personalized-content-v5`, `content-features-v6`,
  `temporal-serving-v2` and snapshot schema 2.
- All 15,206 galleries have schema-2 snapshots. Migration baselines were captured
  at 18:50:19 Singapore time, not backdated. All 26,080 pre-existing snapshots
  match the pre-deployment database copy by count and a digest over every old
  column. Strict historical training remains at 481 examples.
- `/api/model` reports the trained v5 candidate with 1,515 examples and 303
  independent calibration examples; calibration slope is positive (1.2899).
  Acceptance remains false. All 40 items returned by the actual recommendation
  endpoint use `legacy-linear-v1` and expose no calibrated likelihood.
- An actual H@H observer heartbeat arrived after migration, at 18:52:12 Singapore
  time, less than a minute before verification. This verifies the existing event
  receiver path after restart without injecting synthetic integration events.
- Feedback and impression totals remain 2,176 and 36,918. Verification did not
  manufacture preference labels or exposure events.

Deployment verification passed. The current work is complete; retain the gates
and collect new prospective data before another model iteration. Private
allowlisted evidence is `data/reassessment-deployed-20261023.json`.

## Next Review

Collect roughly 300-500 new explicit labels after schema-2 deployment as a
review milestone, not an automatic activation trigger. Require at least three
nonoverlapping windows with complete historical state and valid independent
calibration, passing ranking/calibration confidence checks, and adequate visible
and audit outcomes. Avoid tuning repeatedly against these same old windows.

Run the read-only evaluator with the ML-enabled environment:

```powershell
& '.\.venv-rocm\Scripts\python.exe' -B scripts/evaluate_recommender.py --window 100 --output data/recommendation-evaluation-next.json
& '.\.venv-rocm\Scripts\python.exe' -B -m unittest discover -s tests
```

Keep populated databases, models, gallery-level reports and execution evidence
local under ignored `data/`. Final private evidence for this review includes
`reassessment-v5-final-20261023.json`, `reassessment-tests-20261023.json` and
`reassessment-sandbox-20261023.json` and `reassessment-deployed-20261023.json`.
