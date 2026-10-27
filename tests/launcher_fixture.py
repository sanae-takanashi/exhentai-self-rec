"""Synthetic Windows launcher subprocesses. No production network or data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("supervise", "serve", "sleep", "exit"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--scenario", default="normal")
    args = parser.parse_args()
    if args.mode == "sleep":
        time.sleep(120)
        return 0
    if args.mode == "exit":
        print("ModuleNotFoundError: fixture-only-private-module", file=sys.stderr, flush=True)
        return 7
    if args.mode == "supervise":
        from scripts.launcher_core import LaunchConfig, supervise_background
        from scripts.launcher_windows import LauncherWindowsError, WindowsJob
        mode = "exit" if args.scenario == "exit" else "serve"
        command = [sys.executable, str(ROOT / "scripts/service_bootstrap.py"), "hath", str(Path(__file__)),
                   mode, "--records", str(args.records), "--scenario", args.scenario]
        with patch.object(LaunchConfig, "service_command", return_value=command):
            if args.scenario == "assign-failure":
                with patch.object(WindowsJob, "assign", side_effect=LauncherWindowsError("containment-unavailable")):
                    return supervise_background(args.config)
            if args.scenario == "cleanup-failure":
                with patch.object(WindowsJob, "stop", side_effect=LauncherWindowsError("stop-failed")):
                    return supervise_background(args.config)
            return supervise_background(args.config)

    child = subprocess.Popen([sys.executable, str(Path(__file__)), "sleep", "--records", str(args.records)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    args.records.write_text(json.dumps({"parent": os.getpid(), "child": child.pid}), encoding="utf-8")
    print("Serving ExHentai recommender at http://127.0.0.1", flush=True)
    print("Authorization: Bearer fixture-private-output-never-save", file=sys.stderr, flush=True)
    if args.scenario == "log-flood":
        print("x" * (256 * 1024), flush=True)
        print("[feedback] action=record fixture-only", flush=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ExhRec/fixture"

        def log_message(self, *unused):
            pass

        def do_GET(self):
            if self.path == "/api/health" and args.scenario != "timeout":
                body = b'{"service":"exh-rec","status":"ok"}'
                kind = "application/json"
            else:
                body = b"<title>Launcher Verification</title><h1>Synthetic launcher service</h1>"
                kind = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer(("127.0.0.1", int(os.environ["EXH_REC_PORT"])), Handler).serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
