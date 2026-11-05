"""Native operational views. No gallery controls or application business settings."""
from datetime import datetime, timezone

from scripts.launcher_core import ROOT
from scripts.launcher_fetch import duration
from scripts.launcher_ops import local_time


def size_text(value):
    value = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class OperationsView:
    def __init__(self, app):
        self.app = app
        tk, ttk, notebook = app.tk, app.ttk, app.notebook
        self.overview = ttk.Frame(notebook, padding=16)
        self.connections = ttk.Frame(notebook, padding=12)
        self.environment = ttk.Frame(notebook, padding=12)
        self.storage = ttk.Frame(notebook, padding=16)
        for frame, name in ((self.overview, "Overview"), (self.connections, "Connections"),
                            (self.environment, "Environment"), (self.storage, "Storage")):
            notebook.add(frame, text=name)
            frame.columnconfigure(0, weight=1)

        ttk.Label(self.overview, text="Service overview", style="Status.TLabel").grid(row=0, sticky="w", pady=(0, 12))
        self.overview_values = {}
        for row, (key, label) in enumerate((("health", "Readiness"), ("owner", "Ownership"), ("uptime", "Supervisor uptime"),
                                           ("checked", "Last status check"), ("profile", "Saved / running profile"),
                                           ("edits", "Form changes"), ("activity", "Background activity"),
                                           ("failure", "Latest retained error")), start=1):
            section = ttk.Frame(self.overview)
            section.grid(row=row, sticky="ew", pady=7)
            section.columnconfigure(1, weight=1)
            ttk.Label(section, text=label, width=24, style="Muted.TLabel").grid(row=0, column=0, sticky="nw")
            value = tk.StringVar(value="Unknown")
            ttk.Label(section, textvariable=value, wraplength=460).grid(row=0, column=1, sticky="ew")
            self.overview_values[key] = value

        ttk.Label(self.connections, text="Connection diagnostics", style="Status.TLabel").grid(row=0, sticky="w")
        self.connection_scope = tk.StringVar(value="Saved profile; live tunnel state is not assumed.")
        ttk.Label(self.connections, textvariable=self.connection_scope, style="Muted.TLabel", wraplength=680).grid(
            row=1, sticky="ew", pady=(5, 8))
        self.connection_table = ttk.Treeview(self.connections, columns=("component", "state"), show="headings", height=5)
        self.connection_table.heading("component", text="Component")
        self.connection_table.heading("state", text="Observed state")
        self.connection_table.column("component", width=145, minwidth=130, stretch=False)
        self.connection_table.column("state", width=500, minwidth=200)
        self.connection_table.grid(row=2, sticky="ew")
        scroll = ttk.Scrollbar(self.connections, orient="horizontal", command=self.connection_table.xview)
        scroll.grid(row=3, sticky="ew")
        self.connection_table.configure(xscrollcommand=scroll.set)
        buttons = ttk.Frame(self.connections)
        buttons.grid(row=4, sticky="ew", pady=3)
        self.proxy_button = ttk.Button(buttons, text="Check Proxy TCP", command=app.check_proxy)
        self.observer_button = ttk.Button(buttons, text="Read Observer Status", command=app.check_observer)
        self.proxy_button.grid(row=0, column=0, padx=(0, 8))
        self.observer_button.grid(row=0, column=1)

        self.connection_form = ttk.Frame(self.connections)
        self.connection_form.grid(row=5, sticky="ew")
        self.connection_form.columnconfigure(1, weight=1)
        self.environment_form = ttk.Frame(self.environment)
        self.environment_form.grid(row=0, sticky="ew")
        self.environment_form.columnconfigure(1, weight=1)
        env_bar = ttk.Frame(self.environment)
        env_bar.grid(row=1, sticky="ew", pady=(7, 3))
        env_bar.columnconfigure(1, weight=1)
        self.environment_button = ttk.Button(env_bar, text="Check Environment", command=app.check_environment)
        self.environment_button.grid(row=0, column=0, sticky="w")
        self.revision = tk.StringVar(value="Local revision: " + self.local_revision())
        ttk.Label(env_bar, textvariable=self.revision, style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.environment_result = tk.StringVar(value="Selected Python: not checked. Runtime accelerator: unknown.")
        ttk.Label(self.environment, textvariable=self.environment_result, wraplength=680, justify="left").grid(
            row=2, sticky="ew", pady=(3, 8))
        self.startup_frame = ttk.Frame(self.environment)
        self.startup_frame.grid(row=3, sticky="ew")
        self.startup_frame.columnconfigure(0, weight=1)

        ttk.Label(self.storage, text="Saved profile storage", style="Status.TLabel").grid(row=0, sticky="w")
        self.storage_path = tk.StringVar(value="")
        ttk.Entry(self.storage, textvariable=self.storage_path, state="readonly").grid(row=1, sticky="ew", pady=(8, 10))
        self.storage_summary = tk.StringVar(value="Disk usage: not scanned")
        ttk.Label(self.storage, textvariable=self.storage_summary, wraplength=680).grid(row=2, sticky="ew", pady=(0, 10))
        self.storage_table = ttk.Treeview(self.storage, columns=("category", "size", "files", "coverage"), show="headings", height=6)
        for key, title, width in (("category", "Category", 240), ("size", "Size", 130), ("files", "Files", 95), ("coverage", "Coverage", 130)):
            self.storage_table.heading(key, text=title)
            self.storage_table.column(key, width=width, minwidth=70)
        self.storage_table.grid(row=3, sticky="ew")
        self.backup_summary = tk.StringVar(value="Last verified launcher backup: not checked")
        ttk.Label(self.storage, textvariable=self.backup_summary, wraplength=680).grid(row=4, sticky="ew", pady=10)
        maintenance = ttk.Frame(self.storage)
        maintenance.grid(row=5, sticky="ew")
        self.maintenance_buttons = []
        for column, (label, command) in enumerate((("Scan Usage", app.scan_storage), ("Check Database", app.check_database),
                                                  ("Create Backup", app.create_backup), ("Export Diagnostics", app.export_diagnostics))):
            button = ttk.Button(maintenance, text=label, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column < 3 else 0)
            maintenance.columnconfigure(column, weight=1)
            self.maintenance_buttons.append(button)
        self.database_result = tk.StringVar(value="Database check: not run")
        ttk.Label(self.storage, textvariable=self.database_result, wraplength=680).grid(row=6, sticky="ew", pady=10)
        self.maintenance_result = tk.StringVar(value="")
        ttk.Label(self.storage, textvariable=self.maintenance_result, wraplength=680).grid(row=7, sticky="ew")

    @staticmethod
    def local_revision():
        try:
            head = (ROOT / ".git/HEAD").read_text(encoding="ascii").strip()
            if head.startswith("ref: refs/heads/"):
                ref = head[5:]
                candidate = (ROOT / ".git" / ref).read_text(encoding="ascii").strip()
            else:
                candidate = head
            import re
            return candidate[:12] if re.fullmatch(r"[0-9a-f]{40,64}", candidate) else "unknown"
        except (OSError, UnicodeError):
            return "unknown"

    def render(self, status, config, *, dirty=False):
        state = status.get("state", "checking")
        managed = status.get("managed", False)
        health = status.get("health")
        ready = {"ready": "Ready (identity endpoint verified)", "legacy": "Legacy response; health endpoint unavailable",
                 "occupied": "Not ready / listener did not verify", "stopped": "Offline"}.get(health, "Not checked")
        values = self.overview_values
        values["health"].set(ready)
        values["owner"].set("Managed supervisor" if managed else "External service (read-only)" if state == "external" else "No managed supervisor")
        uptime = "Unknown"
        if managed and status.get("started_at"):
            try:
                started = datetime.fromisoformat(status["started_at"])
                if started.tzinfo is not None:
                    uptime = duration((datetime.now(timezone.utc) - started).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        values["uptime"].set(uptime if managed else "Not available")
        values["checked"].set(local_time(status.get("checked_at")))
        match = status.get("profile_match")
        profile = ("Matching launch profile" if match is True else "Changed since launch; restart required" if match is False
                   else "Unknown (older supervisor)" if managed else "Unknown (external service)" if state == "external" else "Saved profile; service is offline")
        values["profile"].set(profile)
        values["edits"].set("Unsaved changes" if dirty else "No unsaved changes")
        fetch = status.get("fetch_activity", {})
        activity = fetch.get("activity", {})
        stage = activity.get("stage", "idle")
        if fetch.get("availability") == "available":
            text = "Fetch: " + (stage.replace("_", " ") if activity.get("running") else "idle" if stage == "idle" else "last run " + activity.get("status", "finished"))
        else:
            text = "Fetch telemetry: " + fetch.get("availability", "not checked")
        values["activity"].set(text)
        snapshot = status.get("log_snapshot", {})
        errors = [entry for entry in snapshot.get("entries", []) if entry["level"] == "ERROR"]
        values["failure"].set((local_time(errors[-1]["at"]) + "  " + errors[-1]["message"]) if errors else
                              "None in retained logs" if snapshot.get("availability") == "available" else "Logs unavailable")
        self.connection_scope.set("Saved profile diagnostics | launch profile: " + profile)
        proxy = self.app.proxy_result
        proxy_text = {"inherited": "Blank: environment/default, not verified", "reachable": "TCP reachable; upstream access not verified",
                      "unreachable": "TCP connection failed"}.get(proxy.get("state"), "Not checked" if config.proxy else "Environment/default; not verified")
        if proxy.get("checked_at"):
            proxy_text += " | " + local_time(proxy["checked_at"])
        entries = snapshot.get("entries", []) if managed and snapshot.get("run_id") == status.get("run_id") else []
        tunnel_events = [item for item in entries if item["code"] in {"hath.starting", "hath.retry", "hath.error", "ssh.auth-error", "ssh.connection-error"}]
        tunnel = "Unknown (no current-run evidence)"
        if tunnel_events:
            event = tunnel_events[-1]
            tunnel = {"hath.starting": "Start requested; connection unconfirmed", "hath.retry": "Reconnect scheduled",
                      "hath.error": "Integration error observed", "ssh.auth-error": "Authentication error observed",
                      "ssh.connection-error": "Connection error observed"}[event["code"]]
            tunnel += " | " + local_time(event["at"])
        observer = self.app.observer_result
        observer_text = "Saved database: not checked"
        if observer:
            observer_text = f"{observer['clients']} clients; last seen: " + local_time(observer.get("last_seen_at"))
        hath_files = "Disabled in saved profile"
        if config.hath_enabled:
            hath_files = "Enabled; credential paths checked only at service start"
        rows = (("Local API", ready), ("Proxy", proxy_text), ("H@H configuration", hath_files),
                ("SSH tunnel", tunnel), ("Observer", observer_text))
        from tkinter import font
        needed = max(font.nametofont("TkDefaultFont").measure(row[1]) for row in rows) + 20
        self.connection_table.column("state", minwidth=needed)
        for index, row in enumerate(rows):
            key = str(index)
            if self.connection_table.exists(key):
                self.connection_table.item(key, values=row)
            else:
                self.connection_table.insert("", "end", iid=key, values=row)
        self.storage_path.set(str(config.resolve(config.data_dir or "data")))
        enabled = "disabled" if self.app.busy else "normal"
        for button in (*self.maintenance_buttons, self.proxy_button, self.observer_button, self.environment_button):
            button.configure(state=enabled)

    def render_storage(self, result):
        self.storage_summary.set(f"Free: {size_text(result['free_bytes'])} / {size_text(result['total_bytes'])} | " + local_time(result["checked_at"]))
        self.storage_table.delete(*self.storage_table.get_children())
        for row in result["rows"]:
            self.storage_table.insert("", "end", values=(row["category"], size_text(row["bytes"]), row["files"],
                                                         "Not present" if row.get("missing") else "Partial" if row["partial"] else "Scanned"))
        backup = result.get("latest_backup")
        history_partial = result.get("backup_history_partial", False)
        self.backup_summary.set(("Observed backup verified at creation: " if history_partial else "Last backup verified at creation: ") + local_time(backup["at"])
                                if backup else "Backup history scan incomplete" if history_partial else "No verified launcher backup found")

    def render_environment(self, result):
        packages = result["packages"]
        dependency_state = "Required packages: " + (", ".join(result["missing_required"]) + " missing" if result.get("missing_required") else "present")
        self.environment_result.set(f"Selected Python {result['python']} | Accelerator: {result['accelerator']}\n" + dependency_state + " | " + local_time(result["checked_at"]) +
                                    "\n" + " | ".join(f"{name}: {version}" for name, version in packages.items()))

    def clear_checks(self):
        self.environment_result.set("Selected Python: not checked. Runtime accelerator: unknown.")
        self.storage_summary.set("Disk usage: not scanned")
        self.storage_table.delete(*self.storage_table.get_children())
        self.backup_summary.set("Last verified launcher backup: not checked")
        self.database_result.set("Database check: not run")
        self.maintenance_result.set("")
