"""Render retained synthetic native UI previews without touching a live profile."""
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.launcher import LauncherApp
from scripts.launcher_checkup import CHECKS, service_key
from scripts.launcher_core import config_digest
from scripts.launcher_ops import timestamp


def main():
    import tkinter as tk
    from PIL import ImageGrab, ImageStat

    directory = Path(tempfile.mkdtemp(prefix="launcher-preview-"))
    root = tk.Tk()
    root.withdraw()
    app = LauncherApp(root, directory / "synthetic-profile.json")
    try:
        for timer in list(app.timers):
            root.after_cancel(timer)
        app.timers.clear()
        status = {"state": "running", "managed": True, "health": "ready", "run_id": "synthetic",
                  "profile_match": True, "started_at": timestamp(), "checked_at": timestamp(),
                  "local_source": {"revision": "a" * 40, "source_digest": "b" * 64},
                  "runtime_identity": {"availability": "available", "revision": "c" * 40, "source_digest": "d" * 64,
                                       "python": "3.12.10", "started_at": timestamp()}}
        jobs = []
        for index, (name, state, stage, kinds) in enumerate((
                ("Daily refresh", "paused", "query_page", ("backup", "fetch", "retrain")),
                ("Enrich", "queued", "queued", ("enrich",)),
                ("Backup", "succeeded", "finished", ("backup",)),
                ("Fetch", "partial", "finished", ("fetch",)))):
            item = {"id": f"{index:032x}", "name": name, "stage": stage, "state": state, "source": "launcher",
                    "created_at": timestamp(), "updated_at": timestamp(), "started_at": None, "finished_at": None,
                    "retry_of": None, "error_code": ""}
            item["steps"] = [{"kind": kind, "params": {}, "state": "succeeded" if kind == "backup" else "running" if state == "paused" and kind == "fetch" else "pending",
                              "result": {"verified": True} if kind == "backup" else {}} for kind in kinds]
            jobs.append(item)
        status["jobs"] = {"availability": "available", "worker": True, "jobs": jobs,
                          "counts": {"paused": 1, "queued": 1, "succeeded": 1, "partial": 1}, "workflows": []}
        app.latest = status
        view = app.operations
        view.begin_checkup()
        observations = (
            ("pass", "Identity endpoint verified", "No action needed for local API readiness."),
            ("warn", "Source differs from startup snapshot", "Plan an owner-controlled restart after checking active tasks in the web UI."),
            ("pass", "Required launch files exist", "Existence only: credentials were not opened and SSH authentication was not tested."),
            ("pass", "Required base packages present", "Selected Python only; running-service model settings may differ."),
            ("unknown", "TCP reachable; upstream unverified", "Check the saved proxy and its local listener. Upstream access and authentication were not tested."),
            ("skip", "Disabled in saved profile", "No SSH connection was opened."),
            ("skip", "Disabled in saved profile", "The observer database was not read."),
        )
        rows = []
        for (key, component), (outcome, summary, action) in zip(CHECKS, observations):
            row = {"key": key, "component": component, "outcome": outcome, "summary": summary,
                   "action": action, "checked_at": timestamp()}
            rows.append(row)
            view.render_checkup_row(row)
        view.finish_checkup({"rows": rows, "profile_digest": config_digest(app.config), "service_key": service_key(status),
                             "local_source": status["local_source"], "finished_at": timestamp()})
        app.render_status(status)
        root.attributes("-topmost", True)
        for width, height in ((780, 760), (880, 760), (1100, 900)):
            root.geometry(f"{width}x{height}+30+30")
            root.deiconify()
            root.update()
            time.sleep(0.2)
            root.update_idletasks()
            left, top = root.winfo_rootx(), root.winfo_rooty()
            image = ImageGrab.grab(bbox=(left, top, left + root.winfo_width(), top + root.winfo_height()))
            if max(ImageStat.Stat(image.convert("RGB")).stddev) < 8:
                raise RuntimeError("Blank synthetic UI preview")
            image.save(directory / f"overview-{width}x{height}.png")
            for name, frame in (("jobs", app.jobs.frame), ("environment", app.operations.environment)):
                app.notebook.select(frame)
                root.update()
                time.sleep(0.2)
                image = ImageGrab.grab(bbox=(left, top, left + root.winfo_width(), top + root.winfo_height()))
                if max(ImageStat.Stat(image.convert("RGB")).stddev) < 8:
                    raise RuntimeError("Blank synthetic UI preview")
                image.save(directory / f"{name}-{width}x{height}.png")
            app.notebook.select(app.operations.overview)
        app.busy = True
        app.operation_text.set("Checking selected environment (20s limit) | 8s")
        app.operation_progress.grid()
        app.operation_progress.configure(value=35)
        app.render_status(status)
        root.geometry("780x760+30+30")
        root.update()
        time.sleep(0.2)
        left, top = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(left, top, left + root.winfo_width(), top + root.winfo_height())).save(directory / "operation-780x760.png")
    finally:
        app.close()
    print(directory)


if __name__ == "__main__":
    main()
