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

Copying a previous continuing-gallery rating also commits the copied feedback
and pending change together before waking the worker. It never retrains inline.
Feedback and undo received during training remain in the next pending batch.

Runtime retraining commits caller writes before waiting for the model lock.
Feature calculation uses a read snapshot, followed by a short atomic weight
replacement; expensive personalized training does not own a SQLite writer.
Settled Review/count requests avoid redundant membership/audit writes and
commit any necessary changes before sending the HTTP response.

Serving scores and `GET /api/model` never acquire the numerical training lock
or start personalized training. They use the database-confirmed active model,
or return an explicit missing status if that artifact cannot be recovered.
Classifier status reads also avoid starting training. Training-run audit writes
happen after the numerical lock is released; a failed audit write or failed
runtime training preserves the pending claim instead of reporting completion.

Personalized artifacts are atomically published under signature-derived names
in a sibling `*-versions/` directory, alongside the compatibility `.joblib` file.
The previous compatibility artifact is preserved before replacement. A new file
is not active until its signature commits to SQLite, so a rollback or restart
can recover the old active version. Versions and interrupted temporary candidates
are retained, not automatically pruned; include these directories in storage
planning and backups. Acceptance refreshes operate on serving snapshots rather
than mutating the published cache entry.

Fetch and enrichment protect the entire acquired-lock scope, including setup
and finalizers. Exceptions release the fetch lock and clear its running flag.

Jobs retry only positively identified `SQLITE_BUSY` failures of the initial
`BEGIN IMMEDIATE`, before any job is claimed. There are at most seven attempts,
with a 250 ms SQLite busy timeout and interruptible backoff up to two seconds.
Exhaustion stops the worker and notifies waiters; queued jobs remain durable.
Errors after acquisition, uncertain commits and unclassified errors still stop
the worker without automatically replaying work.

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

`tests/test_lock_safety.py` additionally covers concurrent serving, copy/undo
during training, subsequent pending runs, artifact publication/crash recovery,
fetch setup/finalizer faults and real SQLite claim contention. Run it against
an isolated data/model directory, as with the other personalized-model tests.

## Review media prewarm

`Review Prewarm` is a durable Jobs kind. Background preparation is enabled by
default when a saved cookie exists. Settings expose its toggle and candidate
window (default 120, range 40-400). Each job prepares at most three galleries,
then releases the single Jobs worker. Automatic jobs sort behind ordinary queued
work; they do not preempt running jobs. The native launcher also offers a manual
`Review Prewarm` preset, with the usual pause/cancel/retry controls. Pausing a
running job retains the existing queue-wide pause semantics; disable automatic
prewarm in Settings to stop future scheduling.

Selection uses the current saved Review language, model/ranking, bootstrap-match,
recency and primary-band policy. It reads retained Low Interest memberships and
existing audit slots but never creates memberships, freezes an audit day, records
impressions or changes feedback. Transient browser text filters and an already
loaded Focus cursor are not known to this scheduler; its window follows the
server's current ranking, not an exact mirror of a browser's retained queue.

Preparation fetches missing detail/sample metadata, caches the cover and all
selected sample previews (including sprite crops), then analyzes the cover plus
up to ten samples. It uses the configured encoder/device. DINOv2 uses local model
files only; an unavailable model leaves the job partial, without silently changing
the encoder or downloading weights. The existing explicit model-download action
can populate missing model files. Simple uses the same normalized 8x8 RGB feature
layout as the browser fallback, with Pillow's bilinear resampling.

Readiness is persisted against the media URLs/crop geometry, encoder/model version
and saved embedding digest. Changed metadata, overwritten embeddings or missing
cache files invalidate readiness. Failed attempts retain committed work and have
bounded exponential cooldown (10 minutes initially, up to 6 hours). One failed
gallery does not abort the rest of the batch. Automatic scheduling coalesces active
work and stops scanning for five minutes when there is nothing due. Restarts keep
readiness/cooldowns; interrupted Jobs retain the existing explicit retry behavior.

Network access and inference run outside SQLite write transactions and reuse the
existing network limiter/ban cooldown. Cache fills are single-flight per striped
resource lock, publish through temporary siblings, and check recorded sizes/hashes.
DINO inference is serialized across foreground/background callers. Changed visual
features mark one coalesced pending training unit; they never retrain per image.
No cache files are automatically pruned. Completed prewarm avoids remote fetching
and inference during Review, but local transfer/decoding and genuinely new or stale
media can still take time.

`tests/test_review_prewarm.py` covers the synthetic preparation pipeline, sprite
fallback, idempotency/invalidation, transaction availability, cooldown, cancellation,
bounded batches, queue priority, scheduler coalescing and concurrent cache fills.
