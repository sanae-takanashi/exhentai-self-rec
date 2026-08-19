import argparse
import json
import os
import socket
import time
from pathlib import Path

from .core import ObserverConfig, StateStore, scan_and_enqueue, send_pending


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Observe an official H@H downloader without modifying it.")
    result.add_argument("--download-dir", default=os.environ.get("HATH_DOWNLOAD_DIR", ""), help="H@H download directory")
    result.add_argument("--log-file", default=os.environ.get("HATH_LOG_FILE", ""), help="H@H log_out path")
    result.add_argument("--server-url", default=os.environ.get("EXH_REC_URL", ""), help="Recommendation server base URL")
    result.add_argument("--token", default=os.environ.get("EXH_REC_HATH_TOKEN", ""), help="Receiver bearer token")
    result.add_argument("--client-id", default=os.environ.get("HATH_CLIENT_ID", socket.gethostname()))
    result.add_argument(
        "--state-db",
        default=os.environ.get("HATH_OBSERVER_STATE", str(Path.home() / ".hath-observer" / "state.sqlite3")),
    )
    result.add_argument("--interval", type=float, default=float(os.environ.get("HATH_OBSERVER_INTERVAL", "10")))
    result.add_argument("--request-timeout", type=float, default=15.0)
    result.add_argument("--heartbeat-interval", type=float, default=60.0)
    result.add_argument("--pid", type=int)
    result.add_argument("--pid-file", default=os.environ.get("HATH_PID_FILE", ""))
    result.add_argument("--systemd-unit", default=os.environ.get("HATH_SYSTEMD_UNIT", ""))
    result.add_argument("--backfill", action="store_true", help="Report downloads that exist during the first scan")
    result.add_argument("--once", action="store_true", help="Scan and attempt delivery once, then exit")
    return result


def main() -> None:
    args = parser().parse_args()
    if not args.download_dir:
        parser().error("--download-dir or HATH_DOWNLOAD_DIR is required")
    config = ObserverConfig(
        client_id=args.client_id,
        download_dir=Path(args.download_dir).expanduser().resolve(),
        state_db=Path(args.state_db).expanduser().resolve(),
        log_file=Path(args.log_file).expanduser().resolve() if args.log_file else None,
        server_url=args.server_url,
        token=args.token,
        backfill=args.backfill,
        pid=args.pid,
        pid_file=Path(args.pid_file).expanduser().resolve() if args.pid_file else None,
        systemd_unit=args.systemd_unit,
        request_timeout=max(1.0, args.request_timeout),
        heartbeat_interval=max(5.0, args.heartbeat_interval),
    )
    store = StateStore(config.state_db)
    try:
        while True:
            scan = scan_and_enqueue(store, config)
            delivery = send_pending(store, config)
            print(json.dumps({"scan": scan, "delivery": delivery}, ensure_ascii=True), flush=True)
            if args.once:
                break
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
