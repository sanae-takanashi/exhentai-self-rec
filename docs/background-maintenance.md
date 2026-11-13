# Background maintenance

The backend runs one durable Jobs worker. Foreground work takes precedence over
automatic feedback enrichment, backups, and Review prewarm, in that order.
Running jobs are not preempted. Restart the Launcher GUI as well as the backend
when upgrading the supported job kinds.

## Explicit feedback enrichment

The default Review feedback path still performs no remote fetch. An explicit
asynchronous `enrich_feedback` request stores its intent in the same transaction
as the rating. A dispatcher assigns up to three intents to a Feedback Enrichment
job in a single short transaction. Gallery URLs remain in the private intent
table, not job parameters or history.

The job reuses the configured media/visual pipeline, including sprite crops, and
then fetches the existing bounded parent chain. It does not hold a SQLite writer
while fetching or analyzing. Pause/cancel checkpoints retain committed work.
Failed, partial, cancelled or interrupted work is not automatically replayed.
Use the associated job's Retry action after checking settings; retries follow
the job ancestry and skip intents already completed. No mass backfill is added.

## Recovery backups

Scheduled recovery backups are enabled by default, every 24 hours (configurable
from 6 to 168 hours). Scheduling requires an idle queue, enough disk space, and a
changed application-data revision since the previous successful snapshot.
Cancellation or an uncertain/failed attempt requires attention rather than an
automatic repeat. A manually queued Backup job can establish a fresh success.

The Backup job uses SQLite's online backup and independent quick_check/hash
receipt. It adds the DB-selected personalized model, if present, plus available
evaluation and classifier artifacts to a hashed bundle manifest. A fresh child
process copies and verifies the bundle, opens the copied DB read-only, and loads
the matching personalized model without training, scoring one copied gallery
when the model is ready. Socket connections are denied;
the server and Jobs worker are never started in the drill. All recovery files
and drill directories are retained. Nothing is automatically pruned or restored
over the live service.

Success proves database readability and DB-selected personalized-model loading,
not a complete machine recovery. Network credentials, DINO model weights, media
caches and external H@H libraries need their own recovery arrangements. Backups
contain sensitive local application data and must stay private. The Launcher's
Storage Create Backup command remains a DB-only backup, distinct from this Jobs
workflow.

## Health

A separate thread samples health every 60 seconds, independently of the Jobs
worker and maintenance dispatcher. SQLite probes are read-only with a short busy
timeout and query deadline. `/api/maintenance` reports the in-memory sanitized
snapshot without opening SQLite. `/api/jobs` includes the same snapshot and the
Launcher Jobs tab shows its state and fixed warning codes.

Checks cover worker availability, queued work, held feedback, repeated prewarm
failures, free space, backup readiness and progress older than an hour. A long
stage is only a warning, not proof of a deadlock; paused work is excluded. Stale
health samples are labelled stale. The monitor never restarts services, replays
work, probes database writes, activates models or changes a loaded Review queue.
