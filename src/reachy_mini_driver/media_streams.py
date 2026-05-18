"""Direct media stream access metadata for Reachy Mini.

Device Connect RPC stays on NATS; full-rate video and audio use the Reachy
daemon side channels (WebRTC, local GStreamer IPC, or released hardware).
"""

from __future__ import annotations

import asyncio
import os
import platform
from typing import Any

WEBRTC_SIGNALING_PORT = 8443
WEBRTC_PRODUCER_NAME = "reachymini"
CAMERA_IPC_SOCKET = "/tmp/reachymini_camera_socket"
CAMERA_IPC_PIPE = r"\\.\pipe\reachymini_camera_pipe"


def is_loopback_host(host: str) -> bool:
    """True when the driver talks to a daemon on the same machine."""
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or normalized.startswith(
        "127."
    )


def local_ipc_camera_available() -> bool:
    """True when the daemon exposes the on-device camera IPC endpoint."""
    if platform.system() == "Windows":
        return os.path.exists(CAMERA_IPC_PIPE)
    return os.path.exists(CAMERA_IPC_SOCKET)


def daemon_api_url(host: str, api_port: int) -> str:
    return f"http://{host}:{api_port}"


def webrtc_signaling_url(host: str, port: int = WEBRTC_SIGNALING_PORT) -> str:
    return f"ws://{host}:{port}"


async def fetch_daemon_media_status(
    api_call,
) -> dict[str, Any]:
    """GET /api/media/status via the transport ``api`` callable."""
    result = await api_call("/api/media/status", "GET")
    if result.get("status") == "error":
        return {"status": "error", **result}
    return {"status": "success", **result}


async def release_daemon_media(api_call) -> dict[str, Any]:
    """POST /api/media/release — hand camera/mic to direct client access."""
    result = await api_call("/api/media/release", "POST")
    if result.get("status") == "error":
        return result
    return {"status": "success", "released": True, "daemon": result}


async def acquire_daemon_media(api_call) -> dict[str, Any]:
    """POST /api/media/acquire — return camera/mic to the daemon pipeline."""
    result = await api_call("/api/media/acquire", "POST")
    if result.get("status") == "error":
        return result
    return {"status": "success", "released": False, "daemon": result}


def _probe_webrtc_producer(host: str, port: int) -> dict[str, Any]:
    try:
        from reachy_mini.media.webrtc_utils import find_producer_peer_id_by_name, get_producer_list
    except ImportError:
        return {
            "reachable": None,
            "reason": "reachy_mini SDK not installed (pip install reachy-mini)",
        }

    try:
        producers = get_producer_list(host, port)
        peer_id = find_producer_peer_id_by_name(host, port, WEBRTC_PRODUCER_NAME)
        return {
            "reachable": True,
            "producer_name": WEBRTC_PRODUCER_NAME,
            "peer_id": peer_id,
            "producer_count": len(producers),
        }
    except Exception as exc:
        return {"reachable": False, "reason": str(exc)}


async def build_stream_access_info(
    *,
    host: str,
    api_port: int,
    api_call,
    probe_signaling: bool = False,
    target: str | None = None,
) -> dict[str, Any]:
    """Return JSON-only hints for connecting to robot video/audio outside NATS."""
    on_robot = is_loopback_host(host)
    ipc_available = local_ipc_camera_available() if on_robot else False
    signaling_host = host if not is_loopback_host(host) else "127.0.0.1"
    if on_robot:
        signaling_host = "127.0.0.1"

    daemon_status = await fetch_daemon_media_status(api_call)
    media_released = bool(daemon_status.get("released"))

    webrtc: dict[str, Any] = {
        "signaling_url": webrtc_signaling_url(signaling_host if on_robot else host),
        "signaling_host": signaling_host if on_robot else host,
        "signaling_port": WEBRTC_SIGNALING_PORT,
        "producer_name": WEBRTC_PRODUCER_NAME,
        "includes": ["video", "audio"],
        "sdk_connect": {
            "package": "reachy_mini",
            "class": "ReachyMini",
            "kwargs": {
                "host": host,
                "media_backend": "webrtc",
            },
        },
        "client_module": "reachy_mini.media.webrtc_client_gstreamer.GstWebRTCClient",
    }
    if probe_signaling:
        probe_host = signaling_host if on_robot else host
        webrtc["probe"] = await asyncio.to_thread(
            _probe_webrtc_producer, probe_host, WEBRTC_SIGNALING_PORT
        )

    local: dict[str, Any] = {
        "available": ipc_available,
        "camera_ipc": CAMERA_IPC_SOCKET if platform.system() != "Windows" else CAMERA_IPC_PIPE,
        "includes": ["video", "audio"],
        "sdk_connect": {
            "package": "reachy_mini",
            "class": "ReachyMini",
            "kwargs": {
                "host": host,
                "media_backend": "local",
            },
        },
        "client_module": "reachy_mini.media.camera_gstreamer.GStreamerCamera",
    }
    if not on_robot:
        local["reason"] = (
            "LOCAL IPC is only available to processes running on the robot; "
            "remote clients should use the webrtc backend."
        )

    direct: dict[str, Any] = {
        "description": (
            "Release daemon GStreamer ownership, then open /dev/video* and ALSA "
            "directly on the robot (OpenCV, sounddevice, etc.)."
        ),
        "release_rpc": "release_media_hardware",
        "acquire_rpc": "acquire_media_hardware",
        "daemon_released": media_released,
    }

    if media_released:
        recommended = "direct_hardware"
    elif ipc_available:
        recommended = "local"
    else:
        recommended = "webrtc"

    payload: dict[str, Any] = {
        "status": "success",
        "target": target or host,
        "daemon_api_url": daemon_api_url(host, api_port),
        "recommended_backend": recommended,
        "driver_on_robot": on_robot,
        "daemon_media": daemon_status,
        "backends": {
            "webrtc": webrtc,
            "local": local,
            "direct_hardware": direct,
        },
        "usage_notes": [
            "Bulk video/audio must not use Device Connect NATS RPC; use a backend above.",
            "For one-shot stills over the portal, use capture_video_frame(encoding='jpeg').",
            f"WebRTC signaling must be reachable at {webrtc['signaling_url']} from your client.",
        ],
    }
    if daemon_status.get("status") == "error":
        payload["status"] = "partial"
        payload["warning"] = "daemon media status unavailable"
    return payload
