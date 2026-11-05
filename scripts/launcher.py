"""Native desktop launcher and hidden background supervisor entry point."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import queue
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.launcher_core import (
    DEFAULT_CONFIG, LaunchConfig, LauncherController, LauncherError,
    load_config, save_config, startup_registration, supervise_background,
)
from scripts.launcher_windows import LauncherWindowsError
from scripts.launcher_logs import read_log_snapshot
from scripts.launcher_fetch import FetchActivityView, read_fetch_activity
from scripts.launcher_views import OperationsView
from scripts import launcher_ops as ops


ERRORS = {
    "invalid-config": "Configuration is not valid.",
    "config-read-failed": "Cannot read the saved profile. Saving will retain a backup.",
    "config-save-failed": "Cannot save the profile.",
    "invalid-number": "Enter whole numbers for ports and timeouts.",
    "invalid-port": "Ports must be between 1 and 65535.",
    "invalid-timeout": "Startup timeout: 10-1800 seconds; reconnect delay: 1-300 seconds.",
    "invalid-bind-address": "Enter an IPv4 bind address.",
    "invalid-encoder": "Select a supported visual encoder.",
    "invalid-device": "Select a supported device.",
    "invalid-proxy": "Enter a proxy URL with a scheme, host and port.",
    "proxy-credentials-not-supported": "Proxy credentials cannot be stored in this profile.",
    "venv-required": "Select a service virtual environment.",
    "venv-python-missing": "The selected environment has no Scripts/python.exe.",
    "hath-files-required": "Select both H@H token and SSH configuration files.",
    "hath-token-file-missing": "The H@H token file does not exist.",
    "hath-ssh-config-missing": "The H@H SSH configuration does not exist.",
    "ssh-executable-missing": "The SSH executable does not exist.",
    "invalid-ssh-host": "Enter an SSH alias or user@host, without shell syntax.",
    "port-occupied": "The configured port is occupied by another application.",
    "startup-timeout": "The service did not become ready before the startup timeout.",
    "service-exited": "The service exited. Check the environment and saved parameters.",
    "launch-failed": "The service could not be started.",
    "cleanup-failed": "Shutdown cleanup could not be verified. Refresh before starting again.",
    "stop-failed": "The managed process group could not be stopped.",
    "containment-unavailable": "Windows process containment could not be established. Nothing was started.",
    "stop-timeout": "Shutdown is taking longer than expected. Refresh before retrying.",
    "no-managed-service": "This launcher does not own a running service.",
    "runtime-not-ready": "The background worker is still initializing. Refresh shortly.",
    "service-not-ready": "The service is not ready to open.",
    "startup-entry-conflict": "An unexpected startup entry exists under this launcher's name. It was not changed.",
    "startup-command-too-long": "The startup command exceeds Windows' Run-entry length limit.",
    "startup-save-failed": "Cannot update automatic startup.",
    "startup-readback-failed": "Cannot verify automatic startup.",
    "maintenance-path-unavailable": "Maintenance requires a local directory without links or reparse points.",
    "database-unavailable": "The saved profile database is not available.",
    "database-check-failed": "Database verification failed. Existing data was not changed.",
    "environment-check-failed": "The selected environment could not be verified.",
    "maintenance-timeout": "Maintenance timed out. An incomplete artifact may have been retained.",
    "backup-readback-failed": "Backup receipt could not be verified.",
    "diagnostic-readback-failed": "Diagnostic export could not be verified.",
}


def error_message(error) -> str:
    return ERRORS.get(str(error), "The operation could not be completed. No child output was recorded.")


class LauncherApp:
    def __init__(self, root, config_path: Path = DEFAULT_CONFIG):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.path = config_path.resolve()
        self.controller = LauncherController(self.path)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="launcher")
        self.results = queue.Queue()
        self.busy = False
        self.closed = False
        self.timers = set()
        self.polling = False
        self.config_generation = 0
        self.log_lines = None
        self.all_log_lines = []
        self.logs_external = False
        self.proxy_result = {}
        self.observer_result = {}
        self.latest = {"state": "checking", "managed": False}
        self.notice = tk.StringVar(value="")
        try:
            self.config = load_config(self.path)
        except (LauncherError, LauncherWindowsError) as error:
            self.config = LaunchConfig.defaults()
            self.notice.set(error_message(error))
        root.title("ExHentai Recommender Launcher")
        root.geometry("880x760")
        root.minsize(780, 760)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground="#59636b")
        style.configure("Error.TLabel", foreground="#b42332")
        style.configure("TButton", padding=(12, 7))
        style.configure("TNotebook.Tab", padding=(10, 7))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        heading = ttk.Frame(root, padding=(20, 16, 20, 12))
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="ExHentai Recommender", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.state_label = ttk.Label(heading, text="Checking", style="Status.TLabel")
        self.state_label.grid(row=0, column=1, sticky="e", padx=(20, 0))
        self.address = tk.StringVar(value=self.config.home_url())
        ttk.Label(heading, textvariable=self.address, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.ownership = ttk.Label(heading, text="", style="Muted.TLabel")
        self.ownership.grid(row=1, column=1, sticky="e", pady=(7, 0))

        actions = ttk.Frame(root, padding=(20, 0, 20, 14))
        actions.grid(row=1, column=0, sticky="ew")
        self.start_button = ttk.Button(actions, text="Start Service", command=self.start)
        self.stop_button = ttk.Button(actions, text="Stop Service", command=self.stop)
        self.open_button = ttk.Button(actions, text="Open Home", command=self.open_home)
        self.refresh_button = ttk.Button(actions, text="Refresh", command=self.refresh)
        for column, button in enumerate((self.start_button, self.stop_button, self.open_button, self.refresh_button)):
            button.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column < 3 else 0)
            actions.columnconfigure(column, weight=1)

        self.notebook = notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=20)
        self.operations = OperationsView(self)
        basic = self.operations.connection_form
        advanced = self.operations.environment_form
        self.logs_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.logs_tab, text="Logs")
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(1, weight=1)
        log_toolbar = ttk.Frame(self.logs_tab)
        log_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        log_toolbar.columnconfigure(0, weight=1)
        self.log_status = ttk.Label(log_toolbar, text="No recent logs.", style="Muted.TLabel")
        self.log_status.grid(row=0, column=0, sticky="w", columnspan=4)
        self.follow_logs = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_toolbar, text="Follow latest", variable=self.follow_logs,
                        command=self.follow_latest_log).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.log_level = tk.StringVar(value="All levels")
        self.log_source = tk.StringVar(value="All components")
        for column, variable, values in ((1, self.log_level, ("All levels", "INFO", "WARN", "ERROR")),
                                          (2, self.log_source, ("All components", "launcher", "service", "H@H", "SSH", "HTTP"))):
            selector = ttk.Combobox(log_toolbar, textvariable=variable, values=values, state="readonly", width=17)
            selector.grid(row=1, column=column, padx=(8, 0), pady=(8, 0))
            selector.bind("<<ComboboxSelected>>", lambda _: self.render_logs(self.all_log_lines, external=self.logs_external))
        ttk.Button(log_toolbar, text="Copy Logs", command=self.copy_logs).grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        self.log_text = tk.Text(self.logs_tab, wrap="word", state="disabled", font=("Consolas", 10),
                                width=1, height=1, background="#ffffff", foreground="#24292f",
                                relief="flat", padx=10, pady=8)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(self.logs_tab, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("error", foreground="#b42332")
        self.log_text.tag_configure("warning", foreground="#8a5715")
        self.fetch_activity = FetchActivityView(notebook, tk, ttk)
        notebook.tab(self.fetch_activity.frame, text="Activity")
        for frame in (self.operations.overview, self.operations.connections, self.fetch_activity.frame,
                      self.operations.environment, self.operations.storage, self.logs_tab):
            notebook.insert("end", frame)
        notebook.select(self.operations.overview)
        self.variables = {}
        self.form_widgets = []
        values = asdict(self.config)
        for name, value in values.items():
            self.variables[name] = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=str(value))
        for frame in (basic, advanced):
            frame.columnconfigure(1, weight=1)

        integration = ttk.Checkbutton(basic, text="Enable H@H integration", variable=self.variables["hath_enabled"], command=self.update_hath_fields)
        integration.grid(row=0, column=0, columnspan=3, sticky="w", pady=(4, 3))
        self.form_widgets.append((integration, "normal"))
        self.hath_widgets = []
        self.field(basic, 1, "H@H token file", "hath_token_file", browse="file", hath=True)
        self.field(basic, 2, "SSH configuration", "hath_ssh_config", browse="file", hath=True)
        self.field(basic, 3, "SSH host alias", "hath_ssh_host", hath=True)
        self.field(basic, 4, "SSH executable", "ssh_executable", browse="file", hath=True)
        tunnel_settings = ttk.Frame(basic)
        tunnel_settings.grid(row=5, column=0, columnspan=3, sticky="ew")
        for column, (label, name) in enumerate((("Remote port", "remote_port"), ("Reconnect (s)", "reconnect_seconds"))):
            section = ttk.Frame(tunnel_settings)
            section.grid(row=0, column=column, sticky="ew", padx=(0, 12) if column == 0 else 0)
            tunnel_settings.columnconfigure(column, weight=1, uniform="tunnel")
            section.columnconfigure(1, weight=1)
            self.field(section, 0, label, name, hath=True)
        self.field(advanced, 0, "Service environment", "venv_path", browse="directory")
        self.field(advanced, 1, "Data directory", "data_dir", browse="directory")
        self.field(advanced, 2, "Bind address", "host", choices=("127.0.0.1", "0.0.0.0"), editable=True)
        self.field(advanced, 3, "Service port", "port")
        self.field(advanced, 4, "Proxy", "proxy")
        self.field(advanced, 5, "Visual encoder", "visual_encoder", choices=("", "simple", "dinov2"))
        self.field(advanced, 6, "DINO device", "dino_device", choices=("", "auto", "cpu", "cuda", "rocm"))
        self.field(advanced, 7, "Startup timeout (s)", "startup_timeout")

        footer = ttk.Frame(root, padding=(20, 14, 20, 18))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.save_button = ttk.Button(footer, text="Save Parameters", command=self.save)
        self.save_button.grid(row=0, column=1, sticky="e")
        startup = self.operations.startup_frame
        self.auto_start = tk.BooleanVar(value=False)
        self.startup_check = ttk.Checkbutton(startup, text="Start service in background when I sign in", variable=self.auto_start)
        self.startup_check.grid(row=0, column=0, sticky="w")
        self.startup_button = ttk.Button(startup, text="Apply Startup", command=self.apply_startup)
        self.startup_button.grid(row=0, column=1, sticky="e")
        ttk.Label(footer, textvariable=self.notice, wraplength=560, style="Error.TLabel").grid(
            row=0, column=0, sticky="ew", padx=(0, 10))
        for variable in self.variables.values():
            variable.trace_add("write", lambda *_: self.render_profile_edits())
        self.update_hath_fields()
        try:
            self.auto_start.set(startup_registration(self.path).enabled())
        except (LauncherError, LauncherWindowsError, OSError) as error:
            self.notice.set(error_message(error))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.schedule(50, self.refresh)
        self.schedule(100, self.drain)
        self.schedule(1500, self.poll_status)

    def schedule(self, delay, callback):
        def run():
            self.timers.discard(timer)
            if not self.closed:
                callback()
        timer = self.root.after(delay, run)
        self.timers.add(timer)

    def field(self, frame, row, label, name, browse=None, choices=None, editable=False, hath=False):
        ttk = self.ttk
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=5)
        if choices is not None:
            widget = ttk.Combobox(frame, textvariable=self.variables[name], values=choices,
                                  state="normal" if editable else "readonly")
            mode = "normal" if editable else "readonly"
        else:
            widget = ttk.Entry(frame, textvariable=self.variables[name])
            mode = "normal"
        widget.grid(row=row, column=1, columnspan=1 if browse else 2, sticky="ew", pady=5)
        self.form_widgets.append((widget, mode))
        if hath:
            self.hath_widgets.append((widget, mode))
        if browse:
            button = ttk.Button(frame, text="Browse...", command=lambda: self.browse(name, browse))
            button.grid(row=row, column=2, padx=(8, 0), pady=5)
            self.form_widgets.append((button, "normal"))
            if hath:
                self.hath_widgets.append((button, "normal"))

    def browse(self, name, kind):
        from tkinter import filedialog
        value = filedialog.askdirectory(parent=self.root) if kind == "directory" else filedialog.askopenfilename(parent=self.root)
        if value:
            self.variables[name].set(value)

    def collect(self):
        payload = {name: variable.get() for name, variable in self.variables.items()}
        try:
            for name in ("schema", "port", "remote_port", "reconnect_seconds", "startup_timeout"):
                payload[name] = int(payload[name])
        except ValueError:
            raise LauncherError("invalid-number") from None
        return LaunchConfig.from_dict(payload)

    def profile_dirty(self):
        return any(self.variables[name].get() != (value if isinstance(value, bool) else str(value))
                   for name, value in asdict(self.config).items())

    def render_profile_edits(self):
        self.operations.overview_values["edits"].set("Unsaved changes" if self.profile_dirty() else "No unsaved changes")

    def clear_checks(self):
        self.proxy_result = {}
        self.observer_result = {}
        self.operations.clear_checks()

    def check_proxy(self):
        config = self.config
        self.notice.set("Checking saved proxy TCP endpoint...")
        def done(result):
            self.proxy_result = result
            self.notice.set("Proxy check finished. This does not verify upstream access.")
        self.submit(lambda: ops.check_proxy(config), done)

    def check_observer(self):
        config = self.config
        self.notice.set("Reading observer timestamps from the saved profile database...")
        def done(result):
            self.observer_result = result
            self.notice.set("Observer timestamps read. Tunnel connectivity is not assumed.")
        self.submit(lambda: ops.observer_status(config), done)

    def check_environment(self):
        config = self.config
        self.notice.set("Checking selected environment (up to 20 seconds)...")
        def done(result):
            self.operations.render_environment(result)
            self.notice.set("Selected environment checked; running-service settings may differ.")
        self.submit(lambda: ops.inspect_environment(config), done)

    def scan_storage(self):
        config = self.config
        self.notice.set("Scanning saved profile storage...")
        def done(result):
            self.operations.render_storage(result)
            self.notice.set("Storage scan finished. Partial sizes are lower bounds.")
        self.submit(lambda: ops.inspect_storage(config), done)

    def check_database(self):
        config = self.config
        self.notice.set("Checking saved profile database read-only...")
        def done(result):
            self.operations.database_result.set("Database quick_check passed: " + ops.local_time(result["checked_at"]))
            self.notice.set("Read-only database check passed.")
        self.submit(lambda: ops.check_database(config), done)

    def create_backup(self):
        from tkinter import messagebox
        if self.busy:
            return
        config = self.config
        if not messagebox.askyesno("Create Private Backup", "Create a verified SQLite backup of the saved profile database?\n\n"
                                  "It includes private settings and saved authentication. Keep it private.\n\n"
                                  + str(ops.data_directory(config) / "backups"), parent=self.root):
            return
        self.notice.set("Creating and independently checking a private database backup...")
        def done(result):
            self.operations.backup_summary.set("Last backup verified at creation: " + ops.local_time(result["at"]))
            self.operations.maintenance_result.set("Verified backup: " + result["path"])
            self.notice.set("Private backup verified. Existing data was not replaced.")
        self.submit(lambda: ops.backup_database(config), done)

    def export_diagnostics(self):
        config, status = self.config, dict(self.latest)
        self.notice.set("Exporting allowlisted operational diagnostics...")
        def done(result):
            self.operations.maintenance_result.set("Diagnostics: " + result["path"])
            self.notice.set("Diagnostic export verified. No gallery text, credentials, addresses or local paths inside.")
        self.submit(lambda: ops.export_diagnostics(config, status), done)

    def copy_logs(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))
        self.notice.set("Visible sanitized logs copied.")

    def save(self):
        try:
            config = self.collect()
            save_config(config, self.path)
            self.config = config
            self.config_generation += 1
            self.clear_checks()
            self.notice.set("Parameters saved.")
            self.refresh()
            return config
        except (LauncherError, LauncherWindowsError, OSError) as error:
            self.notice.set(error_message(error))
            return None

    def update_hath_fields(self):
        managed = self.latest.get("managed", False)
        for widget, mode in self.hath_widgets:
            widget.configure(state=mode if self.variables["hath_enabled"].get() and not managed and not self.busy else "disabled")

    def submit(self, operation, callback=None):
        if self.busy:
            return
        self.busy = True
        self.render_status(self.latest)

        def work():
            try:
                result = operation()
                self.results.put(("operation", result, callback, None, None))
            except Exception as error:
                self.results.put(("operation", None, callback, error_message(error), None))
        self.executor.submit(work)

    def start(self):
        config = self.save()
        if config is not None:
            self.notebook.select(self.logs_tab)
            self.notice.set("Starting service...")
            self.submit(lambda: self.controller.start(config),
                        lambda result: self.notice.set("Service already running." if result == "already-running" else "Background start requested."))

    def stop(self):
        from tkinter import messagebox
        if not messagebox.askyesno("Stop Service", "Stop the managed server and its H@H tunnel? Active jobs may be interrupted.", parent=self.root):
            return
        self.notice.set("Stopping service...")
        self.submit(self.controller.stop, lambda _: self.notice.set("Service stopped."))

    def open_home(self):
        self.submit(lambda: self.controller.open_home(self.config),
                    lambda opened: self.notice.set("" if opened else "Windows could not open the default browser."))

    def apply_startup(self):
        enabled = self.auto_start.get()
        try:
            if enabled:
                config = self.collect()
                config.validate(check_files=True)
                save_config(config, self.path)
                self.config = config
                self.config_generation += 1
                self.clear_checks()
            registration = startup_registration(self.path)
            registration.set_enabled(enabled)
            self.auto_start.set(registration.enabled())
            self.notice.set("Automatic startup enabled." if enabled else "Automatic startup disabled.")
        except (LauncherError, LauncherWindowsError, OSError) as error:
            try:
                self.auto_start.set(startup_registration(self.path).enabled())
            except (LauncherError, LauncherWindowsError, OSError):
                pass
            self.notice.set(error_message(error))

    def refresh(self):
        if self.closed or self.polling or self.busy:
            return
        self.polling = True
        config = self.config
        generation = self.config_generation

        def work():
            try:
                status = self.controller.status(config)
                status["log_snapshot"] = read_log_snapshot(self.path)
                snapshot = status["log_snapshot"]
                status["logs"] = ([entry["line"] for entry in snapshot["entries"]] if snapshot["availability"] != "unavailable"
                                  else ["Recent logs unavailable."])
                status["fetch_activity"] = (read_fetch_activity(status["home_url"])
                                            if status["state"] in {"running", "external"} else {"availability": "offline"})
                status["checked_at"] = ops.timestamp()
                self.results.put(("status", status, None, None, generation))
            except Exception as error:
                self.results.put(("status", None, None, error_message(error), generation))
        self.executor.submit(work)

    def poll_status(self):
        if self.closed:
            return
        self.refresh()
        self.schedule(1500, self.poll_status)

    def drain(self):
        if self.closed:
            return
        while not self.results.empty():
            kind, result, callback, error, generation = self.results.get_nowait()
            if kind == "status":
                self.polling = False
                if generation != self.config_generation:
                    self.refresh()
                    continue
                if result:
                    self.latest = result
                if error:
                    self.notice.set(error)
                    self.latest = {**self.latest, "state": "checking", "health": "unknown",
                                   "fetch_activity": {"availability": "unavailable"}}
            else:
                self.busy = False
                if error:
                    self.notice.set(error)
                elif callback:
                    callback(result)
                self.refresh()
            self.render_status(self.latest)
        self.schedule(100, self.drain)

    def render_status(self, status):
        state = status.get("state", "checking")
        names = {"checking": "Checking", "stopped": "Stopped", "starting": "Starting", "stopping": "Stopping",
                 "running": "Running", "external": "Externally Started", "occupied": "Port Occupied", "failed": "Failed",
                 "unresponsive": "Unresponsive"}
        self.state_label.configure(text=names.get(state, "Unknown"))
        self.address.set(status.get("home_url", self.config.home_url()))
        self.ownership.configure(text="Managed" if status.get("managed") else "Not managed" if state == "external" else "")
        managed = status.get("managed", False)
        self.start_button.configure(state="disabled" if self.busy or managed or state in {"starting", "stopping", "running", "external"} else "normal")
        self.stop_button.configure(state="normal" if managed and not self.busy and state != "stopping" else "disabled")
        self.open_button.configure(state="normal" if state in {"running", "external"} and not self.busy else "disabled")
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        self.save_button.configure(state="disabled" if self.busy or managed else "normal")
        self.startup_button.configure(state="disabled" if self.busy else "normal")
        self.startup_check.configure(state="disabled" if self.busy else "normal")
        for widget, mode in self.form_widgets:
            widget.configure(state="disabled" if managed or self.busy else mode)
        self.update_hath_fields()
        self.operations.render(status, self.config, dirty=self.profile_dirty())
        if "logs" in status:
            self.render_logs(status["logs"], external=state == "external")
        if "fetch_activity" in status:
            self.fetch_activity.render(status["fetch_activity"])
        if state == "failed" and status.get("error"):
            self.notice.set(error_message(status["error"]))

    def render_logs(self, lines, external=False):
        self.logs_external = external
        self.all_log_lines = list(lines)
        level, source = self.log_level.get(), self.log_source.get()
        visible = [line for line in lines if (level == "All levels" or f"  {level:<5}  " in line)
                   and (source == "All components" or f"[{source}]" in line)]
        self.log_status.configure(text="External service: previous managed logs only." if external and lines else
                                  "External service: logs unavailable." if external else
                                  f"{len(visible)} / {len(lines)} recent entries" if lines else "No recent logs.")
        if visible == self.log_lines:
            return
        previous_view = self.log_text.yview()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in visible:
            tag = "error" if "  ERROR  " in line else "warning" if "  WARN   " in line else ""
            self.log_text.insert("end", line + "\n", tag)
        self.log_text.configure(state="disabled")
        self.log_lines = list(visible)
        if self.follow_logs.get():
            self.follow_latest_log()
        else:
            self.log_text.yview_moveto(previous_view[0])

    def follow_latest_log(self):
        if self.follow_logs.get():
            self.log_text.see("end")

    def close(self):
        self.closed = True
        for timer in self.timers:
            self.root.after_cancel(timer)
        self.timers.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows launcher for ExHentai Recommender")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args(argv)
    if args.background:
        try:
            return supervise_background(args.config.resolve())
        except Exception:
            return 1
    import tkinter as tk
    root = tk.Tk()
    LauncherApp(root, args.config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
