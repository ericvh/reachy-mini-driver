"""End-to-end stdio MCP smoke test for the simulated Reachy Mini.

Requires:
  - A reachable Device Connect messaging backend.
  - The MCP bridge optional dependency: device-connect-agent-tools[mcp].

Example:
    MESSAGING_BACKEND=nats MESSAGING_URLS=nats://127.0.0.1:4222 \
    PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:\
/path/to/device-connect/packages/device-connect-agent-tools \
    python3 tests/smoke_stdio_mcp_bridge.py

The script starts the simulated Reachy Mini as a DeviceRuntime, launches
`python -m device_connect_agent_tools.mcp` over stdio, then calls the MCP tools
`list_devices`, `get_device_functions`, and `invoke_device`.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
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
    parser.add_argument("--device-id", default="reachy-mini-sim-stdio-mcp")
    parser.add_argument("--tenant", default=os.getenv("TENANT", "default"))
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    _require_broker_env()
    _require_fastmcp()

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
    await asyncio.wait_for(registered.wait(), timeout=args.timeout)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "device_connect_agent_tools.mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    client = StdioMcpClient(proc)
    try:
        await client.initialize()
        tools = await client.call("tools/list")
        tool_names = {tool.get("name") for tool in tools["tools"]}
        required_tools = {
            "describe_fleet",
            "list_devices",
            "get_device_functions",
            "invoke_device",
        }
        missing_tools = sorted(required_tools - tool_names)
        if missing_tools:
            raise AssertionError({"missing_tools": missing_tools, "tools": tools})

        devices = await _wait_for_device(client, args.device_id, args.timeout)
        functions = await _call_tool(client, "get_device_functions", {"device_id": args.device_id})
        function_names = {item.get("name") for item in functions.get("functions", [])}
        required_functions = {"get_status", "goto_sleep", "wake_up", "detect_audio_activity"}
        missing_functions = sorted(required_functions - function_names)
        if missing_functions:
            raise AssertionError(
                {"missing_functions": missing_functions, "functions": functions}
            )

        status = await _call_tool(
            client,
            "invoke_device",
            {
                "device_id": args.device_id,
                "function": "get_status",
                "llm_reasoning": "stdio MCP simulated Reachy smoke test",
            },
        )
        sleep = await _call_tool(
            client,
            "invoke_device",
            {
                "device_id": args.device_id,
                "function": "goto_sleep",
                "params": {"owner": "smoke"},
                "llm_reasoning": "verify sleep over stdio MCP",
            },
        )
        wake = await _call_tool(
            client,
            "invoke_device",
            {
                "device_id": args.device_id,
                "function": "wake_up",
                "params": {"owner": "smoke"},
                "llm_reasoning": "verify wake over stdio MCP",
            },
        )

        for label, response in {"get_status": status, "goto_sleep": sleep, "wake_up": wake}.items():
            if not response.get("success"):
                raise AssertionError({label: response})

        print(
            json.dumps(
                {
                    "status": "ok",
                    "device_id": args.device_id,
                    "tools": sorted(tool_names),
                    "device": _compact_device(devices, args.device_id),
                    "functions_checked": sorted(required_functions),
                    "invocations": {
                        "get_status": status,
                        "goto_sleep": sleep,
                        "wake_up": wake,
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        proc.terminate()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await runtime.stop()
        runtime_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime_task


class StdioMcpClient:
    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self._next_id = 1

    async def initialize(self) -> None:
        await self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "reachy-mini-driver-stdio-smoke",
                    "version": "0.1.0",
                },
            },
        )
        await self.notify("notifications/initialized", {})

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            response = await self._read()
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise AssertionError(response["error"])
            return response.get("result", {})

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _write(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + body)
        await self.proc.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                stderr = await self.proc.stderr.read() if self.proc.stderr else b""
                raise RuntimeError(
                    "MCP bridge exited before a response was received: "
                    f"{stderr.decode(errors='replace')}"
                )
            if line in (b"\r\n", b"\n"):
                break
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        length = int(headers["content-length"])
        body = await self.proc.stdout.readexactly(length)
        return json.loads(body.decode())


async def _wait_for_device(
    client: StdioMcpClient,
    device_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    devices: dict[str, Any] = {"devices": []}
    while time.monotonic() < deadline:
        devices = await _call_tool(client, "list_devices", {"device_type": "reachy_mini"})
        if _compact_device(devices, device_id) is not None:
            return devices
        await asyncio.sleep(0.5)
    raise AssertionError(
        {
            "error": "simulated device did not appear before timeout",
            "last_list_devices": devices,
        }
    )


async def _call_tool(
    client: StdioMcpClient,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await client.call("tools/call", {"name": name, "arguments": arguments})
    if result.get("isError"):
        raise AssertionError(result)
    content = result.get("content", [])
    if not content:
        return {}
    text = content[0].get("text", "{}")
    return json.loads(text)


def _require_broker_env() -> None:
    if any(
        os.getenv(name)
        for name in ("MESSAGING_URLS", "NATS_URL", "NATS_URLS", "ZENOH_CONNECT")
    ):
        return
    raise SystemExit(
        "Set MESSAGING_URLS, NATS_URL, NATS_URLS, or ZENOH_CONNECT to run the "
        "stdio MCP smoke test."
    )


def _require_fastmcp() -> None:
    if importlib.util.find_spec("fastmcp") is not None:
        return
    raise SystemExit(
        "Install the MCP bridge optional dependency first: "
        "pip install 'device-connect-agent-tools[mcp]'"
    )


def _messaging_urls() -> list[str] | None:
    raw = os.getenv("MESSAGING_URLS") or os.getenv("NATS_URLS") or os.getenv("NATS_URL")
    if not raw:
        return None
    return [url.strip() for url in raw.split(",") if url.strip()]


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
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
