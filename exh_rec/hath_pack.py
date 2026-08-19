from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any


PACK_LOG_LIMIT = 200
PACK_LINE_LIMIT = 2000
DEFAULT_REMOTE_HELPER = "/srv/hath-observer/hath_observer/archive.py"
REMOTE_OPERATIONS = frozenset({"inspect", "plan", "run"})


class HathPackError(RuntimeError):
    pass


class HathPackConflict(HathPackError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def pack_configuration(environment: dict[str, str] | None = None) -> dict[str, str]:
    values = os.environ if environment is None else environment
    return {
        "ssh_executable": values.get("EXH_REC_HATH_SSH_EXECUTABLE", "").strip(),
        "ssh_config": values.get("EXH_REC_HATH_SSH_CONFIG", "").strip(),
        "ssh_host": values.get("EXH_REC_HATH_SSH_HOST", "").strip(),
        "remote_helper": values.get("EXH_REC_HATH_ARCHIVE_HELPER", DEFAULT_REMOTE_HELPER).strip(),
    }


def build_pack_ssh_command(configuration: dict[str, str], operation: str) -> list[str]:
    if operation not in REMOTE_OPERATIONS:
        raise ValueError(f"Unsupported archive operation: {operation}")
    remote_command = " ".join(
        [
            "/usr/bin/python3",
            shlex.quote(configuration["remote_helper"]),
            operation,
        ]
    )
    return [
        configuration["ssh_executable"],
        "-F",
        configuration["ssh_config"],
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ConnectTimeout=15",
        configuration["ssh_host"],
        remote_command,
    ]


def configured_or_raise() -> dict[str, str]:
    configuration = pack_configuration()
    missing = [name for name, value in configuration.items() if not value]
    if missing:
        raise HathPackError(f"Missing H@H archive configuration: {', '.join(missing)}")
    return configuration


def run_remote_json(operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    configuration = configured_or_raise()
    try:
        completed = subprocess.run(
            build_pack_ssh_command(configuration, operation),
            input=json.dumps(payload, ensure_ascii=True) if payload is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HathPackError(f"Could not query the remote archive helper: {exc}") from exc
    output = (completed.stdout or "").strip()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        message = output[-1000:] or f"SSH exited with code {completed.returncode}"
        raise HathPackError(f"Remote archive helper returned invalid JSON: {message}") from exc
    if not isinstance(result, dict):
        raise HathPackError("Remote archive helper returned an invalid response")
    if completed.returncode != 0 or result.get("error"):
        raise HathPackError(str(result.get("error") or f"SSH exited with code {completed.returncode}"))
    return result


class HathPackRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previews: dict[str, dict[str, Any]] = {}
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "run_id": 0,
            "state": "idle",
            "stage": None,
            "message": None,
            "completed": None,
            "total": None,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "result": None,
            "logs": [],
        }

    def status(self) -> dict[str, Any]:
        configuration = pack_configuration()
        missing = [name for name, value in configuration.items() if not value]
        with self._lock:
            state = {**self._state, "logs": list(self._state["logs"])}
        state.update(
            {
                "configured": not missing,
                "configuration_error": (
                    f"Missing H@H archive configuration: {', '.join(missing)}" if missing else None
                ),
                "remote_host": configuration["ssh_host"] or None,
                "remote_helper": configuration["remote_helper"] or None,
            }
        )
        return state

    def inventory(self) -> dict[str, Any]:
        return run_remote_json("inspect")

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        plan = run_remote_json("plan", request)
        encoded = json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        preview_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._lock:
            self._previews[preview_id] = dict(plan["request"])
            while len(self._previews) > 10:
                del self._previews[next(iter(self._previews))]
        return {**plan, "preview_id": preview_id}

    def start(self, preview_id: object) -> dict[str, Any]:
        key = str(preview_id or "").strip()
        with self._lock:
            if self._state["state"] == "running":
                raise HathPackConflict("An H@H archive job is already running")
            request = self._previews.pop(key, None)
            if request is None:
                raise HathPackConflict("Preview is missing or stale; generate a new dry run")
            run_id = int(self._state["run_id"]) + 1
            self._state = {
                **self._idle_state(),
                "run_id": run_id,
                "state": "running",
                "stage": "starting",
                "message": "Starting remote archive job",
                "started_at": utc_timestamp(),
            }
        worker = threading.Thread(
            target=self._run,
            args=(run_id, request),
            name=f"hath-archive-{run_id}",
            daemon=True,
        )
        worker.start()
        return self.status()

    def _append_log(self, run_id: int, line: str) -> None:
        clean = line.rstrip("\r\n")[:PACK_LINE_LIMIT]
        if not clean:
            return
        with self._lock:
            if self._state["run_id"] != run_id:
                return
            self._state["logs"].append(clean)
            del self._state["logs"][:-PACK_LOG_LIMIT]

    def _apply_event(self, run_id: int, event: dict[str, Any]) -> None:
        message = str(event.get("message") or "").strip()
        with self._lock:
            if self._state["run_id"] != run_id:
                return
            event_type = event.get("event")
            if event_type == "progress":
                self._state.update(
                    {
                        "stage": event.get("stage"),
                        "message": message or self._state["message"],
                        "completed": event.get("completed"),
                        "total": event.get("total"),
                    }
                )
            elif event_type == "result":
                self._state.update({"result": event.get("result"), "message": message})
            elif event_type == "error":
                self._state.update({"error": message or "Remote archive job failed", "message": message})
        if message:
            self._append_log(run_id, message)

    def _finish(self, run_id: int, exit_code: int | None, error: str | None) -> None:
        with self._lock:
            if self._state["run_id"] != run_id:
                return
            final_error = error or self._state["error"]
            self._state.update(
                {
                    "state": "succeeded" if exit_code == 0 and not final_error else "failed",
                    "stage": "finished",
                    "finished_at": utc_timestamp(),
                    "exit_code": exit_code,
                    "error": final_error,
                }
            )

    def _run(self, run_id: int, request: dict[str, Any]) -> None:
        try:
            configuration = configured_or_raise()
            process = subprocess.Popen(
                build_pack_ssh_command(configuration, "run"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if process.stdin is None or process.stdout is None:
                raise OSError("SSH pipes are unavailable")
            process.stdin.write(json.dumps(request, ensure_ascii=True))
            process.stdin.close()
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self._append_log(run_id, line)
                    continue
                if isinstance(event, dict):
                    self._apply_event(run_id, event)
            exit_code = process.wait()
        except (HathPackError, OSError) as exc:
            message = f"Could not run remote archive job: {exc}"
            self._append_log(run_id, message)
            self._finish(run_id, None, message)
            return
        error = None if exit_code == 0 else f"Remote archive job exited with code {exit_code}"
        if error:
            self._append_log(run_id, error)
        self._finish(run_id, exit_code, error)


HATH_PACK_RUNNER = HathPackRunner()
