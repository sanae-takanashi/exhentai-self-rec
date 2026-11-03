# Recommender Reassessment: 2026-11-03

## Decision

Keep `legacy-linear-v1` serving. The v5 challenger is promising but does not
pass the existing ranking-confidence and probability-calibration gates.
Do not install these reports as production acceptance evidence, change the
model artifact, expand Low Interest, or restart the service for this work.
No model ablation earned promotion. This assessment supersedes October's
data counts and activation decision, not its fail-closed policy.

## Fixed Data Snapshot

The read-only SQLite backup contains 18,540 galleries, 2,586 feedback events,
53,757 feature snapshots, 45,998 impressions and 185 Low Interest audits.
Latest gallery feedback contains 608 positive, 1,048 negative and 902 neutral
outcomes. These are different denominators from events and training examples.

Since schema-2 deployment on October 23 at 18:50:19 Singapore time
(`2026-10-23 10:50:19` UTC), there are 410 latest feedback outcomes:
80 positive, 143 negative and 187 neutral. All 223 new nonneutral labels have
schema-2 historical features. Neutral outcomes and unconfirmed impressions
are not negatives. One older `duplicate_update` outcome is excluded from
evaluation, consistently with personalized training, leaving 1,655 evaluable
historical labels overall.

Current production training has 1,759 examples, including 670 positives;
strict historical training has 709 examples, including 289 positives. These
populations differ because missing historical snapshots are not fabricated.
In particular, strict-data ablations do not establish behavior on every
production weak-label example.

The corrected, wider 500-label retrospective diagnostic also favors v5
(P@10 0.84 versus 0.64; NDCG@20 0.827459 versus 0.663121), but has ECE
0.142543 and still includes incomplete pre-migration history. It is not the
formal fresh-cohort result below and cannot justify activation.

## Fresh Temporal Serving Evaluation

Use only the 223 new nonneutral outcomes in chronological windows of
75, 75 and 73 labels. Training, vectorizer fitting, hyperparameter selection
and calibration remain strictly before each window. All three folds have
valid independent monotone calibration, complete schema-2 candidate history,
100% feature coverage and 100% paired serving-candidate coverage.

| Mean Metric | Legacy Serving | v5 Serving |
| --- | ---: | ---: |
| Precision@10 | 0.666667 | 0.800000 |
| NDCG@20 | 0.568840 | 0.779753 |
| ECE | not applicable | 0.136267 |

| UTC Test Window | Labels | Legacy P@10 | v5 P@10 | Legacy NDCG@20 | v5 NDCG@20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oct 24 14:18 - Oct 30 01:23 | 75 | 1.0 | 0.9 | 0.928178 | 0.908695 |
| Oct 30 01:24 - Nov 2 14:48 | 75 | 0.4 | 0.7 | 0.310230 | 0.675505 |
| Nov 2 14:48 - Nov 3 01:26 | 73 | 0.6 | 0.8 | 0.468111 | 0.755060 |

Acceptance is **false**. The NDCG@20 paired-delta 95% interval is
[-0.294243, 0.716070], and the P@10 interval is [-0.383823, 0.650490].
ECE's 95% interval is [0.049278, 0.223256], failing both the <=0.10 mean
and <=0.12 upper-bound checks. Three adjacent windows with visibly varying
outcomes are not evidence of a guaranteed live improvement.

The report is non-diagnostic under the existing `temporal-serving-v2` schema
because historical structural coverage is now complete. That does **not**
mean accepted or randomized: this is conditional replay of labeled galleries
under the existing policy, not full-library recall, unseen-creator performance
or an online trial. Bootstrap/settings history and overwritten mutable state
remain reconstruction limitations. Production uses more examples than strict
replay, so do not equate this with exact validation of the persisted artifact.

## Evaluation Corrections

Candidate materialization loses `feature_snapshot_at` when serving rebuilds
its dictionaries. Consequently, its per-image feature attachment could read
images created after the historical snapshot, even though model-only scoring
already checked that timestamp. There are 196 such image rows in the fresh
cohort. The isolated serving replay now removes later or undated candidate
image rows before scoring; all production files and records remain intact.

The final reports record `visual_history_policy=candidate-snapshot-cutoff-v1`
and `label_policy=explicit-nonneutral-excluding-duplicate-update`. Earlier
reports from this run without that visual-history policy are superseded.
The correction does not claim to recover images overwritten before capture.

The evaluator now accepts inclusive `--test-since` and exclusive `--test-until`
UTC cutoffs, preserves the preceding training prefix, includes a short tail
in the preceding fold and explicitly reports requested/covered test counts.
An empty new-data range never falls back to older evaluation outcomes.

## Bounded Improvement Experiments

Reserve the latest 100 new labels from variant selection. Compare variants on
the preceding 123 labels, training on their strict temporal prefix. The fixed
selection rule requires development NDCG@20 gain >=0.02, P@10 loss <=0.02 and
no ECE regression. This is a screening rule, not a significance claim or a
replacement for formal serving acceptance. Baseline holdout performance was
also visible in the initial general assessment; no candidate selection or
retuning uses those holdout results.

| Development Variant | P@10 | NDCG@20 | ECE |
| --- | ---: | ---: | ---: |
| v5 reference | 0.90 | 0.886494 | 0.144946 |
| No visual features | 0.90 | 0.851929 | 0.126001 |
| No title features | 1.00 | 0.892764 | 0.119600 |
| 90-day training-weight half-life | 0.90 | 0.886494 | 0.158730 |
| Exclude implicit H@H labels | 0.90 | 0.887119 | 0.147842 |
| Uniform calibration weights | 0.90 | 0.886494 | 0.143082 |

No candidate qualifies. No-title's NDCG gain is only 0.006270, despite its
better top ten. Removing visual features loses ranking quality; time decay
worsens calibration. Excluding implicit labels removes just one of 481 strict
training examples here, so it is a weak test of that hypothesis.

Only v5 reaches the final 100-label model-only check: P@10 0.90, NDCG@20
0.830831, AUC 0.844652 and ECE 0.117069. These are model-only metrics on
a different candidate set and windowing scheme, not the serving table above.
Do not repeatedly tune against these now-used outcomes.

## Low Interest And Next Work

The current audit window has 47 matured completed outcomes with 3 positives.
The positive-rate Wilson upper bound is 17.16%, versus the unchanged minimum
150 completed samples and <=10% upper-bound requirement. Only one audit fold
is eligible. Candidate-region prospective validation remains unavailable;
bottom-tail audit results cannot validate an unsampled threshold expansion.

Prioritize new explicit feedback across more time periods and independently
observed calibration/audit outcomes. Retain the no-title hypothesis for a
future untouched cohort, not an immediate feature deletion. Any prospective
candidate-region study needs a frozen cohort and exposure/outcome protocol
before threshold activation. Extra model complexity is not supported here.
The previous 300-500-label milestone is a review trigger, never an automatic
switch; this batch contains 223 new explicit labels, not 410.

## Verification And Reproduction

The 163 focused recommender/evaluation tests pass. The full run executes 694
tests with one skip, no errors and the same three known Windows H@H archive
failures recorded in the previous assessment. New regressions cover cutoff
semantics, no old-data fallback, timestamp-safe held-out outcomes, immutable
ablations, no future per-image statistics and duplicate-update exclusion.

Independent live readback confirms service health and unchanged model hash,
feedback, marks and related settings. The fixed snapshot hash also matches.
No live recommendation request is made to manufacture exposure evidence.
No production restart, retraining, acceptance-file installation or policy
change is performed. Reports and runtime evidence remain under ignored `data/`.

Run with the ML-enabled environment; keep outputs away from the production
`data/recommendation-evaluation.json` acceptance path:

```powershell
& '.\.venv-rocm\Scripts\python.exe' -B scripts/evaluate_recommender.py --db data/reassessment-snapshot-20261103.sqlite3 --test-since '2026-10-23 10:50:19' --window 75 --output data/reassessment-fresh-reproduction.json
& '.\.venv-rocm\Scripts\python.exe' -B scripts/recommender_experiments.py --db data/reassessment-snapshot-20261103.sqlite3 --since '2026-10-23 10:50:19' --holdout-size 100 --output data/reassessment-experiments-reproduction.json
```

Private evidence: `reassessment-inventory-20261103.json`,
`reassessment-fresh-final-20261103.json`, `reassessment-strict-final-20261103.json`,
`reassessment-experiments-final-20261103.json`,
`reassessment-tests-focused-20261103.json`, `reassessment-tests-full-20261103.json`
and `reassessment-verified-20261103.json`. Publication is separately verified
against the private Forgejo branch, preserving the existing GitHub remote.
