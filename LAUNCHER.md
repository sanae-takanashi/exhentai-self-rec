# Windows Launcher

## Interaction

Double-click `launch.vbs` for a console-free entry point using the repository's
`.venv-rocm` or `.venv` Python. Alternatively double-click `launcher.pyw`
(a working Python file association is required), or run
`.venv-rocm\Scripts\pythonw.exe launcher.pyw`. The UI and the service virtual
environment are independent. Tk needs no third-party GUI dependency; optional
tray support uses `pystray` and Pillow from `requirements-launcher.txt`.

The heading, title bar and taskbar use the green E/gallery icon from `assets/`.
Windows gets separate 16-256 pixel icon frames and a launcher-specific taskbar
identity instead of grouping it with other Python windows. Reopen the launcher
to load a new icon; the background service does not need to restart. The source
and build instructions are in [assets/README.md](assets/README.md).

The connection strip shows Stopped, Starting, Running, Stopping, Unresponsive,
Externally started, Port occupied, or Failed. Start, Stop, Open Home and Refresh operate on the
saved profile. Closing the window leaves a background service running.
Stop only signals this launcher's supervisor; it never kills a PID obtained
from a port scan or an old state file. Existing manually started servers are
detected but cannot be stopped or adopted. Stop them using their original
launcher before starting a managed replacement.

The seven tabs are **Overview**, **Jobs**, **Connections**, **Activity**,
**Environment**, **Storage**, and **Logs**. This is an operational console, not a second gallery
browser: content review, feedback, recommendation policy and download-item
operations remain in the web UI.

## Jobs And Workflows

Jobs runs Fetch, Enrich, Retrain and verified private Backup tasks through one
durable SQLite queue in the backend. The launcher is only a client: hiding or
exiting it does not stop queued/running work. The existing web Fetch/Enrich
requests and automatic fetch/model schedules use this same worker after a
backend restart. The web API still waits for its result; repeated clicks can
queue additional manual work. Automatic requests coalesce while equivalent
work is already active. Other web operations are not moved into Jobs.

The composer offers single tasks and Backup + Fetch/Enrich + Retrain presets.
Query, detail limit, rated inclusion and filter fields apply to relevant steps.
Save Workflow retains a named, parameterized preset in the backend database;
an existing name can be replaced after confirmation. These are template-based
workflows, not an arbitrary step editor. Up to 50 presets and 100 active jobs
are accepted. All, Active and History filter the display. All active work and
the latest 100 terminal records are shown, with total counts, timestamps,
checkpoints, per-step states and allowlisted result counts. Older history
remains in SQLite. Job queries/filters are private application data, not part
of sanitized diagnostics; keep the database and its backups private.

Queue appends work; Run Next moves it ahead of waiting work, without preempting
the running job. Pause holds a queued job immediately. For a running Fetch or
Enrich it requests a pause before the next listing page, query metadata phase,
gallery detail or workflow step, after prior writes are committed. The running
job retains the worker slot while paused, so later jobs wait. Resume continues
the same execution. Cancel is cooperative at those checkpoints and retains
committed pages, detail metadata and fetch cursors. Network requests, an entire
query metadata phase, retraining and backup are not interrupted mid-operation;
short jobs may finish before a pending request takes effect.

A partial/failed step stops its workflow. Retry creates a new linked job,
preserves successful workflow steps and re-executes incomplete steps with their
saved parameters. It does not retry only failed galleries inside a step. Fetch
uses its existing durable cursor rules, not an in-memory resume promise. If
the backend stops, in-flight/paused jobs become Interrupted on next startup;
queued jobs resume in order and held jobs remain held. Interrupted work never
silently replays. A bookkeeping failure disables the worker rather than running
untracked work; restart it deliberately after inspecting the database.

Backup jobs use the backend's actual database, online SQLite backup and the
same independent quick_check/hash receipt verification as Storage. Their
artifacts live in that database directory's `backups/`. The queue does not
persist exception text or gallery result payloads. Errors and notifications
use fixed summaries. An uncertain POST result is never automatically retried:
refresh Jobs before deciding whether to send the command again.

Older backends show "backend restart required" and disable job controls. The
launcher does not restart them automatically. Reopen the launcher for UI changes,
then use the normal owner-controlled backend restart after checking active work
to enable the new endpoints and worker. Jobs requires that updated backend;
Storage's explicit offline maintenance remains independent.

## Windows Tray

Install in the Python environment used to open the launcher, for example:

```powershell
.\.venv-rocm\Scripts\python.exe -m pip install -r requirements-launcher.txt
```

The branded tray icon offers Show Launcher, Show Jobs, Open Home, Start Saved
Profile, Stop Managed Service, quick Fetch/Enrich/Retrain/Backup, and Exit
Launcher. Its callbacks only enqueue events for Tk's main thread. Start Saved
Profile never saves unsaved form edits; Stop still requires confirmation and
only controls this launcher's managed supervisor. Quick Backup also asks for
the private-data confirmation. Quick job submission opens Jobs for feedback.

Environment contains Close window to tray and Task notifications preferences.
With a working tray, closing the window hides it by default; Hide to Tray is
also available. Show Launcher or the default tray action restores it. Exit
Launcher removes only the icon/UI, leaving backend work alive. Missing tray
dependencies or a failed tray leave the ordinary window usable, and closing
then exits normally instead of hiding an unreachable window.

Preferences live in ignored `data/launcher/config.ui.json` (or the matching
custom-profile sibling), independently of service parameters/startup digests.
Changes are atomically saved with backups and readback. Notifications report
new terminal jobs and an observed managed-service failure. The first history
snapshot establishes a quiet baseline, repeated terminal states are suppressed,
and large completion batches are aggregated. No workflow names, queries,
gallery titles, paths, credentials or raw errors appear in notifications.
Delivery depends on Windows notification settings; it is not guaranteed.

Overview distinguishes verified health from process ownership and shows the
supervisor uptime, last status check, launch-profile agreement, unsaved edits,
fetch summary and latest retained sanitized error. Uptime is not inferred for
external services. New supervisors record a digest of their launch profile;
older already-running supervisors show profile agreement as unknown until the
next owner-controlled restart. The digest does not prove database overrides
or inherited environment values. Local and service-startup revisions are shown
separately, together with the backend's Python version and startup timestamp.
The source comparison uses a bounded SHA-256 snapshot of backend Python files
and the two service bootstrap scripts. It detects uncommitted source changes
even if the Git revision has not changed. Web assets, launch-UI code, dependencies,
database overrides and inherited settings are not part of this comparison.
The service reports a snapshot captured at application import, not a continuously
re-read checkout revision or a guarantee of atomic live code updates.

## Checkup And Status Refresh

**Run Checkup** in Overview explicitly checks local service identity, startup
source agreement, required launch-file existence, the selected environment's
base packages, proxy TCP reachability, current-run SSH log evidence and saved
observer receipts. It uses the saved profile, not unsaved form edits. It never
starts/stops services, opens credential files, initiates SSH authentication,
requests upstream gallery content, installs packages, or runs training/downloads.
Disabled H@H checks are skipped without reading the observer database.

Each result has a check timestamp and a selectable next-action detail. Results
arrive individually, and failures do not suppress later independent checks.
Passed, Failed, Warning, Unknown and Skipped are distinct; no overall "healthy"
claim is made from partial evidence. A reachable TCP socket is not proof of
upstream access, and historical observer receipts do not prove a live tunnel.
The environment probe still has its 20-second timeout. Database integrity checks
and backups remain separate explicit Storage actions.

The result table is a point-in-time snapshot, not an automatic alert or a
continuously green checklist. An observed service, source or saved-profile
change invalidates its summary. A partial unexpected failure retains completed
rows and leaves pending checks unverified; rerunning replaces that snapshot.
Only the current launcher window retains these results; nothing is persisted
as content-bearing diagnostic history.

Status polling has a separate, single-worker executor from serialized service
and maintenance operations. Environment probes, storage scans and backups do
not suspend readiness, fetch progress or log refresh. The footer shows the
current operation stage and elapsed time. There is no fabricated percentage,
ETA, unsafe cancellation or concurrent mutation queue. Closing the UI leaves
the service running and lets already-started bounded maintenance finish.

The lightweight `/api/runtime` endpoint returns only startup revision, backend
source digest, Python version and startup timestamp from memory. It does not
open the database or recompute source identity per request. Older servers show
startup identity as unknown until an owner-controlled restart; the launcher
does not perform that restart. Source and runtime observations are refreshed
at most every 15 seconds, with 60-second retries for unsupported old servers;
managed-run changes and the Refresh button trigger a fresh check. New workers
omit runtime, Jobs GET and health/fetch polling from retained logs.

Parameters cover `run.ps1`: virtual environment, bind address, port, data
directory, proxy, visual encoder/device, H@H enablement, token-file path,
SSH-config path and SSH host. Environment holds service parameters and automatic
startup; Connections holds H@H parameters, SSH executable, remote loopback port
and reconnect delay. Blank optional
fields preserve the existing environment/default behavior. Secrets remain in
their existing files and are read only by the established H@H supervisor.

Configuration lives in ignored `data/launcher/config.json`. Saving retains a
timestamped backup and atomically replaces a validated UTF-8 JSON sibling.
Only paths and non-secret settings are saved. Proxy URLs containing credentials
are rejected. Runtime status contains fixed codes, never child output,
tokens, cookies or complete command lines.

## Connections And Environment

Connection checks use the **saved** profile, not unsaved edits. Local API health
is polled; proxy TCP checks and observer timestamp reads are explicit actions.
A reachable proxy socket does not prove upstream access. Blank proxy means
inherited/default behavior, not verified direct access. Observer information is
only a client count and latest receipt time from the saved profile's database,
read in SQLite read-only mode; it is not live tunnel proof. SSH start/retry/error
evidence is taken only from the current managed run's allowlisted logs. A start
request is never presented as a connected tunnel.

Check Environment runs a fixed local probe with the saved service Python and a
20-second timeout. It reports interpreter/package versions and detected Torch
accelerator availability without downloading models, installing packages, or
changing the running service. This checks the selected environment, not the
backend's current encoder or database-level overrides. Paths and configuration
remain accessible offline; runtime-only information is explicitly unavailable.

## Storage And Maintenance

Storage always targets the saved profile data directory. Scan Usage reports
disk free space, database/WAL sizes, thumbnail-cache size, backups and launcher
files. Each directory scan is bounded to 20,000 entries and two seconds; partial
results are lower bounds. Linked/reparse paths are not traversed. These totals
cover the named categories, not every file in the data directory.

Check Database runs read-only SQLite `quick_check`. Create Backup requires a
privacy confirmation and uses SQLite's online backup API, including committed
WAL data. A fresh read-only connection verifies the backup before a SHA-256
receipt is written. Backups live in `data/backups/launcher-backup-*.sqlite3`
(relative to the configured data directory). They include private settings and
saved authentication; never publish them. Existing backups are not overwritten.
Incomplete artifacts are retained without a verified receipt. Last-backup status
means verified at creation, not continuously rehashed.

Export Diagnostics creates a new allowlisted JSON file under `diagnostics/` in
the saved data directory. It excludes gallery titles, query text, URLs, local
paths, logs, credentials and full configuration. Both backup receipts and export
files are read back before success is displayed. Maintenance works with the
service offline and does not trigger fetches, training or downloads. There are
no restore, reset, purge or deletion controls.

## Logs

The Logs tab refreshes with service status, and Start Service selects it
automatically. It shows timestamps, severity, launcher lifecycle, H@H tunnel
reconnects, HTTP request results and recognized service activity. Follow latest
can be disabled to inspect older entries without scrolling to the end on update.
The text is read-only and selectable; errors and warnings have distinct colors.
Severity/component filters affect only the view. Copy Logs copies only the
visible sanitized entries; Follow latest can be paused independently.

Each managed run keeps up to 500 events in the ignored sibling
`data/launcher/config.log.json`. Storage is bounded, flushed at most once per
second for ordinary output and immediately for lifecycle transitions, and
retained after the UI closes or the service stops.
A new managed run replaces the previous run's history. Old already-running
workers need an owner-controlled restart to begin producing logs; manually
started external services have no attachable output.

Raw stdout/stderr is never saved or displayed. A bounded pipe reader maps known
output to fixed, allowlisted event summaries. Unknown output, traceback details,
URLs/query values, local paths, credentials and command lines are omitted;
health polling is silent. Unknown output is noted only once per run. The UI
validates the stored event codes and fields again before formatting them.

## Activity

Activity retains the Fetch Activity monitor for the current or most recent fetch in this server process,
including automatic refreshes, manual queries and detail-only enrichment. It
refreshes with service status without starting work or calculating recommendations.

- Run ID, trigger, local start time with UTC offset, elapsed time and age of the latest activity.
- Fetched, newly stored, enriched and error counts.
- Separate query and detail progress bars. Query completion is not overall completion.
- Phase overview for lists/covers, details/samples, optional model work and finalization.
- Frozen query plan with source, resume status, per-query results and page caps.
- Initial-page and catch-up budgets, selected-detail limits and extra sample pages.
- Up to 120 stage messages for listing, covers, details, samples, caching, parent
  metadata, model retraining and final recommendation refresh.

Page caps are upper bounds: queries can end early after catching up. Reaching the
configured page cap is completion, including when an old anchor has not been
reached. The rolling three-calendar-month publication cutoff is also a normal
completion boundary. Actual listing/metadata failures remain partial or failed.
Per-query completion reasons, the frozen publication cutoff, metadata batches
and explicit request-wait reasons remain visible. Detail progress
counts processed attempts, including failures; Enriched counts successful stores.
ETA remains unknown rather than estimating from variable remote and model work.
Long requests retain their current stage and show the age of the latest update.

The lightweight `/api/fetch-progress` endpoint only copies bounded in-memory
telemetry; it does not open the database or expose cookies, request URLs or raw
exception details. Query text and the current gallery title are visible locally
in this view, but not persisted as launcher logs. A new run replaces the previous
snapshot; server restart clears it. Connection failures clear live counts. Older
servers show a restart-required message. Restart the service yourself when ready
to enable this endpoint; updating the launcher alone cannot instrument an already
running old server.

## Background Lifecycle

The background worker holds a per-profile Windows mutex. It checks the actual
HTTP endpoint before starting, and distinguishes an ExhRec server from an
unrelated listener. `/api/health` is a cheap, read-only identity endpoint;
older servers are recognized conservatively by their ExhRec header and home
page. An exclusive local bind distinguishes unused ports from slow TCP refusals
on Windows before startup. Managed-service polling never binds the port while
the child may be creating its listener, and never calls expensive
recommendation/status endpoints.

A gated bootstrap waits until it has been assigned to a kill-on-close Windows
Job Object before importing the application or starting the H@H tunnel. A
random per-run named event requests shutdown. Job cleanup includes descendants
and is checked before reporting Stopped. Failure to establish containment
prevents startup. The existing `run_with_hath.py` remains the H@H supervisor.
The UI is not part of the service job, so closing it does not stop the worker.

## Automatic Startup

The startup checkbox and Apply button control a repo-specific entry under the
current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
This starts the service hidden **after that user signs in**, not before login,
and never opens the UI or browser automatically. No administrator privileges,
stored password, system service or scheduled task is required. The command
contains only the Python executable, launcher script and configuration path.
Enabling/disabling is independently read back. Other startup entries are not
modified. The real profile remains off until the owner explicitly enables it.

## Verification

`python tests/run_launcher_checks.py` retains synthetic fixtures and exercises
configuration, command construction, probes, native containment/singletons,
background startup/exit/stop, startup registry readback, Jobs state transitions,
process-death recovery, HTTP/client validation, native tray lifecycle and UI callbacks.
Pass `--all` to include the complete Python regression suite in the same
retained-data isolation harness.
Fixtures use ephemeral loopback ports and dummy credential files, never the
production database, real SSH connections or download jobs. GUI layout and
default-browser opening are additionally checked against a synthetic server.
Closing that UI was verified to preserve the service; the harness then stopped
its own service. Startup registration and its literal command are tested in an
isolated registry key. No real sign-out/reboot or production autostart activation
is performed by these checks.
