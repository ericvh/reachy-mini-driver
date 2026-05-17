"""Reachy Mini media boundary for audio and video I/O."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import struct
from typing import Any, Protocol


class MediaClient(Protocol):
    def status(self) -> dict[str, Any]:
        """Return media capability and readiness state."""

    def get_video_frame(self, encoding: str = "raw") -> dict[str, Any]:
        """Return one camera frame."""

    def push_video_frame(
        self,
        data_b64: str,
        width: int,
        height: int,
        channels: int = 3,
        dtype: str = "uint8",
    ) -> dict[str, Any]:
        """Push one frame to a robot video/display output if available."""

    def start_audio_input(self) -> dict[str, Any]:
        """Start microphone capture."""

    def stop_audio_input(self) -> dict[str, Any]:
        """Stop microphone capture."""

    def get_audio_sample(self, encoding: str = "float32") -> dict[str, Any]:
        """Return one microphone audio sample."""

    def start_audio_output(self) -> dict[str, Any]:
        """Start speaker playback."""

    def stop_audio_output(self) -> dict[str, Any]:
        """Stop speaker playback."""

    def play_audio_file(self, sound_file: str) -> dict[str, Any]:
        """Play a sound file through the robot speaker."""

    def push_audio_sample(
        self,
        data_b64: str,
        sample_rate: int,
        channels: int,
        dtype: str = "float32",
    ) -> dict[str, Any]:
        """Push audio samples to the robot speaker."""


def _unsupported(reason: str) -> dict[str, Any]:
    return {"status": "unsupported", "reason": reason}


@dataclass
class NullMediaClient:
    """Media client used when the SDK media backend is unavailable."""

    reason: str = "media backend is not configured"

    def status(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "video_input": False,
            "audio_input": False,
            "audio_output": False,
            "reason": self.reason,
        }

    def get_video_frame(self, encoding: str = "raw") -> dict[str, Any]:
        return _unsupported(self.reason)

    def push_video_frame(
        self,
        data_b64: str,
        width: int,
        height: int,
        channels: int = 3,
        dtype: str = "uint8",
    ) -> dict[str, Any]:
        return _unsupported(self.reason)

    def start_audio_input(self) -> dict[str, Any]:
        return _unsupported(self.reason)

    def stop_audio_input(self) -> dict[str, Any]:
        return _unsupported(self.reason)

    def get_audio_sample(self, encoding: str = "float32") -> dict[str, Any]:
        return _unsupported(self.reason)

    def start_audio_output(self) -> dict[str, Any]:
        return _unsupported(self.reason)

    def stop_audio_output(self) -> dict[str, Any]:
        return _unsupported(self.reason)

    def play_audio_file(self, sound_file: str) -> dict[str, Any]:
        return _unsupported(self.reason)

    def push_audio_sample(
        self,
        data_b64: str,
        sample_rate: int,
        channels: int,
        dtype: str = "float32",
    ) -> dict[str, Any]:
        return _unsupported(self.reason)


class SimMediaClient:
    """Deterministic media target for driver bring-up without robot hardware."""

    def __init__(self):
        self.video_frames: list[dict[str, Any]] = []
        self.audio_samples: list[dict[str, Any]] = []
        self.played_files: list[str] = []
        self.audio_input_started = False
        self.audio_output_started = False
        self._frame_index = 0

    def status(self) -> dict[str, Any]:
        return {
            "status": "available",
            "target": "simulated",
            "video_input": True,
            "video_output": True,
            "audio_input": True,
            "audio_output": True,
            "input_audio_samplerate": 16000,
            "input_channels": 1,
            "output_audio_samplerate": 16000,
            "output_channels": 1,
        }

    def get_video_frame(self, encoding: str = "raw") -> dict[str, Any]:
        self._frame_index += 1
        value = self._frame_index % 256
        data = bytes([value, 64, 128, 255] * 4)
        return {
            "status": "success",
            "target": "simulated",
            "kind": "video_frame",
            "encoding": encoding,
            "dtype": "uint8",
            "shape": [4, 4, 1],
            "data_b64": base64.b64encode(data).decode("ascii"),
        }

    def push_video_frame(
        self,
        data_b64: str,
        width: int,
        height: int,
        channels: int = 3,
        dtype: str = "uint8",
    ) -> dict[str, Any]:
        self.video_frames.append(
            {
                "data_b64": data_b64,
                "width": width,
                "height": height,
                "channels": channels,
                "dtype": dtype,
            }
        )
        return {
            "status": "accepted",
            "target": "simulated",
            "width": width,
            "height": height,
            "channels": channels,
            "dtype": dtype,
        }

    def start_audio_input(self) -> dict[str, Any]:
        self.audio_input_started = True
        return {"status": "started", "target": "simulated"}

    def stop_audio_input(self) -> dict[str, Any]:
        self.audio_input_started = False
        return {"status": "stopped", "target": "simulated"}

    def get_audio_sample(self, encoding: str = "float32") -> dict[str, Any]:
        data = struct.pack("<16f", *([0.12] * 16))
        return {
            "status": "success",
            "target": "simulated",
            "kind": "audio_sample",
            "encoding": encoding,
            "dtype": "float32",
            "shape": [16],
            "sample_rate": 16000,
            "channels": 1,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }

    def start_audio_output(self) -> dict[str, Any]:
        self.audio_output_started = True
        return {"status": "started", "target": "simulated"}

    def stop_audio_output(self) -> dict[str, Any]:
        self.audio_output_started = False
        return {"status": "stopped", "target": "simulated"}

    def play_audio_file(self, sound_file: str) -> dict[str, Any]:
        self.played_files.append(sound_file)
        return {"status": "accepted", "target": "simulated", "sound_file": sound_file}

    def push_audio_sample(
        self,
        data_b64: str,
        sample_rate: int,
        channels: int,
        dtype: str = "float32",
    ) -> dict[str, Any]:
        self.audio_samples.append(
            {
                "data_b64": data_b64,
                "sample_rate": sample_rate,
                "channels": channels,
                "dtype": dtype,
            }
        )
        return {
            "status": "accepted",
            "target": "simulated",
            "sample_rate": sample_rate,
            "channels": channels,
            "frames": 1,
        }


class SdkMediaClient:
    """Lazy wrapper around `reachy_mini` SDK media manager.

    The SDK owns the exact media backend selection. This wrapper only normalizes
    results into JSON-serializable Device Connect payloads.
    """

    def __init__(self, host: str = "reachy-mini.local", media_backend: str | None = None):
        self.host = host
        self.media_backend = media_backend
        self._mini = None

    def status(self) -> dict[str, Any]:
        media = self._media()
        return {
            "status": "available",
            "video_input": hasattr(media, "get_frame"),
            "audio_input": hasattr(media, "get_audio_sample"),
            "audio_output": hasattr(media, "play_sound") or hasattr(media, "push_audio_sample"),
            "input_audio_samplerate": self._maybe_call(media, "get_input_audio_samplerate"),
            "input_channels": self._maybe_call(media, "get_input_channels"),
            "output_audio_samplerate": self._maybe_call(media, "get_output_audio_samplerate"),
            "output_channels": self._maybe_call(media, "get_output_channels"),
        }

    def get_video_frame(self, encoding: str = "raw") -> dict[str, Any]:
        media = self._media()
        frame = media.get_frame()
        return self._array_payload(frame, encoding=encoding, kind="video_frame")

    def push_video_frame(
        self,
        data_b64: str,
        width: int,
        height: int,
        channels: int = 3,
        dtype: str = "uint8",
    ) -> dict[str, Any]:
        import numpy as np

        media = self._media()
        method = self._first_method(
            media,
            ("push_frame", "display_frame", "show_image", "set_frame"),
        )
        if method is None:
            return _unsupported("SDK media manager has no video output method")
        raw = base64.b64decode(data_b64)
        array = np.frombuffer(raw, dtype=np.dtype(dtype)).reshape((height, width, channels))
        result = method(array)
        return {
            "status": "accepted",
            "width": width,
            "height": height,
            "channels": channels,
            "dtype": dtype,
            "result": result,
        }

    def start_audio_input(self) -> dict[str, Any]:
        media = self._media()
        result = self._maybe_call(media, "start_recording")
        return {"status": "started", "result": result}

    def stop_audio_input(self) -> dict[str, Any]:
        media = self._media()
        result = self._maybe_call(media, "stop_recording")
        return {"status": "stopped", "result": result}

    def get_audio_sample(self, encoding: str = "float32") -> dict[str, Any]:
        media = self._media()
        sample = media.get_audio_sample()
        return self._array_payload(sample, encoding=encoding, kind="audio_sample")

    def start_audio_output(self) -> dict[str, Any]:
        media = self._media()
        result = self._maybe_call(media, "start_playing")
        return {"status": "started", "result": result}

    def stop_audio_output(self) -> dict[str, Any]:
        media = self._media()
        result = self._maybe_call(media, "stop_playing")
        return {"status": "stopped", "result": result}

    def play_audio_file(self, sound_file: str) -> dict[str, Any]:
        media = self._media()
        result = media.play_sound(sound_file)
        return {"status": "accepted", "sound_file": sound_file, "result": result}

    def push_audio_sample(
        self,
        data_b64: str,
        sample_rate: int,
        channels: int,
        dtype: str = "float32",
    ) -> dict[str, Any]:
        import numpy as np

        raw = base64.b64decode(data_b64)
        dtype_obj = np.dtype(dtype)
        data = np.frombuffer(raw, dtype=dtype_obj)
        if channels > 1:
            data = data.reshape((-1, channels))
        media = self._media()
        result = media.push_audio_sample(data.astype("float32", copy=False))
        return {
            "status": "accepted",
            "sample_rate": sample_rate,
            "channels": channels,
            "frames": int(data.shape[0]),
            "result": result,
        }

    def _media(self):
        if self._mini is None:
            try:
                from reachy_mini import ReachyMini
            except ImportError as exc:
                raise RuntimeError("reachy_mini SDK is not installed") from exc

            kwargs: dict[str, Any] = {}
            if self.host:
                kwargs["host"] = self.host
            if self.media_backend:
                kwargs["media_backend"] = self.media_backend
            self._mini = ReachyMini(**kwargs)
        media = getattr(self._mini, "media", None)
        if media is None:
            raise RuntimeError("reachy_mini SDK media manager is not available")
        return media

    @staticmethod
    def _maybe_call(obj, name: str):
        method = getattr(obj, name, None)
        if method is None:
            return None
        try:
            return method()
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    @staticmethod
    def _first_method(obj, names: tuple[str, ...]):
        for name in names:
            method = getattr(obj, name, None)
            if method is not None:
                return method
        return None

    @staticmethod
    def _array_payload(value, *, encoding: str, kind: str) -> dict[str, Any]:
        if value is None:
            return {"status": "unavailable", "reason": f"no {kind} returned"}
        try:
            import numpy as np

            array = np.asarray(value)
            return {
                "status": "success",
                "kind": kind,
                "encoding": encoding,
                "dtype": str(array.dtype),
                "shape": list(array.shape),
                "data_b64": base64.b64encode(array.tobytes()).decode("ascii"),
            }
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}
