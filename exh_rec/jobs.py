"""Durable, single-worker jobs. No credentials or exception text enter history."""
from datetime import datetime, timezone
import json
import re
import sqlite3
import threading
import time
import uuid


KINDS = {"fetch": "Fetch", "enrich": "Enrich", "review_prewarm": "Review Prewarm", "retrain": "Retrain", "backup": "Backup"}
ACTIVE = frozenset({"queued", "held", "running", "pause_requested", "paused", "cancel_requested"})
TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"})
STATES = ACTIVE | TERMINAL
STEP_STATES = frozenset({"pending", "running", "succeeded", "partial", "failed", "cancelled", "interrupted"})
CLAIM_BUSY_RETRIES = 6
STAGES = frozenset({"queued", "starting", "query_page", "query_metadata", "detail", "finalizing",
                    "retraining", "backup", "media", "visual", "step_complete", "finished", "interrupted", "cancelled"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    state TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    retry_of TEXT,
    error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, priority, sequence);
CREATE TABLE IF NOT EXISTS job_workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class JobError(ValueError):
    pass


class JobHalt(BaseException):
    """Bypass request-error handlers, but run application finally blocks."""
    def __init__(self, state):
        self.state = state


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise JobError("Invalid job identifier")
    return value


def name_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or not all(c.isprintable() for c in value):
        raise JobError("Name must contain 1-80 printable characters")
    return value.strip()


def validate_steps(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise JobError("Choose 1-8 workflow steps")
    steps = []
    for value in values:
        if (not isinstance(value, dict) or set(value) - {"kind", "params"}
                or not isinstance(value.get("kind"), str) or value["kind"] not in KINDS):
            raise JobError("Unsupported job kind")
        kind, params = value["kind"], value.get("params", {})
        allowed = {"fetch": {"query", "include_rated", "filter_text"},
                   "enrich": {"limit", "include_rated", "filter_text"}, "review_prewarm": set(),
                   "retrain": set(), "backup": set()}[kind]
        if not isinstance(params, dict) or set(params) - allowed:
            raise JobError("Unsupported job parameters")
        clean = {}
        for key, item in params.items():
            if key == "include_rated":
                if type(item) is not bool:
                    raise JobError("Invalid review filter")
            elif key == "limit":
                if type(item) is not int or not 1 <= item <= 50:
                    raise JobError("Detail limit must be 1-50")
            elif item is not None and (not isinstance(item, str) or len(item) > 512 or not all(c.isprintable() for c in item)):
                raise JobError("Invalid query or filter")
            clean[key] = item
        steps.append({"kind": kind, "params": clean, "state": "pending", "result": {}})
    return steps


def public_job(row):
    value = {key: row[key] for key in ("id", "name", "source", "state", "stage", "created_at", "started_at",
                                      "finished_at", "updated_at", "retry_of", "error_code")}
    value["steps"] = json.loads(row["steps_json"])
    return value


def result_summary(result):
    if not isinstance(result, dict):
        return {}
    summary = {key: result[key] for key in ("fetched", "stored", "enriched", "parent_enriched", "bytes",
                                          "checked", "ready", "partial", "images", "analyzed")
               if type(result.get(key)) is int and 0 <= result[key] <= 10**12}
    if isinstance(result.get("errors"), list):
        summary["errors"] = len(result["errors"])
    if type(result.get("verified")) is bool:
        summary["verified"] = result["verified"]
    return summary


class _ClaimBusy(Exception):
    pass


class JobContext:
    def __init__(self, queue, job_id):
        self.queue, self.job_id = queue, job_id
        self.step_index = 0

    def has_later(self, kind):
        return any(step["kind"] == kind for step in self.queue.get(self.job_id)["steps"][self.step_index + 1:])

    def checkpoint(self, stage="starting"):
        if stage not in STAGES:
            raise JobError("Invalid checkpoint")
        while True:
            if self.queue.stop.is_set():
                raise JobHalt("interrupted")
            with self.queue.connect() as conn:
                row = conn.execute("SELECT state, stage FROM jobs WHERE id = ?", (self.job_id,)).fetchone()
                state = row["state"]
                if state == "cancel_requested":
                    raise JobHalt("cancelled")
                if state == "pause_requested":
                    conn.execute("UPDATE jobs SET state = 'paused', stage = ?, updated_at = ? WHERE id = ? AND state = 'pause_requested'",
                                 (stage, timestamp(), self.job_id))
                    state = "paused"
                elif row["stage"] != stage:
                    conn.execute("UPDATE jobs SET stage = ?, updated_at = ? WHERE id = ?", (stage, timestamp(), self.job_id))
            if state != "paused":
                return
            self.queue.wake.wait(0.25)
            self.queue.wake.clear()


class JobQueue:
    def __init__(self, connect):
        self.connect = connect
        self.stop, self.wake = threading.Event(), threading.Event()
        self.worker = None
        self.executor = None
        self.waiters = {}
        self.waiters_lock = threading.Lock()

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive() and not self.stop.is_set()

    def initialize(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def get(self, job_id):
        identifier(job_id)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobError("Job not found")
        return public_job(row)

    def snapshot(self, limit=100):
        with self.connect() as conn:
            # Active work is never hidden behind a long completed history.
            active = conn.execute("SELECT * FROM jobs WHERE state NOT IN ('succeeded','partial','failed','cancelled','interrupted') ORDER BY priority DESC, sequence LIMIT 100").fetchall()
            history = conn.execute("SELECT * FROM jobs WHERE state IN ('succeeded','partial','failed','cancelled','interrupted') ORDER BY sequence DESC LIMIT ?",
                                   (max(1, min(100, limit)),)).fetchall()
            counts = {row["state"]: row["total"] for row in conn.execute("SELECT state, COUNT(*) AS total FROM jobs GROUP BY state")}
            workflows = [{"id": row["id"], "name": row["name"], "steps": json.loads(row["steps_json"])}
                         for row in conn.execute("SELECT * FROM job_workflows ORDER BY name COLLATE NOCASE LIMIT 50")]
        return {"service": "exh-rec", "schema": 1, "worker": self.running,
                "jobs": [public_job(row) for row in (*active, *history)], "counts": counts, "workflows": workflows}

    def submit(self, steps, *, name=None, source="launcher", next_job=False, coalesce=False):
        steps = validate_steps(steps)
        if not isinstance(source, str) or source not in {"launcher", "web", "background", "model"} or type(next_job) is not bool:
            raise JobError("Invalid job source or order")
        title = name_text(name) if name is not None else " + ".join(KINDS[step["kind"]] for step in steps)
        encoded = json.dumps(steps, ensure_ascii=True)
        job_id, now = uuid.uuid4().hex, timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if coalesce:
                for row in conn.execute("SELECT * FROM jobs WHERE source = ? AND state IN ('queued','held','running','paused','pause_requested','cancel_requested')", (source,)):
                    existing = public_job(row)
                    if [{"kind": step["kind"], "params": step["params"]} for step in existing["steps"]] == [
                            {"kind": step["kind"], "params": step["params"]} for step in steps]:
                        return existing
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('succeeded','partial','failed','cancelled','interrupted')").fetchone()[0]
            if pending >= 100:
                raise JobError("Queue is full")
            priority = -1 if source == "background" and all(step["kind"] == "review_prewarm" for step in steps) else 0
            conn.execute("INSERT INTO jobs(id,name,source,state,steps_json,priority,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?)",
                         (job_id, title, source, encoded, time.time_ns() if next_job else priority, now, now))
        self.wake.set()
        return self.get(job_id)

    def save_workflow(self, name, steps, workflow_id=None):
        title, steps = name_text(name), validate_steps(steps)
        workflow_id = identifier(workflow_id) if workflow_id is not None else uuid.uuid4().hex
        definition = [{"kind": step["kind"], "params": step["params"]} for step in steps]
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT 1 FROM job_workflows WHERE id = ?", (workflow_id,)).fetchone()
            if conn.execute("SELECT 1 FROM job_workflows WHERE name = ? COLLATE NOCASE AND id != ?", (title, workflow_id)).fetchone():
                raise JobError("Workflow name already exists")
            if not exists and conn.execute("SELECT COUNT(*) FROM job_workflows").fetchone()[0] >= 50:
                raise JobError("Workflow limit reached")
            conn.execute("INSERT INTO job_workflows(id,name,steps_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, steps_json=excluded.steps_json, updated_at=excluded.updated_at",
                         (workflow_id, title, json.dumps(definition, ensure_ascii=True), timestamp()))
        return {"id": workflow_id, "name": title, "steps": definition}

    def action(self, job_id, action):
        identifier(job_id)
        now = timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobError("Job not found")
            state = row["state"]
            target, new_id = None, job_id
            if action == "pause" and state in {"queued", "running"}:
                target = "held" if state == "queued" else "pause_requested"
            elif action == "resume" and state in {"held", "paused", "pause_requested"}:
                target = "queued" if state == "held" else "running"
            elif action == "cancel" and state in ACTIVE - {"cancel_requested"}:
                target = "cancelled" if state in {"queued", "held"} else "cancel_requested"
            elif action == "next" and state in {"queued", "held"}:
                conn.execute("UPDATE jobs SET priority = ?, updated_at = ? WHERE id = ?", (time.time_ns(), now, job_id))
                target = "queued"
            elif action == "retry" and state in TERMINAL - {"succeeded"}:
                if conn.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('succeeded','partial','failed','cancelled','interrupted')").fetchone()[0] >= 100:
                    raise JobError("Queue is full")
                steps = json.loads(row["steps_json"])
                for step in steps:
                    if step["state"] != "succeeded":
                        step.update(state="pending", result={})
                new_id = uuid.uuid4().hex
                conn.execute("INSERT INTO jobs(id,name,source,state,steps_json,created_at,updated_at,retry_of) VALUES(?,?,?,'queued',?,?,?,?)",
                             (new_id, row["name"], "launcher", json.dumps(steps, ensure_ascii=True), now, now, job_id))
            else:
                raise JobError("Action is not available in the current state")
            if target:
                conn.execute("UPDATE jobs SET state = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                             (target, now, now if target == "cancelled" else None, job_id))
        self.wake.set()
        if target == "cancelled":
            with self.waiters_lock:
                waiter = self.waiters.get(job_id)
                if waiter:
                    waiter["error"] = JobError("Job was cancelled before execution")
                    waiter["event"].set()
        return self.get(new_id)

    def recover(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT * FROM jobs WHERE state IN ('running','pause_requested','paused','cancel_requested')").fetchall():
                steps = json.loads(row["steps_json"])
                for step in steps:
                    if step["state"] == "running":
                        step["state"] = "interrupted"
                now = timestamp()
                conn.execute("UPDATE jobs SET state='interrupted', stage='interrupted', steps_json=?, error_code='service_interrupted', finished_at=?, updated_at=? WHERE id=?",
                             (json.dumps(steps, ensure_ascii=True), now, now, row["id"]))

    def start(self, executor):
        if self.worker is not None and self.worker.is_alive():
            raise JobError("Jobs worker is already running")
        self.initialize()
        self.recover()
        self.executor = executor
        self.stop.clear()
        self.worker = threading.Thread(target=self._loop, name="jobs", daemon=True)
        self.worker.start()

    def shutdown(self):
        self.stop.set()
        self.wake.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=3)

    def run_sync(self, steps, *, source="web"):
        if not self.running:
            raise JobError("Jobs worker is not running")
        waiter = {"event": threading.Event()}
        with self.waiters_lock:
            job = self.submit(steps, source=source)
            self.waiters[job["id"]] = waiter
        try:
            while not waiter["event"].wait(0.5):
                if self.stop.is_set():
                    raise JobError("Service interrupted; inspect Jobs before retrying")
            if "error" in waiter:
                raise waiter["error"]
            return waiter.get("result", {})
        finally:
            with self.waiters_lock:
                self.waiters.pop(job["id"], None)

    def _claim(self):
        with self.connect() as conn:
            conn.execute("PRAGMA busy_timeout = 250")
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                    raise _ClaimBusy from None
                raise
            if conn.execute("SELECT 1 FROM jobs WHERE state IN ('running','pause_requested','paused','cancel_requested') LIMIT 1").fetchone():
                return None
            row = conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY priority DESC, sequence LIMIT 1").fetchone()
            if row is None:
                return None
            now = timestamp()
            conn.execute("UPDATE jobs SET state='running', stage='starting', started_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
            return public_job(row)

    def _save_steps(self, job_id, steps):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET steps_json=?, updated_at=? WHERE id=?", (json.dumps(steps, ensure_ascii=True), timestamp(), job_id))

    def _finish(self, job_id, state, code=""):
        now = timestamp()
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET state=?, stage=?, error_code=?, finished_at=?, updated_at=? WHERE id=?",
                         (state, "finished" if state not in {"interrupted", "cancelled"} else state, code, now, now, job_id))

    def _execute(self, job):
        job_id, steps = job["id"], job["steps"]
        context = JobContext(self, job_id)
        result, error = {}, None
        index = None
        try:
            for index, step in enumerate(steps):
                context.step_index = index
                if step["state"] == "succeeded":
                    continue
                context.checkpoint("starting")
                step["state"] = "running"
                self._save_steps(job_id, steps)
                result = self.executor(step["kind"], dict(step["params"]), context, job["source"])
                status = result.get("status") if isinstance(result, dict) else None
                step["state"] = "failed" if status == "failed" else "partial" if status == "partial" else "succeeded"
                step["result"] = result_summary(result)
                self._save_steps(job_id, steps)
                if step["state"] != "succeeded":
                    self._finish(job_id, step["state"], "step_incomplete")
                    return
                # A late pause/cancel still takes effect before the next step.
                if index < len(steps) - 1:
                    context.checkpoint("step_complete")
            self._finish(job_id, "succeeded")
        except JobHalt as halt:
            if index is not None and steps[index]["state"] == "running":
                steps[index]["state"] = halt.state
                self._save_steps(job_id, steps)
            self._finish(job_id, halt.state, "service_interrupted" if halt.state == "interrupted" else "")
            error = JobError("Job stopped; committed work was retained")
        except Exception:
            if index is not None:
                steps[index]["state"] = "failed"
                self._save_steps(job_id, steps)
            self._finish(job_id, "failed", "step_failed")
            error = JobError("Job failed; inspect Jobs before retrying")
        finally:
            with self.waiters_lock:
                waiter = self.waiters.get(job_id)
                if waiter is not None:
                    waiter["error" if error else "result"] = error if error else result
                    waiter["event"].set()

    def _claim_with_retry(self):
        for attempt in range(CLAIM_BUSY_RETRIES + 1):
            if self.stop.is_set():
                return None
            try:
                return self._claim()
            except _ClaimBusy:
                if attempt == CLAIM_BUSY_RETRIES:
                    raise JobError("Jobs writer remained busy; inspect Jobs before retrying") from None
                if self.stop.wait(min(2.0, 0.25 * 2 ** attempt)):
                    return None

    def _loop(self):
        while not self.stop.is_set():
            try:
                job = self._claim_with_retry()
                if job is not None:
                    self._execute(job)
                    continue
            except Exception:
                # Fail closed if durable bookkeeping fails; do not run untracked work.
                self.stop.set()
                with self.waiters_lock:
                    for waiter in self.waiters.values():
                        waiter["error"] = JobError("Jobs worker unavailable; inspect Jobs before retrying")
                        waiter["event"].set()
                return
            self.wake.wait(0.5)
            self.wake.clear()
