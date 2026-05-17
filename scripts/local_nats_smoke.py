"""Manage a local NATS-backed Device Connect smoke environment.

This script wraps the Device Connect integration-test Docker Compose stack and
the Reachy Mini simulated smoke tests.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import importlib.util


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEVICE_CONNECT_ROOT = Path.home() / "src" / "device-connect"
SERVICES = ("nats", "etcd", "device-registry-service")


def main() -> None:
    args = build_parser().parse_args()
    device_connect_root = Path(args.device_connect_root).expanduser().resolve()
    compose_file = device_connect_root / "tests" / "docker-compose-itest.yml"
    if not compose_file.exists():
        raise SystemExit(f"Device Connect compose file not found: {compose_file}")

    if args.command == "start":
        start(compose_file, args.timeout)
    elif args.command == "stop":
        stop(compose_file)
    elif args.command == "status":
        status(compose_file)
    elif args.command == "broker-smoke":
        check_python_dependencies(require_mcp=False)
        run_smoke("smoke_broker_mcp_tools.py", args.device_id, args.tenant, args.timeout)
    elif args.command == "stdio-smoke":
        check_python_dependencies(require_mcp=True)
        run_smoke("smoke_stdio_mcp_bridge.py", args.device_id, args.tenant, args.timeout)
    elif args.command == "all":
        check_python_dependencies(require_mcp=args.stdio)
        start(compose_file, args.timeout)
        try:
            run_smoke("smoke_broker_mcp_tools.py", args.device_id, args.tenant, args.timeout)
            if args.stdio:
                run_smoke("smoke_stdio_mcp_bridge.py", args.device_id, args.tenant, args.timeout)
        finally:
            if not args.keep_running:
                stop(compose_file)
    else:
        raise SystemExit(f"unknown command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device-connect-root",
        default=str(DEFAULT_DEVICE_CONNECT_ROOT),
        help="Path to the local device-connect checkout.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="Start local NATS/etcd/registry services.")
    start_parser.add_argument("--timeout", type=float, default=120.0)

    sub.add_parser("stop", help="Stop local smoke services and remove volumes.")
    sub.add_parser("status", help="Show local smoke service status.")

    broker = sub.add_parser("broker-smoke", help="Run broker-backed hierarchical tool smoke.")
    add_smoke_args(broker, default_device_id="reachy-mini-sim-mcp-smoke")

    stdio = sub.add_parser("stdio-smoke", help="Run stdio MCP bridge smoke.")
    add_smoke_args(stdio, default_device_id="reachy-mini-sim-stdio-mcp")

    all_parser = sub.add_parser("all", help="Start services, run smoke tests, then stop.")
    add_smoke_args(all_parser, default_device_id="reachy-mini-sim-mcp-smoke")
    all_parser.add_argument("--stdio", action="store_true", help="Also run stdio MCP smoke.")
    all_parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Leave local smoke services running after tests complete.",
    )
    return parser


def add_smoke_args(parser: argparse.ArgumentParser, *, default_device_id: str) -> None:
    parser.add_argument("--device-id", default=default_device_id)
    parser.add_argument("--tenant", default=os.getenv("TENANT", "default"))
    parser.add_argument("--timeout", type=float, default=30.0)


def start(compose_file: Path, timeout: float) -> None:
    run_compose(compose_file, "up", "-d", *SERVICES)
    wait_for_port("127.0.0.1", 4222, timeout, "NATS")
    wait_for_port("127.0.0.1", 8000, timeout, "device registry")
    status(compose_file)


def stop(compose_file: Path) -> None:
    run_compose(compose_file, "down", "-v", "--remove-orphans")


def status(compose_file: Path) -> None:
    run_compose(compose_file, "ps")


def run_smoke(script_name: str, device_id: str, tenant: str, timeout: float) -> None:
    script = REPO_ROOT / "tests" / script_name
    env = smoke_env()
    env["TENANT"] = tenant
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--device-id",
            device_id,
            "--tenant",
            tenant,
            "--timeout",
            str(timeout),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def check_python_dependencies(*, require_mcp: bool) -> None:
    missing = []
    for module in ("nats", "nkeys"):
        if importlib.util.find_spec(module) is None:
            missing.append(module)
    if require_mcp and importlib.util.find_spec("fastmcp") is None:
        missing.append("fastmcp")
    if not missing:
        return
    extras = ".[dev]"
    agent_extra = "device-connect-agent-tools[mcp]" if require_mcp else "device-connect-agent-tools"
    raise SystemExit(
        "Missing Python smoke dependencies: "
        + ", ".join(missing)
        + "\nInstall the local packages first, for example:\n"
        + f"  python3 -m pip install -e {REPO_ROOT!s} {extras!r}\n"
        + "  python3 -m pip install -e "
        + f"{DEFAULT_DEVICE_CONNECT_ROOT / 'packages' / 'device-connect-edge'}\n"
        + "  python3 -m pip install -e "
        + f"{DEFAULT_DEVICE_CONNECT_ROOT / 'packages' / 'device-connect-agent-tools'}"
        + (f"[mcp]" if require_mcp else "")
        + f"\nThe stdio MCP smoke requires {agent_extra}."
    )


def smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    device_connect_root = Path(
        env.get("DEVICE_CONNECT_ROOT", str(DEFAULT_DEVICE_CONNECT_ROOT))
    ).expanduser()
    paths = [
        str(REPO_ROOT / "src"),
        str(device_connect_root / "packages" / "device-connect-edge"),
        str(device_connect_root / "packages" / "device-connect-agent-tools"),
    ]
    existing = env.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("DEVICE_CONNECT_ALLOW_INSECURE", "true")
    env.setdefault("DEVICE_CONNECT_DISCOVERY_MODE", "infra")
    env.setdefault("TENANT", "default")
    env["MESSAGING_BACKEND"] = "nats"
    env["MESSAGING_URLS"] = "nats://127.0.0.1:4222"
    env["NATS_URL"] = "nats://127.0.0.1:4222"
    return env


def run_compose(compose_file: Path, *args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        cwd=compose_file.parent,
        check=True,
    )


def wait_for_port(host: str, port: int, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise TimeoutError(f"timed out waiting for {label} on {host}:{port}")


if __name__ == "__main__":
    main()
