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

Parameters cover `run.ps1`: virtual environment, bind address, port, data
directory, proxy, visual encoder/device, H@H enablement, token-file path,
SSH-config path and SSH host. Advanced settings include SSH executable,
remote loopback port, reconnect delay and startup timeout. Blank optional
fields preserve the existing environment/default behavior. Secrets remain in
their existing files and are read only by the established H@H supervisor.

Configuration lives in ignored `data/launcher/config.json`. Saving retains a
timestamped backup and atomically replaces a validated UTF-8 JSON sibling.
Only paths and non-secret settings are saved. Proxy URLs containing credentials
are rejected. Runtime status contains fixed codes, never child output,
tokens, cookies or complete command lines.

## Recent Logs

The Recent Logs tab refreshes with service status, and Start Service selects it
automatically. It shows timestamps, severity, launcher lifecycle, H@H tunnel
reconnects, HTTP request results and recognized service activity. Follow latest
can be disabled to inspect older entries without scrolling to the end on update.
The text is read-only and selectable; errors and warnings have distinct colors.

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
