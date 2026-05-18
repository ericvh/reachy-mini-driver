"""In-process driver state for Reachy Mini."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any


@dataclass
class ReachyState:
    """Physical-state surface exposed by the driver."""

    command_owner: str | None = None
    lease_expires_at: float = 0.0
    execution_state: str = "idle"
    interlock_state: str = "safe"
    current_pose: dict[str, Any] = field(default_factory=dict)
    target_pose: dict[str, Any] = field(default_factory=dict)
    media_ready: bool = False
    video_input_state: str = "unknown"
    audio_input_state: str = "unknown"
    audio_output_state: str = "unknown"
    audio_activity: bool = False
    video_motion_state: str = "unknown"
    last_audio_event: dict[str, Any] = field(default_factory=dict)
    last_motion_event: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""

    def lease_active(self) -> bool:
        return self.lease_expires_at > time.time()


class DriverStateStore:
    """Tracks command ownership, leases, media, and motion state for the driver."""

    def __init__(self, rig_name: str = "reachy_mini"):
        self.rig_name = rig_name
        self.state = ReachyState()

    def snapshot(self) -> dict[str, Any]:
        return {
            "rig": self.rig_name,
            "command_owner": self.state.command_owner,
            "lease_expires_at": self.state.lease_expires_at,
            "lease_active": self.state.lease_active(),
            "execution_state": self.state.execution_state,
            "interlock_state": self.state.interlock_state,
            "current_pose": self.state.current_pose,
            "target_pose": self.state.target_pose,
            "media_ready": self.state.media_ready,
            "video_input_state": self.state.video_input_state,
            "audio_input_state": self.state.audio_input_state,
            "audio_output_state": self.state.audio_output_state,
            "audio_activity": self.state.audio_activity,
            "video_motion_state": self.state.video_motion_state,
            "last_audio_event": self.state.last_audio_event,
            "last_motion_event": self.state.last_motion_event,
            "last_error": self.state.last_error,
        }

    def set_target(self, owner: str, target: dict[str, Any], lease_seconds: float = 5.0) -> None:
        self.state.command_owner = owner
        self.state.lease_expires_at = time.time() + lease_seconds
        self.state.target_pose = target
        self.state.execution_state = "commanded"
        self.state.last_error = ""

    def set_current_pose(self, pose: dict[str, Any]) -> None:
        self.state.current_pose = pose

    def set_error(self, message: str) -> None:
        self.state.last_error = message
        self.state.execution_state = "error"

    def set_media_state(
        self,
        *,
        video_input: str | None = None,
        audio_input: str | None = None,
        audio_output: str | None = None,
        ready: bool | None = None,
    ) -> None:
        if video_input is not None:
            self.state.video_input_state = video_input
        if audio_input is not None:
            self.state.audio_input_state = audio_input
        if audio_output is not None:
            self.state.audio_output_state = audio_output
        if ready is not None:
            self.state.media_ready = ready

    def set_audio_event(self, payload: dict[str, Any]) -> None:
        self.state.last_audio_event = payload
        self.state.audio_activity = payload.get("state") == "active"

    def set_motion_event(self, payload: dict[str, Any]) -> None:
        self.state.last_motion_event = payload
        self.state.video_motion_state = payload.get("state", "unknown")

    def assert_motion_allowed(self) -> None:
        if self.state.interlock_state != "safe":
            raise RuntimeError(f"interlock is {self.state.interlock_state}")
