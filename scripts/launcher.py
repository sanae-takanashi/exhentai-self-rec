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
        self.polling = False
        self.config_generation = 0
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
        style.configure("TNotebook.Tab", padding=(14, 7))
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

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=20)
        basic = ttk.Frame(notebook, padding=18)
        advanced = ttk.Frame(notebook, padding=18)
        notebook.add(basic, text="Service & H@H")
        notebook.add(advanced, text="Advanced")
        self.variables = {}
        self.form_widgets = []
        values = asdict(self.config)
        for name, value in values.items():
            self.variables[name] = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=str(value))
        for frame in (basic, advanced):
            frame.columnconfigure(1, weight=1)

        self.field(basic, 0, "Service environment", "venv_path", browse="directory")
        self.field(basic, 1, "Bind address", "host", choices=("127.0.0.1", "0.0.0.0"), editable=True)
        self.field(basic, 2, "Service port", "port")
        ttk.Separator(basic).grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)
        integration = ttk.Checkbutton(basic, text="Enable H@H integration", variable=self.variables["hath_enabled"], command=self.update_hath_fields)
        integration.grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.form_widgets.append((integration, "normal"))
        self.hath_widgets = []
        self.field(basic, 5, "H@H token file", "hath_token_file", browse="file", hath=True)
        self.field(basic, 6, "SSH configuration", "hath_ssh_config", browse="file", hath=True)
        self.field(basic, 7, "SSH host alias", "hath_ssh_host", hath=True)
        self.field(advanced, 0, "Data directory", "data_dir", browse="directory")
        self.field(advanced, 1, "Proxy", "proxy")
        self.field(advanced, 2, "Visual encoder", "visual_encoder", choices=("", "simple", "dinov2"))
        self.field(advanced, 3, "DINO device", "dino_device", choices=("", "auto", "cpu", "cuda", "rocm"))
        self.field(advanced, 4, "SSH executable", "ssh_executable", browse="file", hath=True)
        self.field(advanced, 5, "H@H remote port", "remote_port", hath=True)
        self.field(advanced, 6, "Reconnect delay (s)", "reconnect_seconds", hath=True)
        self.field(advanced, 7, "Startup timeout (s)", "startup_timeout")

        footer = ttk.Frame(root, padding=(20, 14, 20, 18))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.save_button = ttk.Button(footer, text="Save Parameters", command=self.save)
        self.save_button.grid(row=0, column=1, sticky="e", pady=(0, 12))
        ttk.Separator(footer).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.auto_start = tk.BooleanVar(value=False)
        self.startup_check = ttk.Checkbutton(footer, text="Start service in background when I sign in", variable=self.auto_start)
        self.startup_check.grid(row=2, column=0, sticky="w")
        self.startup_button = ttk.Button(footer, text="Apply Startup", command=self.apply_startup)
        self.startup_button.grid(row=2, column=1, sticky="e")
        ttk.Label(footer, textvariable=self.notice, wraplength=800, style="Error.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.update_hath_fields()
        try:
            self.auto_start.set(startup_registration(self.path).enabled())
        except (LauncherError, LauncherWindowsError, OSError) as error:
            self.notice.set(error_message(error))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.refresh)
        root.after(100, self.drain)
        root.after(1500, self.poll_status)

    def field(self, frame, row, label, name, browse=None, choices=None, editable=False, hath=False):
        ttk = self.ttk
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=8)
        if choices is not None:
            widget = ttk.Combobox(frame, textvariable=self.variables[name], values=choices,
                                  state="normal" if editable else "readonly")
            mode = "normal" if editable else "readonly"
        else:
            widget = ttk.Entry(frame, textvariable=self.variables[name])
            mode = "normal"
        widget.grid(row=row, column=1, columnspan=1 if browse else 2, sticky="ew", pady=8)
        self.form_widgets.append((widget, mode))
        if hath:
            self.hath_widgets.append((widget, mode))
        if browse:
            button = ttk.Button(frame, text="Browse...", command=lambda: self.browse(name, browse))
            button.grid(row=row, column=2, padx=(8, 0), pady=8)
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

    def save(self):
        try:
            config = self.collect()
            save_config(config, self.path)
            self.config = config
            self.config_generation += 1
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
                self.results.put(("status", self.controller.status(config), None, None, generation))
            except Exception as error:
                self.results.put(("status", None, None, error_message(error), generation))
        self.executor.submit(work)

    def poll_status(self):
        if self.closed:
            return
        self.refresh()
        self.root.after(1500, self.poll_status)

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
            else:
                self.busy = False
                if error:
                    self.notice.set(error)
                elif callback:
                    callback(result)
                self.refresh()
            self.render_status(self.latest)
        self.root.after(100, self.drain)

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
        if state == "failed" and status.get("error"):
            self.notice.set(error_message(status["error"]))

    def close(self):
        self.closed = True
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
