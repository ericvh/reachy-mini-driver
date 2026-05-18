"""Panorama capture through Device Connect (invoke_device), not direct daemon I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from reachy_mini_driver.mesh import (
    connect_mesh,
    disconnect_mesh,
    load_portal_credentials_metadata,
    require_messaging_env,
    resolve_mesh_settings,
    wait_for_device,
)

__all__ = [
    "PanoramaDeviceConnectClient",
    "connect_mesh",
    "disconnect_mesh",
    "load_portal_credentials_metadata",
    "require_messaging_env",
    "resolve_mesh_settings",
    "unwrap_invoke_response",
    "wait_for_device",
]

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

    async def get_body_yaw(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._call, "get_body_yaw", None)

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


