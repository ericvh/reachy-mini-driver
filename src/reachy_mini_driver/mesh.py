"""Device Connect mesh helpers for agents, scripts, and MCP clients.

Always use :func:`connect_mesh` instead of calling ``device_connect_agent_tools.connect()``
directly when using a portal ``.creds.json`` file. The bundled helpers ensure broker URLs,
tenant zone, and NATS backend match the credentials file.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

__all__ = [
    "connect_mesh",
    "disconnect_mesh",
    "load_portal_credentials_metadata",
    "require_messaging_env",
    "resolve_mesh_settings",
    "wait_for_device",
]


def load_portal_credentials_metadata(path: str | Path) -> dict[str, Any]:
    """Read device_id, tenant, and broker URLs from a portal credentials JSON file."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in credentials file: {path}")
    urls = tuple(data.get("nats", {}).get("urls", []) or ())
    return {
        "device_id": data.get("device_id"),
        "tenant": data.get("tenant"),
        "messaging_urls": urls,
    }


def resolve_mesh_settings(
    *,
    credentials_file: str | None = None,
    tenant: str | None = None,
    device_id: str | None = None,
) -> tuple[str, str | None, tuple[str, ...]]:
    """Return ``(tenant zone, device_id, messaging_urls)`` for portal / NATS clients."""
    creds_path = (
        credentials_file
        or os.environ.get("NATS_CREDENTIALS_FILE")
        or os.environ.get("PORTAL_CREDENTIALS_FILE")
    )
    meta: dict[str, Any] = {}
    if creds_path and Path(creds_path).expanduser().is_file():
        meta = load_portal_credentials_metadata(creds_path)

    zone = tenant or os.environ.get("TENANT") or meta.get("tenant") or "default"
    resolved_device_id = device_id or meta.get("device_id")
    urls = tuple(
        url.strip()
        for url in (
            os.environ.get("MESSAGING_URLS", "")
            or os.environ.get("NATS_URLS", "")
            or os.environ.get("NATS_URL", "")
            or ",".join(meta.get("messaging_urls", ()))
        ).split(",")
        if url.strip()
    )
    return zone, resolved_device_id, urls


def require_messaging_env(urls: tuple[str, ...] = ()) -> None:
    if urls or any(
        os.getenv(name)
        for name in (
            "MESSAGING_URLS",
            "NATS_URL",
            "NATS_URLS",
            "ZENOH_CONNECT",
            "MESSAGING_BACKEND",
            "NATS_CREDENTIALS_FILE",
            "PORTAL_CREDENTIALS_FILE",
        )
    ):
        return
    raise SystemExit(
        "Device Connect messaging is not configured. Set NATS_CREDENTIALS_FILE to a "
        "portal .creds.json, or set MESSAGING_URLS / NATS_URL."
    )


def connect_mesh(
    *,
    credentials_file: str | None = None,
    tenant: str | None = None,
) -> str:
    """Connect to the Device Connect mesh; returns the tenant zone used."""
    from device_connect_agent_tools import connect

    zone, _, urls = resolve_mesh_settings(credentials_file=credentials_file, tenant=tenant)
    require_messaging_env(urls)
    connect(zone=zone, servers=list(urls) if urls else None)
    return zone


def disconnect_mesh() -> None:
    from device_connect_agent_tools import disconnect

    disconnect()


def wait_for_device(
    device_id: str,
    *,
    timeout_s: float = 60.0,
    device_type: str = "reachy_mini",
) -> dict[str, Any]:
    from device_connect_agent_tools import list_devices

    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"devices": []}
    while time.monotonic() < deadline:
        last = list_devices(device_type=device_type)
        for device in last.get("devices", []):
            if device.get("device_id") == device_id:
                return device
        time.sleep(0.5)
    raise TimeoutError(
        f"device {device_id!r} not found on the mesh within {timeout_s}s; last roster: {last}"
    )
