"""Optional Windows tray bridge. Callbacks enqueue commands; never call Tk."""
import json
from pathlib import Path
import queue
import threading

from exh_rec.jobs import KINDS, TERMINAL
from scripts.launcher_core import LauncherError, atomic_json


DEFAULTS = {"schema": 1, "close_to_tray": True, "notifications": True}


def load_preferences(path):
    if not Path(path).exists():
        return dict(DEFAULTS)
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != set(DEFAULTS)
                or type(value["schema"]) is not int or value["schema"] != 1
                or any(type(value[key]) is not bool for key in ("close_to_tray", "notifications"))):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError):
        raise LauncherError("tray-settings-unavailable") from None


def save_preferences(path, *, close_to_tray, notifications):
    if type(close_to_tray) is not bool or type(notifications) is not bool:
        raise LauncherError("tray-settings-unavailable")
    value = {"schema": 1, "close_to_tray": close_to_tray, "notifications": notifications}
    try:
        atomic_json(Path(path), value, backup=True)
        if load_preferences(path) != value:
            raise ValueError()
    except (OSError, ValueError):
        raise LauncherError("tray-settings-unavailable") from None


class Notifications:
    def __init__(self):
        self.states = None

    def observe(self, snapshot):
        if snapshot.get("availability") != "available":
            return []
        jobs = snapshot["jobs"]
        current = {job["id"]: job["state"] for job in jobs}
        messages = []
        if self.states is not None:
            for job in jobs:
                state = job["state"]
                if state in TERMINAL and self.states.get(job["id"]) != state:
                    # No job name, query, path, gallery text or error payload.
                    kinds = ", ".join(dict.fromkeys(KINDS[step["kind"]] for step in job["steps"]))
                    messages.append(f"{kinds}: {state}.")
        self.states = current
        if len(messages) > 3:
            return [f"{len(messages)} jobs finished. Open Jobs for results."]
        return messages


class TrayBridge:
    def __init__(self):
        self.events = queue.Queue()
        self.ready = threading.Event()
        self.failed = threading.Event()
        self.stopping = threading.Event()
        self.icon = None
        self.thread = None
        self.state = {}

    @property
    def available(self):
        return self.ready.is_set() and not self.failed.is_set() and self.thread is not None and self.thread.is_alive()

    def start(self, image_path):
        try:
            import pystray
            from PIL import Image
            with Image.open(image_path) as source:
                bitmap = source.convert("RGBA")
            item, menu = pystray.MenuItem, pystray.Menu

            def entry(label, command, gate=None, default=False):
                def invoke(icon, selected):
                    self.events.put(command)
                return item(label, invoke, default=default,
                            enabled=lambda _: gate is None or self.state.get(gate, False))

            self.icon = pystray.Icon("exh-rec-launcher", bitmap, "ExHentai Recommender", menu(
                entry("Show Launcher", "show", default=True), entry("Show Jobs", "jobs"),
                entry("Open Home", "home", "home"), menu.SEPARATOR,
                entry("Start Saved Profile", "start", "start"), entry("Stop Managed Service", "stop", "stop"),
                item("Queue Job", menu(*(entry(KINDS[kind], "queue:" + kind, "jobs")
                                         for kind in ("fetch", "enrich", "retrain", "backup")))),
                menu.SEPARATOR, entry("Exit Launcher", "exit")))

            def setup(icon):
                try:
                    if self.stopping.is_set():
                        icon.stop()
                        return
                    icon.visible = True
                    self.ready.set()
                except Exception:
                    self.failed.set()
                    icon.stop()

            def run():
                try:
                    self.icon.run(setup)
                except Exception:
                    self.failed.set()
                finally:
                    self.ready.clear()
                    self.events.put("tray-ended")
            # pystray permits a non-main run thread on Windows.
            self.thread = threading.Thread(target=run, name="launcher-tray", daemon=True)
            self.thread.start()
            return True
        except Exception:
            self.failed.set()
            return False

    def update(self, status, *, busy=False, jobs=False):
        state = status.get("state")
        self.state = {"home": not busy and state in {"running", "external"},
                      "start": not busy and not status.get("managed") and state in {"stopped", "failed"},
                      "stop": not busy and bool(status.get("managed")) and state != "stopping",
                      "jobs": not busy and jobs}
        if self.available:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def notify(self, message):
        if self.available and self.icon.HAS_NOTIFICATION:
            try:
                self.icon.notify(message, "ExHentai Recommender")
            except Exception:
                pass

    def close(self):
        self.stopping.set()
        if self.icon:
            try:
                self.icon.remove_notification()
                self.icon.stop()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=3)
        self.ready.clear()
