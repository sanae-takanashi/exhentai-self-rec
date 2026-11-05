"""Configuration, status and lifecycle for the Windows launcher. No GUI imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

from scripts.launcher_logs import FAILURES, RecentLog
from scripts.launcher_windows import (
    LauncherWindowsError, ProfileMutex, StartupRegistration, StopEvent,
    WindowsJob, mutex_active, signal_stop,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "launcher" / "config.json"
ENV_FIELDS = {
    "host": "EXH_REC_HOST", "data_dir": "EXH_REC_DATA_DIR", "proxy": "EXH_REC_PROXY",
    "visual_encoder": "EXH_REC_VISUAL_ENCODER", "dino_device": "EXH_REC_DINOV2_DEVICE",
}


class LauncherError(RuntimeError):
    pass


@dataclass(frozen=True)
class LaunchConfig:
    schema: int = 1
    venv_path: str = ".venv"
    host: str = "0.0.0.0"
    port: int = 18787
    data_dir: str = ""
    proxy: str = ""
    visual_encoder: str = ""
    dino_device: str = ""
    hath_enabled: bool = False
    hath_token_file: str = ""
    hath_ssh_config: str = ""
    hath_ssh_host: str = "exh-rec-hath"
    ssh_executable: str = ""
    remote_port: int = 18788
    reconnect_seconds: int = 5
    startup_timeout: int = 300

    @classmethod
    def defaults(cls, root: Path = ROOT):
        return cls(venv_path=".venv-rocm" if (root / ".venv-rocm" / "Scripts" / "python.exe").is_file() else ".venv")

    @classmethod
    def from_dict(cls, payload: dict):
        if not isinstance(payload, dict) or set(payload) - {field.name for field in fields(cls)}:
            raise LauncherError("invalid-config")
        config = cls(**payload)
        config.validate()
        return config

    def validate(self, check_files: bool = False, root: Path = ROOT):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"schema", "port", "remote_port", "reconnect_seconds", "startup_timeout"}:
                if type(value) is not int:
                    raise LauncherError("invalid-number")
            elif field.name == "hath_enabled":
                if type(value) is not bool:
                    raise LauncherError("invalid-config")
            elif not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise LauncherError("invalid-text")
        if self.schema != 1:
            raise LauncherError("unsupported-config")
        if not 1 <= self.port <= 65535 or not 1 <= self.remote_port <= 65535:
            raise LauncherError("invalid-port")
        if not 1 <= self.reconnect_seconds <= 300 or not 10 <= self.startup_timeout <= 1800:
            raise LauncherError("invalid-timeout")
        try:
            ipaddress.IPv4Address(self.host)
        except ValueError:
            raise LauncherError("invalid-bind-address") from None
        if self.visual_encoder not in {"", "simple", "dinov2"}:
            raise LauncherError("invalid-encoder")
        if self.dino_device not in {"", "auto", "cpu", "cuda", "rocm"}:
            raise LauncherError("invalid-device")
        if not self.venv_path.strip():
            raise LauncherError("venv-required")
        if self.proxy:
            try:
                parsed = urllib.parse.urlsplit(self.proxy)
                if parsed.username is not None or parsed.password is not None:
                    raise LauncherError("proxy-credentials-not-supported")
                if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
                    raise ValueError()
            except ValueError:
                raise LauncherError("invalid-proxy") from None
        if self.hath_enabled:
            if not self.hath_token_file or not self.hath_ssh_config:
                raise LauncherError("hath-files-required")
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:\-]*", self.hath_ssh_host):
                raise LauncherError("invalid-ssh-host")
        if check_files:
            if not self.service_python(root).is_file():
                raise LauncherError("venv-python-missing")
            if self.hath_enabled:
                if not self.resolve(self.hath_token_file, root).is_file():
                    raise LauncherError("hath-token-file-missing")
                if not self.resolve(self.hath_ssh_config, root).is_file():
                    raise LauncherError("hath-ssh-config-missing")
                if not Path(self.ssh_program()).is_file():
                    raise LauncherError("ssh-executable-missing")

    @staticmethod
    def resolve(value: str, root: Path = ROOT) -> Path:
        path = Path(value).expanduser()
        return (root / path).resolve() if not path.is_absolute() else path.resolve()

    def service_python(self, root: Path = ROOT) -> Path:
        return self.resolve(self.venv_path, root) / "Scripts" / "python.exe"

    def ssh_program(self) -> str:
        return str(self.resolve(self.ssh_executable)) if self.ssh_executable else str(
            Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe")

    def home_url(self) -> str:
        self.validate()
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}/"

    def environment(self, root: Path = ROOT) -> dict[str, str]:
        environment = os.environ.copy()
        for field, variable in ENV_FIELDS.items():
            value = getattr(self, field)
            if value:
                environment[variable] = str(self.resolve(value, root)) if field == "data_dir" else value
        environment["EXH_REC_PORT"] = str(self.port)
        # The supervisor reads credentials itself. A disabled integration must not
        # inherit an ambient token or pack connection from a previous shell.
        for variable in ("EXH_REC_HATH_TOKEN", "EXH_REC_HATH_TOKEN_FILE", "EXH_REC_HATH_SSH_CONFIG",
                         "EXH_REC_HATH_SSH_HOST", "EXH_REC_HATH_SSH_EXECUTABLE"):
            environment.pop(variable, None)
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def service_command(self, root: Path = ROOT) -> list[str]:
        arguments = [str(self.service_python(root)), str(root / "scripts/service_bootstrap.py")]
        if not self.hath_enabled:
            return [*arguments, "server", "exh_rec.app"]
        return [*arguments, "hath", str(root / "scripts/run_with_hath.py"),
                "--token-file", str(self.resolve(self.hath_token_file, root)),
                "--ssh-config", str(self.resolve(self.hath_ssh_config, root)),
                "--ssh-host", self.hath_ssh_host, "--local-port", str(self.port),
                "--remote-port", str(self.remote_port), "--reconnect-seconds", str(self.reconnect_seconds),
                "--ssh-executable", self.ssh_program()]


def load_config(path: Path = DEFAULT_CONFIG, root: Path = ROOT) -> LaunchConfig:
    if not path.exists():
        return LaunchConfig.defaults(root)
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        return LaunchConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        raise LauncherError("config-read-failed") from None


def atomic_json(path: Path, payload: dict, backup: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    if json.loads(temporary.read_text(encoding="utf-8")) != payload:
        raise LauncherError("config-readback-failed")
    if backup and path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.{uuid.uuid4().hex[:8]}.bak"))
    os.replace(temporary, path)


def save_config(config: LaunchConfig, path: Path = DEFAULT_CONFIG):
    config.validate()
    try:
        atomic_json(path, asdict(config), backup=True)
        if load_config(path) != config:
            raise LauncherError("config-readback-failed")
    except OSError:
        raise LauncherError("config-save-failed") from None


def config_digest(config: LaunchConfig) -> str:
    return hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode("utf-8")).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_service(config: LaunchConfig, timeout: float = 0.6, *, check_available: bool = True) -> str:
    """Return ready, legacy, occupied or stopped. Never return response bodies."""
    endpoint = urllib.parse.urlsplit(config.home_url())
    # Closed loopback ports can time out instead of refusing on Windows. An
    # exclusive bind distinguishes an unused address without waiting on TCP.
    # Never bind while our child may be starting its own listener.
    if check_available:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as available:
            if os.name == "nt":
                available.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                available.bind((config.host, config.port))
            except OSError:
                pass
            else:
                return "stopped"
    try:
        with socket.create_connection((endpoint.hostname, config.port), timeout=timeout):
            pass
    except ConnectionRefusedError:
        return "stopped"
    except OSError:
        return "occupied"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(config.home_url() + "api/health", timeout=timeout) as response:
            if not response.headers.get("Server", "").startswith("ExhRec/"):
                return "occupied"
            payload = json.loads(response.read(4096))
        if payload == {"service": "exh-rec", "status": "ok"}:
            return "ready"
    except urllib.error.HTTPError as error:
        legacy = error.code == 404 and error.headers.get("Server", "").startswith("ExhRec/")
        error.close()
        if not legacy:
            return "occupied"
        try:
            with opener.open(config.home_url(), timeout=timeout) as response:
                if response.headers.get("Server", "").startswith("ExhRec/") and b"<title>ExHentai Recommender</title>" in response.read(16384):
                    return "legacy"
        except Exception:
            pass
    except Exception:
        pass
    return "occupied"


def profile_id(config_path: Path) -> str:
    return hashlib.sha256(str(config_path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]


def mutex_name(config_path: Path) -> str:
    return "Local\\ExhRecLauncher-" + profile_id(config_path)


def event_name(config_path: Path, run_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise LauncherError("invalid-runtime-state")
    return mutex_name(config_path) + "-Stop-" + run_id


def runtime_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.stem + ".runtime.json")


def read_runtime(config_path: Path) -> dict:
    try:
        path = runtime_path(config_path)
        if path.stat().st_size > 8192:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("profile_id") != profile_id(config_path):
            return {}
        event_name(config_path, payload.get("run_id", ""))
        if payload.get("state") not in {"starting", "running", "stopping", "stopped", "failed"}:
            return {}
        if type(payload.get("supervisor_pid")) is not int or payload["supervisor_pid"] <= 0:
            return {}
        if not isinstance(payload.get("error"), str) or len(payload["error"]) > 100:
            return {}
        if "config_digest" in payload and not re.fullmatch(r"[0-9a-f]{64}", str(payload["config_digest"])):
            return {}
        if "host" in payload or "port" in payload:
            LaunchConfig.from_dict({"host": payload.get("host"), "port": payload.get("port")})
        return payload
    except (OSError, ValueError, TypeError, LauncherError):
        return {}


def launcher_python() -> Path:
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    return windowless if windowless.is_file() else executable


def worker_command(config_path: Path, executable: Path | None = None) -> list[str]:
    return [str(executable or launcher_python()), str(ROOT / "scripts/launcher.py"),
            "--background", "--config", str(config_path.resolve())]


def startup_registration(config_path: Path = DEFAULT_CONFIG) -> StartupRegistration:
    command = subprocess.list2cmdline(worker_command(config_path))
    return StartupRegistration("ExhRecLauncher-" + profile_id(config_path), command)


class LauncherController:
    def __init__(self, config_path: Path = DEFAULT_CONFIG):
        self.path = config_path.resolve()

    def status(self, config: LaunchConfig) -> dict:
        live = mutex_active(mutex_name(self.path))
        runtime = read_runtime(self.path)
        if live:
            active = LaunchConfig.from_dict({**asdict(config), "host": runtime.get("host", config.host),
                                             "port": runtime.get("port", config.port)})
            probe = probe_service(active, check_available=False)
            state = "running" if probe in {"ready", "legacy"} and runtime.get("state") == "running" else "starting"
            if runtime.get("state") == "running" and probe not in {"ready", "legacy"}:
                state = "unresponsive"
            if runtime.get("state") == "stopping":
                state = "stopping"
            return {"state": state, "managed": bool(runtime), "home_url": active.home_url(),
                    "error": runtime.get("error", ""), "health": probe,
                    "started_at": runtime.get("started_at", ""), "run_id": runtime.get("run_id", ""),
                    "profile_match": config_digest(config) == runtime["config_digest"] if "config_digest" in runtime else None}
        probe = probe_service(config)
        if probe in {"ready", "legacy"}:
            return {"state": "external", "managed": False, "home_url": config.home_url(), "error": "", "health": probe}
        state = "occupied" if probe == "occupied" else "failed" if runtime.get("state") == "failed" else "stopped"
        return {"state": state, "managed": False, "home_url": config.home_url(), "error": runtime.get("error", ""), "health": probe}

    def start(self, config: LaunchConfig) -> str:
        status = self.status(config)
        if status["managed"] or status["state"] in {"running", "starting", "stopping", "external"}:
            return "already-running"
        if status["state"] == "occupied":
            raise LauncherError("port-occupied")
        config.validate(check_files=True)
        save_config(config, self.path)
        worker = subprocess.Popen(worker_command(self.path), cwd=ROOT, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        deadline = time.monotonic() + 5
        while not mutex_active(mutex_name(self.path)):
            if worker.poll() is not None:
                if worker.returncode == 0 and probe_service(config) in {"ready", "legacy"}:
                    return "already-running"
                raise LauncherError(read_runtime(self.path).get("error") or "launch-failed")
            if time.monotonic() >= deadline:
                break
            time.sleep(0.025)
        return "start-requested"

    def stop(self, timeout: float = 20) -> None:
        if not mutex_active(mutex_name(self.path)):
            raise LauncherError("no-managed-service")
        runtime = read_runtime(self.path)
        if not runtime:
            raise LauncherError("runtime-not-ready")
        signal_stop(event_name(self.path, runtime["run_id"]))
        deadline = time.monotonic() + timeout
        while mutex_active(mutex_name(self.path)):
            if time.monotonic() >= deadline:
                raise LauncherError("stop-timeout")
            time.sleep(0.1)
        final = read_runtime(self.path)
        if final.get("run_id") == runtime["run_id"] and final.get("state") == "failed":
            raise LauncherError(final.get("error") or "stop-failed")

    def open_home(self, config: LaunchConfig) -> bool:
        status = self.status(config)
        if status["state"] not in {"running", "external"}:
            raise LauncherError("service-not-ready")
        return webbrowser.open(status["home_url"], new=2)


def supervise_background(config_path: Path) -> int:
    lock = ProfileMutex(mutex_name(config_path))
    if not lock.owned:
        lock.close()
        return 0
    config = None
    job = None
    stop = None
    child = None
    launched = False
    output_reader = None
    failure = ""
    run_id = uuid.uuid4().hex
    recent_log = RecentLog(config_path, run_id)
    runtime = {"profile_id": profile_id(config_path), "run_id": run_id, "supervisor_pid": os.getpid(),
               "state": "starting", "error": "", "started_at": datetime.now(timezone.utc).isoformat()}

    def publish(state: str, error: str = ""):
        runtime.update(state=state, error=error)
        atomic_json(runtime_path(config_path), runtime)
        fields = {"reason": error if error in FAILURES else "launch-failed"} if state == "failed" else {}
        recent_log.add("launcher." + state, **fields)
        recent_log.flush(force=True)

    try:
        config = load_config(config_path)
        config.validate(check_files=True)
        runtime.update(host=config.host, port=config.port, config_digest=config_digest(config))
        probe = probe_service(config)
        if probe in {"ready", "legacy"}:
            return 0
        if probe != "stopped":
            raise LauncherError("port-occupied")
        stop = StopEvent(event_name(config_path, run_id))
        publish("starting")
        job = WindowsJob()
        child = subprocess.Popen(config.service_command(), cwd=ROOT, env=config.environment(),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(child.pid)
        output_reader = threading.Thread(target=recent_log.capture, args=(child.stdout,), daemon=True,
                                         name="launcher-output")
        output_reader.start()
        child.stdin.write(b"1")
        child.stdin.flush()
        child.stdin.close()
        launched = True
        deadline = time.monotonic() + config.startup_timeout
        ready = False
        while True:
            recent_log.flush()
            if stop.wait():
                publish("stopping")
                break
            if child.poll() is not None:
                raise LauncherError("service-exited")
            if not ready:
                if probe_service(config, check_available=False) == "ready":
                    ready = True
                    publish("running")
                elif time.monotonic() >= deadline:
                    raise LauncherError("startup-timeout")
    except (LauncherError, LauncherWindowsError) as error:
        failure = str(error)
    except Exception:
        failure = "launch-failed"
    finally:
        try:
            if child is not None and child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
        except OSError:
            failure = failure or "cleanup-failed"
        try:
            if job is not None:
                try:
                    job.stop()
                finally:
                    job.close()
        except Exception:
            failure = "cleanup-failed"
        try:
            if child is not None and child.poll() is None:
                child.terminate()
            if child is not None:
                child.wait(timeout=5)
        except Exception:
            failure = "cleanup-failed"
        finally:
            try:
                if output_reader is not None:
                    output_reader.join(timeout=2)
                elif child is not None and child.stdout is not None:
                    child.stdout.close()
                if failure:
                    publish("failed", failure)
                elif launched:
                    publish("stopped")
            finally:
                if stop is not None:
                    stop.close()
                lock.close()
    return 1 if failure else 0
