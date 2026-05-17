"""Reachy Mini hardware transport boundary."""

from __future__ import annotations

import asyncio
import json
import math
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from device_connect_edge.drivers.transport import DriverTransport


def rpy_to_pose(
    pitch_deg: float,
    roll_deg: float,
    yaw_deg: float,
    x_mm: float = 0,
    y_mm: float = 0,
    z_mm: float = 0,
) -> list[list[float]]:
    """Convert RPY degrees plus XYZ millimeters to a 4x4 pose matrix."""
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)
    yaw = math.radians(yaw_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, x_mm / 1000],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, y_mm / 1000],
        [-sp, cp * sr, cp * cr, z_mm / 1000],
        [0, 0, 0, 1],
    ]


class ReachyTransport(Protocol):
    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        """Start receiving robot state."""

    async def stop(self) -> None:
        """Stop receiving robot state."""

    async def send_command(self, command: dict[str, Any]) -> None:
        """Send a real-time robot command."""

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        """Call the Reachy daemon API."""


class HttpReachyTransport:
    """HTTP Reachy transport for daemon and move REST API calls."""

    def __init__(self, host: str, api_port: int):
        self.host = host
        self.api_port = api_port

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_command(self, command: dict[str, Any]) -> None:
        raise NotImplementedError(f"real-time command not implemented: {command!r}")

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        return await asyncio.to_thread(self._api_sync, path, method, data)

    def _api_sync(self, path: str, method: str, data: dict[str, Any] | None) -> dict:
        url = f"http://{self.host}:{self.api_port}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode() if data is not None else None
        try:
            with urllib.request.urlopen(req, body, timeout=10) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            return {"status": "error", "code": exc.code, "reason": exc.read().decode()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


_WS_CMD_MAP = {
    "head_pose": lambda command: {
        "type": "set_target",
        "head": [value for row in command["head_pose"] for value in row],
    },
    "antennas_joint_positions": lambda command: {
        "type": "set_antennas",
        "antennas": command["antennas_joint_positions"],
    },
    "body_yaw": lambda command: {
        "type": "set_body_yaw",
        "body_yaw": command["body_yaw"],
    },
    "torque": lambda command: {
        "type": "set_torque",
        "on": command["torque"],
        "ids": command.get("ids"),
    },
}


class WebSocketReachyTransport:
    """Lite Reachy transport using the daemon WebSocket SDK stream."""

    def __init__(self, host: str, api_port: int):
        self.host = host
        self.api_port = api_port
        self._ws = None
        self._read_task: asyncio.Task[None] | None = None

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "WebSocket transport requires the websockets package. "
                "Install with: pip install 'reachy-mini-driver[websocket]'"
            ) from exc

        self._ws = await websockets.connect(f"ws://{self.host}:{self.api_port}/ws/sdk")
        self._read_task = asyncio.create_task(self._read_loop(on_joints, on_imu))

    async def _read_loop(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message_type = message.get("type")
            if message_type == "joint_positions":
                on_joints(message)
            elif message_type == "imu_data":
                on_imu(message)

    async def stop(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_command(self, command: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket transport is not connected")
        for key, builder in _WS_CMD_MAP.items():
            if key in command:
                await self._ws.send(json.dumps(builder(command)))
                return
        raise NotImplementedError(f"unsupported WebSocket command: {command!r}")

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        http = HttpReachyTransport(self.host, self.api_port)
        return await http.api(path, method, data)


class ZenohReachyTransport:
    """Wireless Reachy transport using Device Connect messaging topics."""

    def __init__(self, messaging: DriverTransport, prefix: str):
        self._messaging = messaging
        self._prefix = prefix

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        async def _on_joints(data: bytes, _reply: str | None = None) -> None:
            try:
                on_joints(json.loads(data.decode()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return

        async def _on_imu(data: bytes, _reply: str | None = None) -> None:
            try:
                on_imu(json.loads(data.decode()))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return

        await self._messaging.subscribe(f"{self._prefix}/joint_positions", _on_joints)
        await self._messaging.subscribe(f"{self._prefix}/imu_data", _on_imu)

    async def stop(self) -> None:
        return None

    async def send_command(self, command: dict[str, Any]) -> None:
        await self._messaging.publish(
            f"{self._prefix}/command",
            json.dumps(command).encode(),
        )

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        raise NotImplementedError("use ReachyHardwareTransport.api() for REST calls")


class ReachyHardwareTransport:
    """Reachy transport with REST API calls and auto-selected real-time I/O."""

    def __init__(
        self,
        host: str,
        api_port: int,
        *,
        mode: str = "auto",
        prefix: str = "reachy_mini",
    ):
        if mode not in {"auto", "websocket", "zenoh", "http"}:
            raise ValueError("mode must be one of: auto, websocket, zenoh, http")
        self.host = host
        self.api_port = api_port
        self.mode = mode
        self.prefix = prefix
        self._http = HttpReachyTransport(host, api_port)
        self._messaging: DriverTransport | None = None
        self._realtime: ReachyTransport | None = None

    def set_messaging(self, messaging: DriverTransport) -> None:
        self._messaging = messaging
        self._realtime = None

    async def _ensure_realtime(self) -> ReachyTransport:
        if self._realtime is not None:
            return self._realtime
        if self.mode == "http":
            self._realtime = self._http
            return self._realtime

        use_websocket = self.mode == "websocket"
        if self.mode == "auto":
            status = await self._http.api("/api/daemon/status")
            if status.get("status") == "error" or "wireless_version" not in status:
                # Daemon not reachable or status incomplete: prefer WebSocket when the
                # host is a directly reachable Lite daemon.
                use_websocket = True
            else:
                use_websocket = not status.get("wireless_version")

        if use_websocket:
            self._realtime = WebSocketReachyTransport(self.host, self.api_port)
        else:
            if self._messaging is None:
                raise RuntimeError(
                    "Zenoh transport requires an active Device Connect messaging session"
                )
            self._realtime = ZenohReachyTransport(self._messaging, self.prefix)
        return self._realtime

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        realtime = await self._ensure_realtime()
        await realtime.start(on_joints, on_imu)

    async def stop(self) -> None:
        if self._realtime is not None:
            await self._realtime.stop()

    async def send_command(self, command: dict[str, Any]) -> None:
        realtime = await self._ensure_realtime()
        await realtime.send_command(command)

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        return await self._http.api(path, method, data)


class NullReachyTransport:
    """Hardware-free transport for tests and dry runs."""

    def __init__(self):
        self.commands: list[dict[str, Any]] = []
        self.api_calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        on_joints(
            {
                "head_joint_positions": [0.0, 0.0, 0.0],
                "antennas_joint_positions": [0.0, 0.0],
            }
        )

    async def stop(self) -> None:
        return None

    async def send_command(self, command: dict[str, Any]) -> None:
        self.commands.append(command)

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        self.api_calls.append((path, method, data))
        if path == "/api/daemon/status":
            return {"state": "running", "wireless_version": False}
        return {"status": "success", "path": path, "method": method}


class SimReachyTransport:
    """Stateful hardware-free target that behaves like a small Reachy daemon."""

    def __init__(self):
        self.commands: list[dict[str, Any]] = []
        self.api_calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.head_pose = rpy_to_pose(0, 0, 0)
        self.antenna_positions = [0.0, 0.0]
        self.sleeping = False
        self.motion_stopped = False
        self._on_joints: Callable[[dict[str, Any]], None] | None = None
        self._on_imu: Callable[[dict[str, Any]], None] | None = None

    async def start(
        self,
        on_joints: Callable[[dict[str, Any]], None],
        on_imu: Callable[[dict[str, Any]], None],
    ) -> None:
        self._on_joints = on_joints
        self._on_imu = on_imu
        self._publish_state()

    async def stop(self) -> None:
        self._on_joints = None
        self._on_imu = None

    async def send_command(self, command: dict[str, Any]) -> None:
        self.commands.append(command)
        self.motion_stopped = False
        if "head_pose" in command:
            self.head_pose = command["head_pose"]
        if "antennas_joint_positions" in command:
            self.antenna_positions = list(command["antennas_joint_positions"])
        self._publish_state()

    async def api(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict:
        self.api_calls.append((path, method, data))
        if path == "/api/daemon/status":
            return {
                "state": "running",
                "target": "simulated",
                "wireless_version": True,
                "sleeping": self.sleeping,
                "motion_stopped": self.motion_stopped,
            }
        if path == "/api/move/play/goto_sleep" and method == "POST":
            self.sleeping = True
            self.motion_stopped = False
            self._publish_state()
            return {"status": "success", "action": "goto_sleep", "target": "simulated"}
        if path == "/api/move/play/wake_up" and method == "POST":
            self.sleeping = False
            self.motion_stopped = False
            self._publish_state()
            return {"status": "success", "action": "wake_up", "target": "simulated"}
        if path == "/api/move/stop" and method == "POST":
            self.motion_stopped = True
            return {"status": "success", "action": "stop", "target": "simulated"}
        return {"status": "success", "path": path, "method": method, "target": "simulated"}

    def _publish_state(self) -> None:
        if self._on_joints is not None:
            self._on_joints(
                {
                    "head_joint_positions": [0.0, 0.0, 0.0],
                    "antennas_joint_positions": self.antenna_positions,
                    "simulated": True,
                }
            )
        if self._on_imu is not None:
            self._on_imu(
                {
                    "orientation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                    "acceleration": {"x": 0.0, "y": 0.0, "z": 9.81},
                    "simulated": True,
                }
            )
