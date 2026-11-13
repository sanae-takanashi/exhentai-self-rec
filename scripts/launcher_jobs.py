"""Bounded Jobs client and native queue/workflow views."""
from datetime import datetime, timezone
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

from exh_rec.jobs import KINDS, STATES, STEP_STATES, STAGES, TERMINAL, identifier, validate_steps
from scripts.launcher_core import LauncherError, NoRedirect
from scripts.launcher_ops import local_time


MAX_BYTES = 16 * 1024 * 1024


def _text(value, size=512):
    if not isinstance(value, str) or len(value) > size or any(not c.isprintable() for c in value):
        raise ValueError()
    return value


def validate_job(value):
    if not isinstance(value, dict):
        raise ValueError()
    result = {"id": identifier(value["id"]), "name": _text(value["name"], 80), "state": value["state"],
              "source": value["source"], "stage": value["stage"], "error_code": value["error_code"]}
    if (result["state"] not in STATES or result["source"] not in {"launcher", "web", "background", "model"}
            or result["stage"] not in STAGES or result["error_code"] not in {"", "step_failed", "step_incomplete", "service_interrupted"}):
        raise ValueError()
    for key in ("created_at", "updated_at", "started_at", "finished_at"):
        item = value[key]
        if item is not None:
            _text(item, 40)
            if not datetime.fromisoformat(item).tzinfo:
                raise ValueError()
        elif key in {"created_at", "updated_at"}:
            raise ValueError()
        result[key] = item
    result["retry_of"] = identifier(value["retry_of"]) if value.get("retry_of") is not None else None
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8 or any(not isinstance(step, dict) for step in steps):
        raise ValueError()
    validated = validate_steps([{"kind": step["kind"], "params": step["params"]} for step in steps])
    for clean, step in zip(validated, steps):
        if step["state"] not in STEP_STATES or not isinstance(step["result"], dict):
            raise ValueError()
        clean["state"] = step["state"]
        clean["result"] = {key: item for key, item in step["result"].items()
                           if key in {"fetched", "stored", "enriched", "parent_enriched", "bytes", "errors",
                                      "checked", "ready", "partial", "images", "analyzed"}
                           and type(item) is int and 0 <= item <= 10**12}
        if type(step["result"].get("verified")) is bool:
            clean["result"]["verified"] = step["result"]["verified"]
    result["steps"] = validated
    return result


def validate_snapshot(payload):
    if (not isinstance(payload, dict) or payload.get("service") != "exh-rec" or type(payload.get("schema")) is not int
            or payload["schema"] != 1 or type(payload.get("worker")) is not bool):
        raise ValueError()
    if not isinstance(payload.get("jobs"), list) or len(payload["jobs"]) > 200:
        raise ValueError()
    jobs = [validate_job(item) for item in payload["jobs"]]
    if len({job["id"] for job in jobs}) != len(jobs):
        raise ValueError()
    counts = payload["counts"]
    if not isinstance(counts, dict) or any(key not in STATES or type(item) is not int or not 0 <= item <= 10**12 for key, item in counts.items()):
        raise ValueError()
    workflows = payload["workflows"]
    if not isinstance(workflows, list) or len(workflows) > 50:
        raise ValueError()
    workflows = [{"id": identifier(item["id"]), "name": _text(item["name"], 80),
                  "steps": [{"kind": step["kind"], "params": step["params"]} for step in validate_steps(item["steps"])]}
                 for item in workflows]
    result = {"worker": payload["worker"], "jobs": jobs, "counts": dict(counts), "workflows": workflows}
    health = payload.get("maintenance")
    if isinstance(health, dict) and health.get("state") in {"starting", "ok", "warning", "stale"}:
        allowed = {"worker_unavailable", "feedback_attention", "long_running_job", "backup_attention",
                   "backup_old", "prewarm_attention", "disk_low", "read_probe_unavailable"}
        warnings = health.get("warnings", [])
        if isinstance(warnings, list) and all(isinstance(item, str) and item in allowed for item in warnings):
            result["maintenance"] = {"state": health["state"], "warnings": warnings}
    return result


def _request(home_url, route, payload=None, *, timeout=0.8):
    endpoint = urllib.parse.urlsplit(home_url)
    ipaddress.IPv4Address(endpoint.hostname)
    if (endpoint.scheme != "http" or endpoint.username is not None or endpoint.password is not None
            or endpoint.path != "/" or endpoint.query or endpoint.fragment or not endpoint.port):
        raise ValueError()
    if route not in {"api/jobs", "api/jobs/action", "api/jobs/workflows"}:
        raise ValueError()
    data = json.dumps(payload, ensure_ascii=True).encode("ascii") if payload is not None else None
    request = urllib.request.Request(home_url + route, data=data, headers={"Content-Type": "application/json"} if data else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        if not response.headers.get("Server", "").startswith("ExhRec/"):
            raise ValueError()
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        return json.loads(raw)


def read_jobs(home_url):
    try:
        return {"availability": "available", **validate_snapshot(_request(home_url, "api/jobs"))}
    except urllib.error.HTTPError as error:
        unsupported = error.code == 404 and error.headers.get("Server", "").startswith("ExhRec/")
        error.close()
        return {"availability": "unsupported" if unsupported else "unavailable"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"availability": "unavailable"}


def write_jobs(home_url, route, payload):
    # Verify capability before a mutation, rather than sending to an unknown listener.
    try:
        observed = read_jobs(home_url)
        if observed.get("availability") != "available" or not observed.get("worker"):
            raise LauncherError("jobs-unavailable")
        result = _request(home_url, route, payload, timeout=8)
        if result.get("service") != "exh-rec" or type(result.get("schema")) is not int or result["schema"] != 1:
            raise ValueError()
        if route == "api/jobs/workflows":
            item = result["item"]
            return {"id": identifier(item["id"]), "name": _text(item["name"], 80), "steps": validate_steps(item["steps"])}
        return validate_job(result["item"])
    except urllib.error.HTTPError as error:
        rejected = error.code in {400, 409}
        error.close()
        raise LauncherError("jobs-rejected" if rejected else "jobs-outcome-unknown") from None
    except LauncherError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise LauncherError("jobs-outcome-unknown") from None


PRESETS = {name: [{"kind": kind} for kind in kinds] for name, kinds in (
    ("Fetch", ("fetch",)), ("Enrich", ("enrich",)), ("Review Prewarm", ("review_prewarm",)),
    ("Retrain", ("retrain",)), ("Backup", ("backup",)),
    ("Backup + Fetch + Retrain", ("backup", "fetch", "retrain")),
    ("Backup + Enrich + Retrain", ("backup", "enrich", "retrain")))}


def _duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def job_timing(job, *, now=None):
    """Format persisted execution timestamps, not checkpoint update activity."""
    now = now if now is not None else datetime.now(timezone.utc)
    started, finished = job["started_at"], job["finished_at"]
    if finished:
        event, value = "Finished", finished
    elif started:
        event, value = "Started", started
    else:
        event = "Queued" if job["state"] == "queued" else "Created"
        value = job["created_at"]
    seconds = max(0, int((now - datetime.fromisoformat(value)).total_seconds()))
    age = "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m"), (1, "s")):
        if seconds >= size:
            age = f"{seconds // size}{unit} ago"
            break
    if not started:
        elapsed = "Not started"
    elif finished or job["state"] not in TERMINAL:
        end = datetime.fromisoformat(finished) if finished else now
        elapsed = _duration((end - datetime.fromisoformat(started)).total_seconds())
    else:
        elapsed = "Unknown"
    detail = ("Created " + local_time(job["created_at"]) + " | Updated " + local_time(job["updated_at"])
              + "\nStarted " + (local_time(started) if started else "Not started")
              + " | Finished " + (local_time(finished) if finished else "-")
              + "\nElapsed (including pauses): " + elapsed)
    return f"{event} {age}", elapsed, detail


class JobsView:
    def __init__(self, app):
        self.app = app
        tk, ttk = app.tk, app.ttk
        self.frame = ttk.Frame(app.notebook, padding=12)
        app.notebook.add(self.frame, text="Jobs")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(5, weight=2, minsize=100)
        self.frame.rowconfigure(8, weight=1, minsize=75)
        self.snapshot = {}
        self.jobs = {}
        self.workflow_defs = dict(PRESETS)
        self.workflow_ids = {}
        self.heading = tk.StringVar(value="Jobs: waiting for service")
        ttk.Label(self.frame, textvariable=self.heading, style="Status.TLabel", wraplength=680).grid(row=0, sticky="ew")
        composer = ttk.Frame(self.frame)
        composer.grid(row=1, sticky="ew", pady=(8, 6))
        composer.columnconfigure(0, weight=1)
        self.preset = tk.StringVar(value="Fetch")
        self.presets = ttk.Combobox(composer, textvariable=self.preset, values=list(PRESETS), state="readonly")
        self.presets.grid(row=0, column=0, sticky="ew")
        self.presets.bind("<<ComboboxSelected>>", lambda _: self.load_preset())
        self.queue_button = ttk.Button(composer, text="Queue", command=lambda: app.queue_job())
        self.queue_button.grid(row=0, column=1, padx=(8, 0))
        self.next_button = ttk.Button(composer, text="Run Next", command=lambda: app.queue_job(next_job=True))
        self.next_button.grid(row=0, column=2, padx=(8, 0))
        options = ttk.Frame(self.frame)
        options.grid(row=2, sticky="ew", pady=(0, 5))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Query").grid(row=0, column=0, padx=(0, 8))
        self.query = tk.StringVar(value="")
        self.query_entry = ttk.Entry(options, textvariable=self.query)
        self.query_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(options, text="Details").grid(row=0, column=2, padx=8)
        self.limit = tk.StringVar(value="20")
        self.limit_input = ttk.Spinbox(options, from_=1, to=50, textvariable=self.limit, width=5)
        self.limit_input.grid(row=0, column=3)
        extra = ttk.Frame(self.frame)
        extra.grid(row=3, sticky="ew", pady=(0, 6))
        extra.columnconfigure(2, weight=1)
        self.include_rated = tk.BooleanVar(value=False)
        self.rated_check = ttk.Checkbutton(extra, text="Include rated", variable=self.include_rated)
        self.rated_check.grid(row=0, column=0, padx=(0, 12))
        ttk.Label(extra, text="Filter").grid(row=0, column=1, padx=(0, 8))
        self.filter_text = tk.StringVar(value="")
        self.filter_entry = ttk.Entry(extra, textvariable=self.filter_text)
        self.filter_entry.grid(row=0, column=2, sticky="ew")
        self.save_workflow = ttk.Button(extra, text="Save Workflow", command=app.save_job_workflow)
        self.save_workflow.grid(row=0, column=3, padx=(8, 0))
        bar = ttk.Frame(self.frame)
        bar.grid(row=4, sticky="ew", pady=(0, 6))
        self.mode = tk.StringVar(value="All")
        for index, text in enumerate(("All", "Active", "History")):
            ttk.Radiobutton(bar, text=text, value=text, variable=self.mode, command=self.render_table).grid(row=0, column=index, padx=(0, 12))
        table_frame = ttk.Frame(self.frame)
        table_frame.grid(row=5, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("name", "state", "when", "elapsed", "step", "source"), show="headings", height=5)
        for key, label, width in (("name", "Job", 170), ("state", "State", 115), ("when", "Execution", 155),
                                  ("elapsed", "Elapsed", 90), ("step", "Checkpoint", 115), ("source", "Source", 75)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=65, stretch=key == "name")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.table.tag_configure("failed", foreground="#b42332")
        self.table.tag_configure("partial", foreground="#8a5715")
        self.table.tag_configure("interrupted", foreground="#8a5715")
        self.table.bind("<<TreeviewSelect>>", lambda _: self.select())
        actions = ttk.Frame(self.frame)
        actions.grid(row=6, sticky="ew", pady=(7, 5))
        self.actions = {}
        for index, (key, label) in enumerate((("pause", "Pause"), ("resume", "Resume"), ("cancel", "Cancel"), ("next", "Run Next"), ("retry", "Retry"))):
            button = ttk.Button(actions, text=label, command=lambda key=key: app.job_action(key))
            button.grid(row=0, column=index, padx=(0, 6))
            self.actions[key] = button
        self.detail = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self.detail, wraplength=680, justify="left").grid(row=7, sticky="ew", pady=(0, 5))
        step_frame = ttk.Frame(self.frame)
        step_frame.grid(row=8, sticky="nsew")
        step_frame.columnconfigure(0, weight=1)
        step_frame.rowconfigure(0, weight=1)
        self.steps = ttk.Treeview(step_frame, columns=("step", "state", "result"), show="headings", height=3)
        for key, label, width in (("step", "Step", 130), ("state", "State", 115), ("result", "Result", 380)):
            self.steps.heading(key, text=label)
            self.steps.column(key, width=width, minwidth=60)
        self.steps.grid(row=0, column=0, sticky="nsew")
        step_scroll = ttk.Scrollbar(step_frame, command=self.steps.yview)
        step_scroll.grid(row=0, column=1, sticky="ns")
        self.steps.configure(yscrollcommand=step_scroll.set)
        self.status = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self.status, style="Muted.TLabel", wraplength=680).grid(row=9, sticky="ew", pady=(5, 0))
        self.health = tk.StringVar(value="Health: waiting")
        ttk.Label(self.frame, textvariable=self.health, style="Muted.TLabel", wraplength=680).grid(row=10, sticky="ew", pady=(5, 0))
        self.select()

    def load_preset(self):
        steps = self.workflow_defs.get(self.preset.get(), [])
        fetch = next((step for step in steps if step["kind"] == "fetch"), {})
        enrich = next((step for step in steps if step["kind"] == "enrich"), {})
        params = (fetch or enrich).get("params", {})
        self.query.set(fetch.get("params", {}).get("query") or "")
        self.limit.set(str(enrich.get("params", {}).get("limit", 20)))
        self.include_rated.set(params.get("include_rated", False))
        self.filter_text.set(params.get("filter_text") or "")
        self.render_controls()

    def definition(self):
        values = self.workflow_defs.get(self.preset.get())
        if values is None:
            raise LauncherError("jobs-invalid")
        try:
            steps = []
            for step in values:
                kind, params = step["kind"], dict(step.get("params", {}))
                if kind in {"fetch", "enrich"}:
                    params.update(include_rated=self.include_rated.get(), filter_text=self.filter_text.get().strip() or None)
                if kind == "fetch":
                    params["query"] = self.query.get().strip() or None
                elif kind == "enrich":
                    params["limit"] = int(self.limit.get())
                steps.append({"kind": kind, "params": params})
            return [{"kind": step["kind"], "params": step["params"]} for step in validate_steps(steps)]
        except (TypeError, ValueError):
            raise LauncherError("jobs-invalid") from None

    def selected(self):
        selection = self.table.selection()
        return self.jobs.get(selection[0]) if selection else None

    def render(self, snapshot):
        self.snapshot = snapshot
        health = snapshot.get("maintenance", {})
        self.health.set("Health: " + health.get("state", "unavailable") +
                        (" | " + ", ".join(health["warnings"]) if health.get("warnings") else ""))
        availability = snapshot.get("availability")
        if availability != "available":
            self.heading.set({"offline": "Jobs: service offline", "unsupported": "Jobs: backend restart required"}.get(availability, "Jobs: unavailable"))
            self.jobs = {}
        else:
            self.jobs = {item["id"]: item for item in snapshot["jobs"]}
            counts = snapshot["counts"]
            active = sum(value for key, value in counts.items() if key not in TERMINAL)
            self.heading.set(f"Jobs | {active} active | {sum(counts.values())} total" if snapshot["worker"] else "Jobs: worker unavailable")
            self.workflow_defs = dict(PRESETS)
            self.workflow_ids = {}
            for workflow in snapshot["workflows"]:
                label = "Saved: " + workflow["name"]
                self.workflow_defs[label] = workflow["steps"]
                self.workflow_ids[label] = workflow["id"]
            self.presets.configure(values=list(self.workflow_defs))
        self.render_table()

    def render_table(self):
        now = datetime.now(timezone.utc)
        previous = self.table.selection()
        visible = [job for job in self.jobs.values() if self.mode.get() == "All"
                   or (job["state"] in TERMINAL) == (self.mode.get() == "History")]
        wanted = {job["id"] for job in visible}
        for child in self.table.get_children():
            if child not in wanted:
                self.table.delete(child)
        for index, job in enumerate(visible):
            when, elapsed, _ = job_timing(job, now=now)
            values = (job["name"], job["state"].replace("_", " ").title(), when, elapsed,
                      job["stage"].replace("_", " ").title(), job["source"].title())
            if self.table.exists(job["id"]):
                self.table.item(job["id"], values=values, tags=(job["state"],))
                self.table.move(job["id"], "", index)
            else:
                self.table.insert("", index, iid=job["id"], values=values, tags=(job["state"],))
        if previous and previous[0] in wanted:
            self.table.selection_set(previous[0])
        elif visible:
            self.table.selection_set(visible[0]["id"])
        self.select()

    def select(self):
        job = self.selected()
        self.steps.delete(*self.steps.get_children())
        if job:
            for index, step in enumerate(job["steps"], start=1):
                result = ", ".join(f"{key}: {value}" for key, value in step["result"].items())
                state = "Paused" if job["state"] == "paused" and step["state"] == "running" else step["state"].title()
                self.steps.insert("", "end", values=(f"{index}. {KINDS[step['kind']]}", state, result))
            self.detail.set(job_timing(job)[2])
            self.status.set({"pause_requested": "Pause pending at the next committed checkpoint.",
                             "paused": "Paused at a committed checkpoint; queue execution is waiting.",
                             "cancel_requested": "Cancel pending at the next committed checkpoint.",
                             "interrupted": "Service interrupted. Retry keeps completed workflow steps.",
                             "failed": "Step failed. Check saved settings and Activity before retrying.",
                             "partial": "Step incomplete. Retry keeps completed workflow steps."}.get(job["state"], ""))
        else:
            self.detail.set("")
            self.status.set("")
        self.render_controls()

    def render_controls(self):
        enabled = not self.app.busy and self.snapshot.get("availability") == "available" and self.snapshot.get("worker") is True
        for button in (self.queue_button, self.next_button, self.save_workflow):
            button.configure(state="normal" if enabled else "disabled")
        definitions = self.workflow_defs.get(self.preset.get(), [])
        kinds = {step["kind"] for step in definitions}
        self.query_entry.configure(state="normal" if "fetch" in kinds else "disabled")
        self.limit_input.configure(state="normal" if "enrich" in kinds else "disabled")
        for widget in (self.filter_entry, self.rated_check):
            widget.configure(state="normal" if kinds & {"fetch", "enrich"} else "disabled")
        selected = self.selected()
        state = selected["state"] if selected else None
        allowed = {"pause": {"queued", "running"}, "resume": {"held", "paused", "pause_requested"},
                   "cancel": {"queued", "held", "running", "paused", "pause_requested"},
                   "next": {"queued", "held"}, "retry": TERMINAL - {"succeeded"}}
        for key, button in self.actions.items():
            button.configure(state="normal" if enabled and state in allowed[key] else "disabled")
