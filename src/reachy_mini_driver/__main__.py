"""CLI runner for the Reachy Mini Device Connect/MHP driver."""

from __future__ import annotations

import argparse
import asyncio
import logging

from reachy_mini_driver.runtime_launcher import gather_cli_run_params, run_device_connect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--tenant", default=None)
    parser.add_argument(
        "--target",
        default=None,
        help="Reachy target alias or host[:port]. Use 'sim' for driver-level simulation.",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--api-port", type=int, default=None)
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Use the driver-level simulated target.",
    )
    parser.add_argument("--messaging-backend", default=None)
    parser.add_argument("--messaging-url", action="append", default=None)
    parser.add_argument("--nats-credentials-file", default=None)
    parser.add_argument(
        "--portal",
        action="store_true",
        help=(
            "Connect to portal.deviceconnect.dev using NATS credentials. "
            "Auto-discovers ~/Downloads/*.json when no credentials file is set."
        ),
    )
    parser.add_argument(
        "--portal-credentials",
        default=None,
        help="Path to portal Device Connect credentials JSON (alias for --nats-credentials-file).",
    )
    parser.add_argument(
        "--portal-credentials-glob",
        default=None,
        help="Glob used to auto-discover portal credentials under --portal-credentials-dir.",
    )
    parser.add_argument(
        "--portal-credentials-dir",
        default=None,
        help="Directory searched for portal credentials (default: ~/Downloads).",
    )
    parser.add_argument(
        "--transport-mode",
        default=None,
        choices=["auto", "websocket", "zenoh", "http"],
        help="Reachy real-time transport: auto (default), websocket, zenoh, or http.",
    )
    parser.add_argument("--allow-insecure", action="store_true")
    parser.add_argument("--mhp-rig", default=None)
    parser.add_argument("--no-media", action="store_true")
    return parser


async def _run_cli(args: argparse.Namespace) -> None:
    params = gather_cli_run_params(args)
    await run_device_connect(params, stop_event=None)


def main() -> None:
    parser = build_parser()
    asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
