# Review model retraining

Review feedback is saved before model training runs. Training runs in the
background worker, and its schedule is independent of saving a rating.

## Schedule

- `batched` is the default: train after 10 pending feedback changes, or after
  the oldest pending change has waited 10 minutes. Both limits are configurable.
- `immediate` makes pending feedback eligible at the worker's next check.
- `manual` keeps pending feedback until an explicit retrain request.
- The manual retrain action queues work in every schedule mode, including
  `manual`. Repeated clicks coalesce into one queued request rather than starting
  overlapping training runs.

`POST /api/retrain` returns HTTP 202 with `queued: true` and the current
`model_retrain` status. It acknowledges durable queueing, not completion. The
HTTP request does not train the model inline. A manual request received while
training is running remains queued for a subsequent run.

H@H completion events use this same schedule. The event and a pending change
commit together before the worker is woken; duplicate events do not enqueue
another change. Download exclusion is immediate, while learned weights update
when the queued training completes.

Runtime retraining commits caller writes before waiting for the model lock.
Feature calculation uses a read snapshot, followed by a short atomic weight
replacement; expensive personalized training does not own a SQLite writer.
Settled Review/count requests avoid redundant membership/audit writes and
commit any necessary changes before sending the HTTP response.

## Pending work and recovery

`pending_count` counts outstanding changes, rather than unique galleries.
An explicit retrain request with an empty queue creates one pending unit.
`manual_requested` indicates an outstanding explicit request, `running` shows
the worker's current activity, and `claimed_count` shows changes claimed by
the running job. During training, new feedback starts a fresh pending batch.
Successful completion clears only the claimed batch and preserves newer work.

The queue and claim are stored in SQLite. If training fails, the claim is
restored alongside feedback received during training, with the oldest pending
time retained. Explicit manual intent is restored too. Startup recovers an
interrupted claim once; repeated recovery does not add it twice. Failure status
retains pending work for retry instead of presenting a completed training run.

`last_completed_at` records the latest successful run. A 202 response or an
empty pending count while a job is running does not indicate completion.

## Isolated checks

Run `.venv-rocm/Scripts/python.exe -B -m unittest discover -s tests -p test_retrain_jobs.py`.
The tests use a fresh temporary SQLite database for each case and stub expensive
personalized training while exercising real training transactions and threads.
They do not train models, touch the live database, or start a server.
Temporary fixtures are retained to respect the workspace's recoverable file
operations policy.
