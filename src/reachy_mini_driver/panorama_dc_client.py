"""Panorama capture through Device Connect (invoke_device), not direct daemon I/O."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

InvokeFn = Callable[[str, str, dict[str, Any] | None, str | None], dict[str, Any]]


def unwrap_invoke_response(response: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``invoke_device`` / MCP tool replies into the driver RPC body."""
    if response.get("success") is False or response.get("error"):
        reason = response.get("error") or response.get("reason") or "invoke failed"
        return {"status": "error", "reason": str(reason)}
    if "result" in response:
        result = response["result"]
        if isinstance(result, dict):
            return result
        return {"status": "success", "result": result}
    if response.get("status") in {"error", "accepted", "success", "unsupported"}:
        return response
    return response


@dataclass
class PanoramaDeviceConnectClient:
    """Device Connect client that implements the panorama driver protocol via RPC."""

    device_id: str
    owner: str = "panorama"
    llm_reasoning: str = "Reachy panorama scan via Device Connect"
    max_edge: int | None = None
    quality: int | None = None
    encoding: str = "jpeg"
    _invoke: InvokeFn | None = None

    def _call(self, function: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._invoke is None:
            from device_connect_agent_tools import invoke_device

            invoke = invoke_device
        else:
            invoke = self._invoke
        raw = invoke(
            self.device_id,
            function,
            params,
            f"{self.llm_reasoning}: {function}",
        )
        return unwrap_invoke_response(raw)

    async def set_body_yaw(
        self,
        yaw_deg: float = 0.0,
        duration_s: float = 0.0,
        owner: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._call,
            "set_body_yaw",
            {
                "yaw_deg": yaw_deg,
                "duration_s": duration_s,
                "owner": owner or self.owner,
            },
        )

    async def look_at_world(
        self,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        z_mm: float = 0.0,
        owner: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._call,
            "look_at_world",
            {
                "pitch": pitch,
                "roll": roll,
                "yaw": yaw,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "z_mm": z_mm,
                "owner": owner or self.owner,
            },
        )

    async def capture_video_frame(
        self,
        encoding: str | None = None,
        max_edge: int | None = None,
        quality: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "encoding": encoding or self.encoding,
        }
        edge = max_edge if max_edge is not None else self.max_edge
        qual = quality if quality is not None else self.quality
        if edge is not None:
            params["max_edge"] = edge
        if qual is not None:
            params["quality"] = qual
        return await asyncio.to_thread(self._call, "capture_video_frame", params)


def load_portal_credentials_metadata(path: str | Path) -> dict[str, Any]:
    """Read device_id and tenant from a portal credentials JSON file."""
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
    """Return (tenant zone, device_id, messaging_urls) for portal / NATS clients."""
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
        for name in ("MESSAGING_URLS", "NATS_URL", "NATS_URLS", "ZENOH_CONNECT", "MESSAGING_BACKEND")
    ):
        return
    raise SystemExit(
        "Device Connect messaging is not configured. Set MESSAGING_URLS (or NATS_URL), "
        "or pass --credentials-file with portal metadata."
    )


def _ensure_nats_backend(urls: tuple[str, ...]) -> None:
    if os.environ.get("MESSAGING_BACKEND"):
        return
    if urls and all(url.startswith("nats://") or url.startswith("tls://") for url in urls):
        os.environ.setdefault("MESSAGING_BACKEND", "nats")
    if urls and not os.environ.get("MESSAGING_URLS"):
        os.environ["MESSAGING_URLS"] = ",".join(urls)


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


def connect_mesh(
    *,
    credentials_file: str | None = None,
    tenant: str | None = None,
) -> str:
    """Connect to the Device Connect mesh; returns the tenant zone used."""
    from device_connect_agent_tools import connect

    zone, _, urls = resolve_mesh_settings(credentials_file=credentials_file, tenant=tenant)
    require_messaging_env(urls)
    _ensure_nats_backend(urls)
    connect(zone=zone)
    return zone


def disconnect_mesh() -> None:
    from device_connect_agent_tools import disconnect

    disconnect()
