"""Explicit, read-only operational checkup. Results are timestamped observations."""
from scripts import launcher_ops as ops
from scripts.launcher_core import LauncherError, config_digest
from scripts.launcher_logs import read_log_snapshot
from scripts.launcher_runtime import compare_source, read_runtime_identity
from exh_rec.runtime_identity import capture_source


CHECKS = (("service", "Local service"), ("source", "Startup source"), ("files", "Launch files"),
          ("environment", "Selected environment"), ("proxy", "Proxy TCP"),
          ("tunnel", "SSH tunnel evidence"), ("observer", "Observer receipts"))


FILE_ERRORS = {
    "venv-python-missing": "Select a service environment containing Scripts/python.exe in Environment.",
    "hath-token-file-missing": "Select the existing H@H token file in Connections; do not paste its contents.",
    "hath-ssh-config-missing": "Select the existing SSH configuration file in Connections.",
    "ssh-executable-missing": "Select an existing SSH executable in Connections.",
}


def service_key(status):
    return (status.get("state"), status.get("health"), status.get("managed") is True, status.get("run_id"),
            status.get("runtime_identity", {}).get("started_at"))


def run_checkup(config, controller, emit=lambda row: None):
    report = {"rows": [], "profile_digest": config_digest(config), "started_at": ops.timestamp()}

    def row(key, outcome, summary, action):
        value = {"key": key, "component": dict(CHECKS)[key], "outcome": outcome,
                 "summary": summary, "action": action, "checked_at": ops.timestamp()}
        report["rows"].append(value)
        emit(value)

    status = {}
    try:
        status = controller.status(config)
        state, health = status.get("state"), status.get("health")
        if state in {"running", "external"} and health == "ready":
            row("service", "pass", "Identity endpoint verified",
                "External service: read-only monitoring; use its original launcher for lifecycle control."
                if state == "external" else "No action needed for local API readiness.")
        elif state in {"occupied", "failed", "unresponsive"}:
            row("service", "fail", {"occupied": "Port occupied by unverified listener", "failed": "Managed service failed",
                                    "unresponsive": "Managed service is unresponsive"}[state],
                "Inspect Logs and Environment. Do not terminate a process based only on port ownership.")
        elif health == "legacy":
            row("service", "unknown", "Legacy service; health unavailable", "Use the original launcher for a restart when ready.")
        else:
            row("service", "unknown", "Offline or transitioning", "Start Service when ready, or wait for the current transition.")
    except Exception:
        row("service", "unknown", "Readiness check unavailable", "Refresh status and retry; no service was started or stopped.")

    local = capture_source()
    report["local_source"] = local
    runtime = (read_runtime_identity(status["home_url"]) if status.get("state") in {"running", "external"}
               and status.get("home_url") else {"availability": "offline"})
    status["runtime_identity"] = runtime
    report["service_key"] = service_key(status)
    comparison = compare_source(local, runtime)
    if comparison == "changed":
        row("source", "warn", "Source differs from startup snapshot", "Plan an owner-controlled restart after checking active tasks in the web UI.")
    elif comparison == "matching":
        different_revision = local.get("revision") != runtime.get("revision")
        row("source", "pass", "Source matches; revisions differ" if different_revision else "Backend source matches startup snapshot",
            "Startup: " + ops.local_time(runtime["started_at"])
            + ". Backend Python sources only; web assets, settings and dependencies are not covered.")
    else:
        row("source", "unknown", "Startup source comparison unavailable",
            "Older services need an owner-controlled restart to expose startup identity; offline or incomplete snapshots remain unknown.")

    try:
        config.validate(check_files=True)
        row("files", "pass", "Required launch files exist", "Existence only: credentials were not opened and SSH authentication was not tested.")
    except LauncherError as error:
        row("files", "fail", "Required launch configuration unavailable", FILE_ERRORS.get(str(error), "Review saved parameters in Connections and Environment."))
    except Exception:
        row("files", "unknown", "Launch file check unavailable", "Review file access for the saved profile in Connections and Environment.")

    try:
        environment = ops.inspect_environment(config)
        report["environment"] = environment
        missing = environment.get("missing_required", [])
        row("environment", "fail" if missing else "pass", "Required packages missing" if missing else "Required base packages present",
            "Check the package versions in Environment. This probe checks the selected Python, not the running backend or its model.")
    except Exception:
        row("environment", "unknown", "Environment probe failed or timed out", "Check the selected Python in Environment; nothing was installed or downloaded.")

    try:
        proxy = ops.check_proxy(config)
        report["proxy"] = proxy
        proxy_state = proxy.get("state")
        row("proxy", "fail" if proxy_state == "unreachable" else "unknown",
            {"reachable": "TCP reachable; upstream unverified", "unreachable": "TCP connection failed",
             "inherited": "Inherited/default proxy unverified"}.get(proxy_state, "Proxy state unavailable"),
            "Check the saved proxy and its local listener. Upstream access and authentication were not tested.")
    except Exception:
        row("proxy", "unknown", "Proxy check unavailable", "Review the saved proxy in Environment; no remote content was requested.")

    if not config.hath_enabled:
        row("tunnel", "skip", "Disabled in saved profile", "No SSH connection was opened.")
        row("observer", "skip", "Disabled in saved profile", "The observer database was not read.")
    else:
        try:
            snapshot = read_log_snapshot(controller.path)
            entries = (snapshot.get("entries", []) if status.get("managed")
                       and snapshot.get("run_id") == status.get("run_id") else [])
            events = [entry for entry in entries if entry.get("code") in {
                "hath.starting", "hath.retry", "hath.error", "ssh.auth-error", "ssh.connection-error"}]
            event = events[-1] if events else None
            row("tunnel", "warn" if event and event["code"] != "hath.starting" else "unknown",
                {"hath.starting": "Start requested; connection unconfirmed", "hath.retry": "Last evidence: reconnect scheduled",
                 "hath.error": "Last evidence: integration error", "ssh.auth-error": "Last evidence: authentication error",
                 "ssh.connection-error": "Last evidence: connection error"}.get(event["code"] if event else "", "No current-run tunnel proof"),
                ("Last evidence: " + ops.local_time(event["at"]) + ". " if event else "")
                + "Inspect Connections and Logs; neither a start request nor a receipt proves a live SSH tunnel.")
        except Exception:
            row("tunnel", "unknown", "Tunnel evidence unavailable", "Inspect Connections and Logs; no SSH connection was opened.")
        try:
            observer = ops.observer_status(config)
            report["observer"] = observer
            row("observer", "unknown", f"Recorded clients: {observer['clients']}",
                "Last receipt: " + ops.local_time(observer.get("last_seen_at"))
                + ". Saved-database history only; not a live connection check.")
        except Exception:
            row("observer", "unknown", "Observer history unavailable", "Check the saved data directory and database access in Storage.")
    report["finished_at"] = ops.timestamp()
    return report
