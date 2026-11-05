# Windows Launcher

## Interaction

Double-click `launch.vbs` for a console-free entry point using the repository's
`.venv-rocm` or `.venv` Python. Alternatively double-click `launcher.pyw`
(a working Python file association is required), or run
`.venv-rocm\Scripts\pythonw.exe launcher.pyw`. The UI and the service virtual
environment are independent. No third-party GUI dependency is needed.

The connection strip shows Stopped, Starting, Running, Stopping, Unresponsive,
Externally started, Port occupied, or Failed. Start, Stop, Open Home and Refresh operate on the
saved profile. Closing the window leaves a background service running.
Stop only signals this launcher's supervisor; it never kills a PID obtained
from a port scan or an old state file. Existing manually started servers are
detected but cannot be stopped or adopted. Stop them using their original
launcher before starting a managed replacement.

The six tabs are **Overview**, **Connections**, **Activity**, **Environment**,
**Storage**, and **Logs**. This is an operational console, not a second gallery
browser: content review, feedback, recommendation policy and download-item
operations remain in the web UI.

Overview distinguishes verified health from process ownership and shows the
supervisor uptime, last status check, launch-profile agreement, unsaved edits,
fetch summary and latest retained sanitized error. Uptime is not inferred for
external services. New supervisors record a digest of their launch profile;
older already-running supervisors show profile agreement as unknown until the
next owner-controlled restart. The digest does not prove database overrides
or inherited environment values. Local revision means the checkout revision,
not the revision of an already-running backend.

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
background startup/exit/stop, startup registry readback and UI callbacks.
Fixtures use ephemeral loopback ports and dummy credential files, never the
production database, real SSH connections or download jobs. GUI layout and
default-browser opening are additionally checked against a synthetic server.
Closing that UI was verified to preserve the service; the harness then stopped
its own service. Startup registration and its literal command are tested in an
isolated registry key. No real sign-out/reboot or production autostart activation
is performed by these checks.
