"""Broker-backed smoke test for the simulated Reachy Mini agent-tool path.

Requires a reachable Device Connect messaging backend, for example:

    MESSAGING_BACKEND=nats MESSAGING_URLS=nats://127.0.0.1:4222 \
    PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:\
/path/to/device-connect/packages/device-connect-agent-tools \
    python3 tests/smoke_broker_mcp_tools.py

The script starts the simulated Reachy Mini driver as a DeviceRuntime, then uses
the hierarchical agent-tool functions exposed by the MCP bridge:
describe_fleet, list_devices, get_device_functions, and invoke_device.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from typing import Any

from device_connect_edge import DeviceRuntime

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.transport import SimReachyTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", default="reachy-mini-sim-mcp-smoke")
    parser.add_argument("--tenant", default=os.getenv("TENANT", "default"))
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    _require_broker_env()

    driver = ReachyMiniDriver(
        transport=SimReachyTransport(),
        media=SimMediaClient(),
    )
    runtime = DeviceRuntime(
        driver=driver,
        device_id=args.device_id,
        tenant=args.tenant,
        messaging_backend=os.getenv("MESSAGING_BACKEND") or None,
        messaging_urls=_messaging_urls(),
        nats_credentials_file=os.getenv("NATS_CREDENTIALS_FILE") or None,
        allow_insecure=True,
    )
    registered = asyncio.Event()

    async def mark_registered() -> None:
        registered.set()

    runtime.add_registration_listener(mark_registered)

    runtime_task = asyncio.create_task(runtime.run())
    try:
        await asyncio.wait_for(registered.wait(), timeout=args.timeout)
        result = await asyncio.to_thread(_exercise_agent_tools, args.device_id, args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        with suppress(Exception):
            from device_connect_agent_tools import disconnect

            disconnect()
        await runtime.stop()
        runtime_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime_task


def _require_broker_env() -> None:
    if any(
        os.getenv(name)
        for name in ("MESSAGING_URLS", "NATS_URL", "NATS_URLS", "ZENOH_CONNECT")
    ):
        return
    raise SystemExit(
        "Set MESSAGING_URLS, NATS_URL, NATS_URLS, or ZENOH_CONNECT to run the "
        "broker-backed smoke test."
    )


def _messaging_urls() -> list[str] | None:
    raw = os.getenv("MESSAGING_URLS") or os.getenv("NATS_URLS") or os.getenv("NATS_URL")
    if not raw:
        return None
    return [url.strip() for url in raw.split(",") if url.strip()]


def _exercise_agent_tools(device_id: str, timeout: float) -> dict[str, Any]:
    from device_connect_agent_tools import (
        connect,
        describe_fleet,
        disconnect,
        get_device_functions,
        invoke_device,
        list_devices,
    )

    disconnect()
    connect()
    try:
        deadline = time.monotonic() + timeout
        devices: dict[str, Any] = {"devices": []}
        while time.monotonic() < deadline:
            devices = list_devices(device_type="reachy_mini")
            if _contains_device(devices, device_id):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                {
                    "error": "simulated device did not appear before timeout",
                    "last_list_devices": devices,
                }
            )

        fleet = describe_fleet()
        functions = get_device_functions(device_id)
        names = {item.get("name") for item in functions.get("functions", [])}
        required = {"get_status", "goto_sleep", "wake_up", "detect_audio_activity"}
        missing = sorted(required - names)
        if missing:
            raise AssertionError({"missing_functions": missing, "functions": functions})

        status = invoke_device(
            device_id=device_id,
            function="get_status",
            llm_reasoning="broker-backed simulated Reachy smoke test",
        )
        sleep = invoke_device(
            device_id=device_id,
            function="goto_sleep",
            params={"owner": "smoke"},
            llm_reasoning="verify sleep command routing",
        )
        wake = invoke_device(
            device_id=device_id,
            function="wake_up",
            params={"owner": "smoke"},
            llm_reasoning="verify wake command routing",
        )
        audio = invoke_device(
            device_id=device_id,
            function="detect_audio_activity",
            params={"threshold": 0.05},
            llm_reasoning="verify low-level audio event routing",
        )

        for label, response in {
            "get_status": status,
            "goto_sleep": sleep,
            "wake_up": wake,
            "detect_audio_activity": audio,
        }.items():
            if not response.get("success"):
                raise AssertionError({label: response})

        return {
            "status": "ok",
            "device_id": device_id,
            "fleet": fleet,
            "device": _compact_device(devices, device_id),
            "functions_checked": sorted(required),
            "invocations": {
                "get_status": status,
                "goto_sleep": sleep,
                "wake_up": wake,
                "detect_audio_activity": audio,
            },
        }
    finally:
        disconnect()


def _contains_device(devices: dict[str, Any], device_id: str) -> bool:
    return _compact_device(devices, device_id) is not None


def _compact_device(devices: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    for device in devices.get("devices", []):
        if device.get("device_id") == device_id:
            return device
    return None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
