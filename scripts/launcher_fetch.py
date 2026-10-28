"""Read-only fetch telemetry client and native activity view."""
from datetime import datetime
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

from exh_rec.fetch_activity import COUNTERS, MESSAGES, STAGES


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_activity(payload):
    if not isinstance(payload, dict) or payload.get("service") != "exh-rec" or payload.get("schema") != 1:
        raise ValueError()
    data = payload["activity"]
    if type(data["running"]) is not bool or data["stage"] not in STAGES | {"idle"}:
        raise ValueError()
    if data["stage"] == "idle":
        if data["running"]:
            raise ValueError()
        return {"stage": "idle", "running": False, "queries": [], "events": []}
    numeric = (*COUNTERS, "initial_pages", "extra_pages", "queries_done", "pages_done", "page_budget",
               "error_count", "parent_error_count", "incomplete_count", "elapsed_seconds", "quiet_seconds")
    for key in numeric:
        if type(data[key]) is not int or not 0 <= data[key] <= 10**12:
            raise ValueError()
    if (data["trigger"] not in {"manual", "background", "enrich", "other"}
            or data["status"] not in {"", "success", "partial", "failed"}
            or data["message"] not in MESSAGES.values()):
        raise ValueError()
    if type(data["model_retrained"]) is not bool:
        raise ValueError()
    for key in ("started_at", "updated_at"):
        if not isinstance(data[key], str) or len(data[key]) > 40:
            raise ValueError()
        datetime.fromisoformat(data[key])
    if (not isinstance(data["current_item"], str) or len(data["current_item"]) > 256
            or any(not char.isprintable() for char in data["current_item"])):
        raise ValueError()
    if not isinstance(data["queries"], list) or len(data["queries"]) > 100:
        raise ValueError()
    for row in data["queries"]:
        if (not isinstance(row["label"], str) or len(row["label"]) > 256
                or any(not char.isprintable() for char in row["label"])
                or row["source"] not in {"recent", "bootstrap", "learned", "manual", "other"}
                or row["state"] not in {"pending", "running", "done", "partial", "failed", "skipped"}
                or type(row["resuming"]) is not bool):
            raise ValueError()
        for key in ("index", "pages_done", "page_budget", "fetched", "stored"):
            if type(row[key]) is not int or not 0 <= row[key] <= 10**12:
                raise ValueError()
        for key in ("anchor_matches", "anchor_target"):
            if type(row.get(key, 0)) is not int or not 0 <= row.get(key, 0) <= 10**12:
                raise ValueError()
    if not isinstance(data["events"], list) or len(data["events"]) > 120:
        raise ValueError()
    for event in data["events"]:
        if (event["message"] not in MESSAGES.values() or type(event["warning"]) is not bool
                or not isinstance(event["at"], str) or len(event["at"]) > 40):
            raise ValueError()
        datetime.fromisoformat(event["at"])
        for key in ("query_index", "page", "detail_index"):
            if type(event[key]) is not int or not 0 <= event[key] <= 10**12:
                raise ValueError()
    return data


def read_fetch_activity(home_url, timeout=0.8):
    try:
        endpoint = urllib.parse.urlsplit(home_url)
        ipaddress.IPv4Address(endpoint.hostname)
        if (endpoint.scheme != "http" or endpoint.username is not None or endpoint.password is not None
                or endpoint.path != "/" or endpoint.query or endpoint.fragment or not endpoint.port):
            raise ValueError()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(home_url + "api/fetch-progress", timeout=timeout) as response:
            if not response.headers.get("Server", "").startswith("ExhRec/"):
                raise ValueError()
            raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError()
            activity = validate_activity(json.loads(raw))
        return {"availability": "available", "activity": activity}
    except urllib.error.HTTPError as error:
        unsupported = error.code == 404 and error.headers.get("Server", "").startswith("ExhRec/")
        error.close()
        return {"availability": "unsupported" if unsupported else "unavailable"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"availability": "unavailable"}


def duration(seconds):
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class FetchActivityView:
    def __init__(self, notebook, tk, ttk):
        self.frame = ttk.Frame(notebook, padding=12)
        notebook.add(self.frame, text="Fetch Activity")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(8, weight=2)
        self.frame.rowconfigure(10, weight=1)
        self.heading = ttk.Label(self.frame, text="Waiting for service", style="Status.TLabel", wraplength=680)
        self.heading.grid(row=0, column=0, sticky="ew")
        self.summary = ttk.Label(self.frame, text="", style="Muted.TLabel", wraplength=680)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(5, 8))
        metrics = ttk.Frame(self.frame)
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.metrics = {}
        self.phases = []
        for index, name in enumerate(("Fetched", "New", "Enriched", "Errors")):
            metrics.columnconfigure(index, weight=1)
            variable = tk.StringVar(value=f"{name}: --")
            self.metrics[name] = variable
            ttk.Label(metrics, textvariable=variable).grid(row=0, column=index, sticky="w")
        for index, name in enumerate(("Lists + covers", "Details + samples", "Model (optional)", "Finalize")):
            label = ttk.Label(metrics, text=name, style="Muted.TLabel")
            label.grid(row=1, column=index, sticky="w", pady=(5, 0))
            self.phases.append(label)
        query_row = ttk.Frame(self.frame)
        query_row.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        query_row.columnconfigure(0, weight=1)
        self.query_label = ttk.Label(query_row, text="Queries: --")
        self.query_label.grid(row=0, column=0, sticky="ew")
        self.query_bar = ttk.Progressbar(query_row, mode="determinate", maximum=1, length=150)
        self.query_bar.grid(row=0, column=1, sticky="e", padx=(10, 0))
        detail_row = ttk.Frame(self.frame)
        detail_row.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        detail_row.columnconfigure(0, weight=1)
        self.detail_label = ttk.Label(detail_row, text="Details: --", wraplength=520)
        self.detail_label.grid(row=0, column=0, sticky="ew")
        self.detail_bar = ttk.Progressbar(detail_row, mode="determinate", maximum=1, length=150)
        self.detail_bar.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.plan_label = ttk.Label(self.frame, text="Query Plan", style="Muted.TLabel", wraplength=680)
        self.plan_label.grid(row=7, column=0, sticky="ew", pady=(0, 4))
        self.plan = self.table(ttk, 8, ("query", "source", "state", "pages", "anchors", "fetched", "new"),
                               ("Query", "Source", "State", "Pages / Cap", "Anchors", "Fetched", "New"),
                               (210, 76, 78, 88, 64, 64, 54), height=5)
        ttk.Label(self.frame, text="Stage Messages", style="Muted.TLabel").grid(row=9, column=0, sticky="w", pady=(10, 4))
        self.events = self.table(ttk, 10, ("time", "message", "context"),
                                 ("Time", "Activity", "Query / Detail"), (86, 380, 104), height=4)
        self.events.tag_configure("warning", foreground="#b42332")
        self.plan.tag_configure("running", foreground="#096749")
        self.plan.tag_configure("partial", foreground="#8a5715")
        self.plan.tag_configure("failed", foreground="#b42332")
        self.event_rows = None
        self.frame.bind("<Configure>", self.resize)

    def resize(self, event):
        width = max(200, event.width - 30)
        for label in (self.heading, self.summary, self.plan_label):
            label.configure(wraplength=width)
        for label in (self.query_label, self.detail_label):
            label.configure(wraplength=max(160, width - 170))

    def table(self, ttk, row, columns, headings, widths, height):
        frame = ttk.Frame(self.frame)
        frame.grid(row=row, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode="browse")
        for name, label, width in zip(columns, headings, widths):
            tree.heading(name, text=label)
            tree.column(name, width=width, minwidth=40, stretch=name in {"query", "message"})
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        return tree

    def render(self, payload):
        availability = payload.get("availability", "unavailable")
        data = payload.get("activity", {})
        if availability != "available" or data.get("stage") == "idle":
            headings = {"offline": "Service is not running", "unsupported": "Service restart required for fetch telemetry",
                        "unavailable": "Fetch status unavailable", "available": "No fetch in this server session"}
            self.heading.configure(text=headings.get(availability, "Fetch status unavailable"))
            self.summary.configure(text="")
            for name, variable in self.metrics.items():
                variable.set(f"{name}: --")
            for label, text in zip(self.phases, ("Lists + covers", "Details + samples", "Model (optional)", "Finalize")):
                label.configure(foreground="#59636b", text=text)
            self.query_label.configure(text="Queries: --")
            self.query_bar.configure(value=0, maximum=1)
            self.detail_label.configure(text="Details: --")
            self.detail_bar.configure(value=0, maximum=1)
            self.plan_label.configure(text="Query Plan")
            self.plan.delete(*self.plan.get_children())
            self.events.delete(*self.events.get_children())
            self.event_rows = None
            return
        running = data["running"]
        state = "Running" if running else {"success": "Completed", "partial": "Partially completed", "failed": "Failed"}.get(data["status"], "Stopped")
        self.heading.configure(text=f"{state} | {data['message']}")
        started = datetime.fromisoformat(data["started_at"]).strftime("%m-%d %H:%M UTC")
        self.summary.configure(text=f"#{data['run_id']} {data['trigger']} | {started} | "
                               f"Elapsed {duration(data['elapsed_seconds'])} | Update {data['quiet_seconds']}s ago | ETA --"
                               + (f"\nCurrent: {data['current_item'][:77]}{'...' if len(data['current_item']) > 77 else ''}" if data["current_item"] else ""))
        for name, key in (("Fetched", "fetched"), ("New", "stored"), ("Enriched", "enriched")):
            self.metrics[name].set(f"{name}: {data[key]}")
        self.metrics["Errors"].set(f"Errors: {data['error_count'] + data['parent_error_count']}")
        phase = {"selecting_details": 1, "enriching_details": 1, "retraining": 2,
                 "finalizing": 3, "finished": 3}.get(data["stage"], 0)
        for index, label in enumerate(self.phases):
            label.configure(foreground="#096749" if index == phase and data["status"] != "failed" else "#59636b")
        self.phases[0].configure(text="Lists (skipped)" if data["trigger"] == "enrich" else "Lists + covers")
        self.phases[2].configure(text="Model (done)" if data.get("model_retrained") else "Model (skipped)" if not running else "Model (optional)")
        self.query_label.configure(text=f"Queries: {data['queries_done']}/{data['query_total']} | "
                                   f"List pages: {data['pages_done']} / Cap {data['page_budget']}")
        self.query_bar.configure(maximum=max(1, data["query_total"]), value=data["queries_done"])
        details = f"{data['detail_done']}/{data['detail_total']} processed" if "detail_total" in data and data["stage"] not in {"starting", "running", "fetching_pages", "cover_metadata", "storing", "error", "selecting_details"} else "selection pending"
        self.detail_label.configure(text=f"Details: {details} | Limit {data['detail_limit']} | "
                                    f"Samples +{data['sample_extra_pages']} | Parents {data['parent_enriched']}")
        self.detail_bar.configure(maximum=max(1, data["detail_total"]), value=data["detail_done"])
        self.plan_label.configure(text=f"Plan: {data['initial_pages']} initial + {data['extra_pages']} catch-up pages/query | "
                                  f"Incomplete: {data['incomplete_count']}")
        wanted = {str(row["index"]) for row in data["queries"]}
        for item in self.plan.get_children():
            if item not in wanted:
                self.plan.delete(item)
        for row in data["queries"]:
            key = str(row["index"])
            values = (row["label"] + (" [resume]" if row["resuming"] else ""), row["source"], row["state"],
                      f"{row['pages_done']}/{row['page_budget']}",
                      f"{row.get('anchor_matches', 0)}/{row['anchor_target']}" if row.get("anchor_target") else "--",
                      row["fetched"], row["stored"])
            if self.plan.exists(key):
                self.plan.item(key, values=values, tags=(row["state"],))
            else:
                self.plan.insert("", "end", iid=key, values=values, tags=(row["state"],))
        if data["events"] != self.event_rows:
            view = self.events.yview()
            self.events.delete(*self.events.get_children())
            for event in data["events"]:
                at = datetime.fromisoformat(event["at"]).astimezone().strftime("%H:%M:%S")
                context = f"Q{event['query_index']}" if event["query_index"] else ""
                if event["detail_index"]:
                    context = f"D{event['detail_index']}"
                elif event["page"]:
                    context += f" / P{event['page']}"
                self.events.insert("", "end", values=(at, event["message"], context), tags=("warning",) if event["warning"] else ())
            self.events.yview_moveto(1 if view[1] >= .99 else view[0])
            self.event_rows = list(data["events"])
