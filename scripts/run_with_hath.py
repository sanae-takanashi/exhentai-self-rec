from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence


def read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read H@H token file: {path}") from exc
    if len(token) < 32 or any(character.isspace() for character in token):
        raise RuntimeError("H@H token must be at least 32 characters with no whitespace")
    return token


def build_ssh_command(
    ssh_executable: str,
    ssh_config: Path,
    ssh_host: str,
    local_port: int,
    remote_port: int,
) -> list[str]:
    return [
        ssh_executable,
        "-F",
        ssh_config.as_posix(),
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ConnectTimeout=15",
        "-R",
        f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}",
        ssh_host,
    ]


def ensure_local_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise RuntimeError(
            f"Local port {port} is already in use; stop the existing rec instance before starting the H@H launcher"
        ) from exc
    finally:
        probe.close()


def stop_process(process: Optional[subprocess.Popen[bytes]], name: str) -> None:
    if process is None or process.poll() is not None:
        return
    print(f"Stopping {name}...", flush=True)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def configure_pack_environment(
    environment: dict[str, str],
    ssh_executable: str,
    ssh_config: Path,
    ssh_host: str,
) -> None:
    environment["EXH_REC_HATH_SSH_EXECUTABLE"] = ssh_executable
    environment["EXH_REC_HATH_SSH_CONFIG"] = ssh_config.as_posix()
    environment["EXH_REC_HATH_SSH_HOST"] = ssh_host


def supervise(
    token_file: Path,
    ssh_config: Path,
    ssh_host: str,
    local_port: int,
    remote_port: int,
    reconnect_seconds: float,
    ssh_executable: str,
) -> int:
    token = read_token(token_file)
    if not ssh_config.is_file():
        raise RuntimeError(f"SSH config does not exist: {ssh_config}")
    explicit_ssh = Path(ssh_executable).expanduser()
    resolved_ssh = str(explicit_ssh) if explicit_ssh.is_file() else shutil.which(ssh_executable)
    if not resolved_ssh:
        raise RuntimeError(f"SSH executable not found: {ssh_executable}")
    ensure_local_port_available(local_port)

    environment = os.environ.copy()
    environment["EXH_REC_HATH_TOKEN"] = token
    environment["EXH_REC_PORT"] = str(local_port)
    environment["PYTHONUNBUFFERED"] = "1"
    configure_pack_environment(environment, resolved_ssh, ssh_config, ssh_host)
    receiver = subprocess.Popen([sys.executable, "-m", "exh_rec.app"], env=environment)
    tunnel: Optional[subprocess.Popen[bytes]] = None
    next_tunnel_start = 0.0
    ssh_command = build_ssh_command(resolved_ssh, ssh_config, ssh_host, local_port, remote_port)
    print(
        f"H@H receiver token loaded from {token_file}; remote 127.0.0.1:{remote_port} forwards to rec 127.0.0.1:{local_port}",
        flush=True,
    )

    try:
        while receiver.poll() is None:
            now = time.monotonic()
            if tunnel is not None and tunnel.poll() is not None:
                print(
                    f"H@H SSH tunnel exited with code {tunnel.returncode}; retrying in {reconnect_seconds:g}s",
                    flush=True,
                )
                tunnel = None
                next_tunnel_start = now + reconnect_seconds
            if tunnel is None and now >= next_tunnel_start:
                print(f"Starting H@H SSH tunnel through {ssh_host}...", flush=True)
                tunnel = subprocess.Popen(ssh_command)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Stopping rec and H@H tunnel...", flush=True)
    finally:
        stop_process(tunnel, "H@H SSH tunnel")
        stop_process(receiver, "rec server")
    return int(receiver.returncode or 0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the recommender with a supervised H@H reverse SSH tunnel.")
    result.add_argument("--token-file", type=Path, required=True)
    result.add_argument("--ssh-config", type=Path, required=True)
    result.add_argument("--ssh-host", default="exh-rec-hath")
    result.add_argument("--local-port", type=int, default=18787)
    result.add_argument("--remote-port", type=int, default=18788)
    result.add_argument("--reconnect-seconds", type=float, default=5.0)
    result.add_argument("--ssh-executable", default="ssh.exe" if os.name == "nt" else "ssh")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.local_port <= 65535 or not 1 <= args.remote_port <= 65535:
        parser().error("local and remote ports must be between 1 and 65535")
    try:
        return supervise(
            token_file=args.token_file.resolve(),
            ssh_config=args.ssh_config.resolve(),
            ssh_host=args.ssh_host,
            local_port=args.local_port,
            remote_port=args.remote_port,
            reconnect_seconds=max(1.0, args.reconnect_seconds),
            ssh_executable=args.ssh_executable,
        )
    except RuntimeError as exc:
        print(f"H@H launcher error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
