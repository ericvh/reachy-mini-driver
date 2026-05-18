"""Device Connect driver for Reachy Mini."""

from __future__ import annotations

import base64
import logging
import math
import struct
from datetime import UTC, datetime
from typing import Any

from device_connect_edge.drivers import DeviceDriver, emit, rpc
from device_connect_edge.types import DeviceIdentity, DeviceStatus

from reachy_mini_driver.media import MediaClient, SdkMediaClient
from reachy_mini_driver.driver_state import DriverStateStore
from reachy_mini_driver.transport import (
    ReachyHardwareTransport,
    ReachyTransport,
    rpy_to_pose,
)

logger = logging.getLogger(__name__)


class ReachyMiniDriver(DeviceDriver):
    """Standalone Device Connect driver for Reachy Mini."""

    device_type = "reachy_mini"

    def __init__(
        self,
        *,
        host: str = "reachy-mini.local",
        api_port: int = 8000,
        transport: ReachyTransport | None = None,
        transport_mode: str = "auto",
        prefix: str = "reachy_mini",
        media: MediaClient | None = None,
        driver_state: DriverStateStore | None = None,
    ):
        super().__init__()
        self.host = host
        self.api_port = api_port
        self.transport_mode = transport_mode
        self.prefix = prefix
        self._custom_transport = transport is not None
        self.transport_client = transport or ReachyHardwareTransport(
            host,
            api_port,
            mode=transport_mode,
            prefix=prefix,
        )
        self.media = media or SdkMediaClient(host=host)
        self.driver_state = driver_state or DriverStateStore()
        self._latest_joints: dict[str, Any] | None = None
        self._latest_imu: dict[str, Any] | None = None
        self._previous_video_frame: bytes | None = None
        self._logged_first_joints = False

    @property
    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            device_type=self.device_type,
            manufacturer="Pollen Robotics",
            model="Reachy Mini",
            description="Standalone Reachy Mini Device Connect driver",
        )

    @property
    def status(self) -> DeviceStatus:
        state = "idle"
        if self.driver_state.state.execution_state not in {"idle", "error"}:
            state = "busy"
        if self.driver_state.state.execution_state == "error":
            state = "error"
        return DeviceStatus(ts=datetime.now(UTC), availability=state)

    async def connect(self) -> None:
        logger.info(
            "ReachyMiniDriver connecting (host=%s:%s transport_mode=%s)",
            self.host,
            self.api_port,
            self.transport_mode,
        )
        if isinstance(self.transport_client, ReachyHardwareTransport):
            messaging = self.transport
            if messaging is not None:
                logger.info("Attaching Device Connect messaging to Reachy transport")
                self.transport_client.set_messaging(messaging)
        await self.transport_client.start(
            on_joints=self._record_joints,
            on_imu=self._record_imu,
        )
        logger.info("ReachyMiniDriver connected — realtime state streaming active")

    async def disconnect(self) -> None:
        logger.info("ReachyMiniDriver disconnecting")
        await self.transport_client.stop()
        logger.info("ReachyMiniDriver disconnected")

    def _record_joints(self, payload: dict[str, Any]) -> None:
        if not self._logged_first_joints:
            logger.info("Receiving joint state from Reachy")
            self._logged_first_joints = True
        self._latest_joints = payload
        self.driver_state.set_current_pose({"joints": payload})

    def _record_imu(self, payload: dict[str, Any]) -> None:
        self._latest_imu = payload

    @rpc()
    async def get_status(self) -> dict[str, Any]:
        """Return daemon and driver state for the robot."""
        daemon = await self.transport_client.api("/api/daemon/status")
        return {"daemon": daemon, "driver_state": self.driver_state.snapshot()}

    @rpc()
    async def get_joints(self) -> dict[str, Any]:
        """Return the most recently observed joint state."""
        if self._latest_joints is None:
            return {"status": "error", "reason": "no joint data"}
        head = self._latest_joints.get("head_joint_positions", [])
        antennas = self._latest_joints.get("antennas_joint_positions", [])
        return {
            "status": "success",
            "head_degrees": [math.degrees(value) for value in head],
            "antenna_degrees": [math.degrees(value) for value in antennas],
        }

    @rpc()
    async def get_imu(self) -> dict[str, Any]:
        """Return the most recently observed IMU state."""
        if self._latest_imu is None:
            return {"status": "error", "reason": "no IMU data"}
        return {"status": "success", "imu": self._latest_imu}

    @rpc()
    async def get_media_status(self) -> dict[str, Any]:
        """Return audio and video input/output readiness."""
        try:
            status = self.media.status()
            self.driver_state.set_media_state(
                video_input="available" if status.get("video_input") else "unavailable",
                audio_input="available" if status.get("audio_input") else "unavailable",
                audio_output="available" if status.get("audio_output") else "unavailable",
                ready=status.get("status") == "available",
            )
            return status
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def detect_audio_activity(
        self,
        threshold: float = 0.05,
        duration_ms: int = 120,
    ) -> dict[str, Any]:
        """Sample microphone input and emit a low-level audio activity event.

        Args:
            threshold: RMS threshold above which microphone activity is active.
            duration_ms: Evidence window represented by the sample.
        """
        self._assert_range("threshold", threshold, 0.0, 1.0)
        self._assert_range("duration_ms", duration_ms, 1, 10000)
        sample = self.media.get_audio_sample(encoding="float32")
        if sample.get("status") != "success":
            return {"status": sample.get("status", "error"), "sample": sample}
        rms = self._estimate_audio_rms(sample)
        active = rms >= threshold
        payload = {
            "kind": "audio_activity_detected" if active else "audio_activity_ended",
            "source": "microphone",
            "state": "active" if active else "inactive",
            "rms": rms,
            "threshold": threshold,
            "duration_ms": duration_ms,
            "confidence": self._confidence(rms, threshold),
        }
        await self._record_and_emit_audio_event(payload)
        return {"status": "success", "event": payload}

    @rpc()
    async def detect_motion(
        self,
        threshold: float = 0.02,
        source: str = "camera",
    ) -> dict[str, Any]:
        """Sample video input and emit a low-level motion event.

        Args:
            threshold: Changed-byte fraction above which motion is active.
            source: Logical video source name.
        """
        self._assert_range("threshold", threshold, 0.0, 1.0)
        frame = self.media.get_video_frame(encoding="raw")
        if frame.get("status") != "success":
            return {"status": frame.get("status", "error"), "frame": frame}
        raw = base64.b64decode(frame.get("data_b64", ""))
        magnitude = self._frame_delta(raw)
        active = magnitude >= threshold
        payload = {
            "kind": "motion_detected" if active else "motion_ended",
            "source": source,
            "state": "active" if active else "inactive",
            "magnitude": magnitude,
            "threshold": threshold,
            "confidence": self._confidence(magnitude, threshold),
        }
        await self._record_and_emit_motion_event(payload)
        return {"status": "success", "event": payload}

    @rpc()
    async def capture_video_frame(self, encoding: str = "raw") -> dict[str, Any]:
        """Capture one camera frame.

        Args:
            encoding: Requested payload encoding. Current supported value is `raw`.
        """
        if encoding != "raw":
            return {"status": "error", "reason": "only raw frame encoding is implemented"}
        try:
            result = self.media.get_video_frame(encoding=encoding)
            self.driver_state.set_media_state(video_input=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def push_video_frame(
        self,
        data_b64: str,
        width: int,
        height: int,
        channels: int = 3,
        dtype: str = "uint8",
    ) -> dict[str, Any]:
        """Push one video frame to the robot video/display output if available.

        Args:
            data_b64: Base64-encoded raw frame buffer.
            width: Frame width in pixels.
            height: Frame height in pixels.
            channels: Channel count, usually 3 for RGB.
            dtype: Numpy dtype name for the raw frame buffer.
        """
        if width < 1 or width > 4096:
            return {"status": "error", "reason": "width outside supported range [1, 4096]"}
        if height < 1 or height > 4096:
            return {"status": "error", "reason": "height outside supported range [1, 4096]"}
        if channels < 1 or channels > 4:
            return {"status": "error", "reason": "channels outside supported range [1, 4]"}
        try:
            result = self.media.push_video_frame(data_b64, width, height, channels, dtype)
            self.driver_state.set_media_state(video_input=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def start_audio_input(self) -> dict[str, Any]:
        """Start microphone capture."""
        try:
            result = self.media.start_audio_input()
            self.driver_state.set_media_state(audio_input=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def stop_audio_input(self) -> dict[str, Any]:
        """Stop microphone capture."""
        try:
            result = self.media.stop_audio_input()
            self.driver_state.set_media_state(audio_input=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def capture_audio_sample(self, encoding: str = "float32") -> dict[str, Any]:
        """Capture one microphone audio sample.

        Args:
            encoding: Audio sample encoding. Current supported value is `float32`.
        """
        if encoding != "float32":
            return {"status": "error", "reason": "only float32 audio encoding is implemented"}
        try:
            result = self.media.get_audio_sample(encoding=encoding)
            self.driver_state.set_media_state(audio_input=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def start_audio_output(self) -> dict[str, Any]:
        """Start speaker output."""
        try:
            result = self.media.start_audio_output()
            self.driver_state.set_media_state(audio_output=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def stop_audio_output(self) -> dict[str, Any]:
        """Stop speaker output."""
        try:
            result = self.media.stop_audio_output()
            self.driver_state.set_media_state(audio_output=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def play_audio_file(self, sound_file: str) -> dict[str, Any]:
        """Play an audio file through the robot speaker.

        Args:
            sound_file: Path to a local sound file reachable by the driver process.
        """
        try:
            result = self.media.play_audio_file(sound_file)
            self.driver_state.set_media_state(audio_output=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def push_audio_sample(
        self,
        data_b64: str,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: str = "float32",
    ) -> dict[str, Any]:
        """Push audio samples to the robot speaker.

        Args:
            data_b64: Base64-encoded raw audio sample buffer.
            sample_rate: Sample rate in Hz.
            channels: Channel count.
            dtype: Numpy dtype name for the raw sample buffer.
        """
        if sample_rate < 8000 or sample_rate > 48000:
            return {
                "status": "error",
                "reason": "sample_rate outside supported range [8000, 48000]",
            }
        if channels < 1 or channels > 8:
            return {"status": "error", "reason": "channels outside supported range [1, 8]"}
        try:
            result = self.media.push_audio_sample(data_b64, sample_rate, channels, dtype)
            self.driver_state.set_media_state(audio_output=result.get("status", "unknown"))
            return result
        except Exception as exc:
            self.driver_state.set_error(str(exc))
            return {"status": "error", "reason": str(exc)}

    @rpc()
    async def look_at_world(
        self,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        z_mm: float = 0.0,
        owner: str = "agent",
    ) -> dict[str, Any]:
        """Set a bounded head target in world coordinates.

        Args:
            pitch: Pitch angle in degrees, limited to [-30, 30].
            roll: Roll angle in degrees, limited to [-30, 30].
            yaw: Yaw angle in degrees, limited to [-45, 45].
            x_mm: X offset in millimeters, limited to [-50, 50].
            y_mm: Y offset in millimeters, limited to [-50, 50].
            z_mm: Z offset in millimeters, limited to [-50, 50].
            owner: Logical command owner for the motion lease.
        """
        self._assert_range("pitch", pitch, -30, 30)
        self._assert_range("roll", roll, -30, 30)
        self._assert_range("yaw", yaw, -45, 45)
        self._assert_range("x_mm", x_mm, -50, 50)
        self._assert_range("y_mm", y_mm, -50, 50)
        self._assert_range("z_mm", z_mm, -50, 50)
        self.driver_state.assert_motion_allowed()

        pose = rpy_to_pose(pitch, roll, yaw, x_mm, y_mm, z_mm)
        target = {
            "kind": "look_at_world",
            "pitch": pitch,
            "roll": roll,
            "yaw": yaw,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "z_mm": z_mm,
        }
        self.driver_state.set_target(owner, target)
        await self.transport_client.send_command({"head_pose": pose})
        return {"status": "accepted", "target": target}

    @rpc()
    async def antenna_pose(
        self,
        left: float = 0.0,
        right: float = 0.0,
        owner: str = "agent",
    ) -> dict[str, Any]:
        """Set bounded antenna angles.

        Args:
            left: Left antenna angle in degrees, limited to [-80, 80].
            right: Right antenna angle in degrees, limited to [-80, 80].
            owner: Logical command owner for the motion lease.
        """
        self._assert_range("left", left, -80, 80)
        self._assert_range("right", right, -80, 80)
        self.driver_state.assert_motion_allowed()
        target = {"kind": "antenna_pose", "left": left, "right": right}
        self.driver_state.set_target(owner, target)
        await self.transport_client.send_command(
            {"antennas_joint_positions": [math.radians(left), math.radians(right)]}
        )
        return {"status": "accepted", "target": target}

    @rpc()
    async def goto_sleep(self, owner: str = "agent") -> dict[str, Any]:
        """Put the robot into its sleep posture."""
        self.driver_state.assert_motion_allowed()
        self.driver_state.set_target(owner, {"kind": "goto_sleep"})
        result = await self.transport_client.api("/api/move/play/goto_sleep", "POST")
        return {"status": "accepted", "result": result}

    @rpc()
    async def wake_up(self, owner: str = "agent") -> dict[str, Any]:
        """Wake the robot from its sleep posture."""
        self.driver_state.assert_motion_allowed()
        self.driver_state.set_target(owner, {"kind": "wake_up"})
        result = await self.transport_client.api("/api/move/play/wake_up", "POST")
        return {"status": "accepted", "result": result}

    @rpc()
    async def stop_motion(self, owner: str = "agent") -> dict[str, Any]:
        """Stop current robot motion."""
        self.driver_state.set_target(owner, {"kind": "stop_motion"})
        result = await self.transport_client.api("/api/move/stop", "POST")
        return {"status": "accepted", "result": result}

    @emit()
    async def audio_event(
        self,
        kind: str,
        source: str,
        state: str,
        rms: float,
        threshold: float,
        duration_ms: int,
        confidence: float,
    ):
        """Emitted when low-level microphone activity changes."""
        pass

    @emit()
    async def motion_event(
        self,
        kind: str,
        source: str,
        state: str,
        magnitude: float,
        threshold: float,
        confidence: float,
    ):
        """Emitted when low-level video motion activity changes."""
        pass

    @emit()
    async def safety_event(self, reason: str):
        """Emitted when the driver rejects or stops motion."""
        pass

    @staticmethod
    def _assert_range(name: str, value: float, minimum: float, maximum: float) -> None:
        if value < minimum or value > maximum:
            raise ValueError(f"{name}={value} outside safe range [{minimum}, {maximum}]")

    async def _record_and_emit_audio_event(self, payload: dict[str, Any]) -> None:
        self.driver_state.set_audio_event(payload)
        try:
            await self.audio_event(**payload)
        except RuntimeError as exc:
            if "Driver not associated with a DeviceRuntime" not in str(exc):
                raise

    async def _record_and_emit_motion_event(self, payload: dict[str, Any]) -> None:
        self.driver_state.set_motion_event(payload)
        try:
            await self.motion_event(**payload)
        except RuntimeError as exc:
            if "Driver not associated with a DeviceRuntime" not in str(exc):
                raise

    def _estimate_audio_rms(self, sample: dict[str, Any]) -> float:
        if sample.get("dtype") != "float32":
            return 0.0
        raw = base64.b64decode(sample.get("data_b64", ""))
        count = len(raw) // 4
        if count == 0:
            return 0.0
        values = struct.unpack(f"<{count}f", raw[: count * 4])
        mean_square = sum(value * value for value in values) / count
        return round(math.sqrt(mean_square), 6)

    def _frame_delta(self, raw: bytes) -> float:
        if not raw:
            return 0.0
        previous = self._previous_video_frame
        self._previous_video_frame = raw
        if previous is None or len(previous) != len(raw):
            return 0.0
        changed = sum(1 for old, new in zip(previous, raw, strict=True) if old != new)
        return round(changed / len(raw), 6)

    @staticmethod
    def _confidence(value: float, threshold: float) -> float:
        if threshold == 0:
            return 1.0 if value > 0 else 0.0
        return round(min(1.0, value / threshold), 6)
