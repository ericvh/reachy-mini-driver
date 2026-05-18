"""Panorama-style capture using existing Device Connect driver RPCs.

An agent (or ``examples/panorama_scan.py``) orchestrates a room scan over **Device
Connect** ``invoke_device`` with:

1. ``set_body_yaw`` / ``look_at_world`` — aim base and head.
2. ``capture_video_frame`` — JPEG still at each pose (NATS-safe over the portal).

Stitching is done locally after download; this module does not send bulk video
over NATS.

**Coverage:** combine ``set_body_yaw`` (base rotation, about ±160°) with
``look_at_world`` head yaw (±45°) to scan a much wider arc. A full 360° still
needs overlapping shots and proper stitching (this module only does a flat strip).
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class PanoramaDriver(Protocol):
    """Minimal driver surface used for panorama capture."""

    async def set_body_yaw(
        self,
        yaw_deg: float = 0.0,
        duration_s: float = 0.0,
        owner: str = "agent",
    ) -> dict[str, Any]: ...

    async def look_at_world(
        self,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        z_mm: float = 0.0,
        owner: str = "agent",
    ) -> dict[str, Any]: ...

    async def capture_video_frame(
        self,
        encoding: str = "jpeg",
        max_edge: int | None = None,
        quality: int | None = None,
    ) -> dict[str, Any]: ...


# Matches look_at_world validation in device_connect.py
YAW_MIN_DEG = -45.0
YAW_MAX_DEG = 45.0
PITCH_MIN_DEG = -30.0
PITCH_MAX_DEG = 30.0


def default_yaw_steps(count: int = 5) -> list[float]:
    """Evenly spaced head yaw angles across the driver's allowed range (±45°)."""
    return _linspace_steps(YAW_MIN_DEG, YAW_MAX_DEG, count)


def default_pitch_steps(count: int = 3) -> list[float]:
    """Evenly spaced head pitch (inclination / declination) across ±30°."""
    return _linspace_steps(PITCH_MIN_DEG, PITCH_MAX_DEG, count)


def _linspace_steps(lo: float, hi: float, count: int) -> list[float]:
    if count < 2:
        return [(lo + hi) / 2.0] if count == 1 else [0.0]
    step = (hi - lo) / (count - 1)
    return [lo + step * index for index in range(count)]


def clamp_head_target(pitch: float, yaw: float) -> tuple[float, float]:
    pitch = max(PITCH_MIN_DEG, min(PITCH_MAX_DEG, pitch))
    yaw = max(YAW_MIN_DEG, min(YAW_MAX_DEG, yaw))
    return pitch, yaw


@dataclass
class PanoramaFrame:
    """One captured still with the head pose used."""

    index: int
    body_yaw_deg: float
    pitch_deg: float
    yaw_deg: float
    look_result: dict[str, Any]
    capture_result: dict[str, Any]
    jpeg_bytes: bytes | None = None

    @property
    def ok(self) -> bool:
        return (
            self.jpeg_bytes is not None
            and self.look_result.get("status") == "accepted"
            and self.capture_result.get("status") == "success"
        )


@dataclass
class PanoramaScanResult:
    """Outcome of a multi-pose capture pass."""

    frames: list[PanoramaFrame] = field(default_factory=list)
    encoding: str = "jpeg"
    owner: str = "panorama"

    @property
    def success_count(self) -> int:
        return sum(1 for frame in self.frames if frame.ok)

    @property
    def coverage_yaw_deg(self) -> float:
        yaws = [frame.yaw_deg for frame in self.frames if frame.ok]
        if len(yaws) < 2:
            return 0.0
        return max(yaws) - min(yaws)

    def _coverage_pitch_deg(self) -> float:
        pitches = [frame.pitch_deg for frame in self.frames if frame.ok]
        if len(pitches) < 2:
            return 0.0
        return max(pitches) - min(pitches)

    @property
    def coverage_body_yaw_deg(self) -> float:
        body = [frame.body_yaw_deg for frame in self.frames if frame.ok]
        if len(body) < 2:
            return 0.0
        return max(body) - min(body)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "frame_count": len(self.frames),
            "success_count": self.success_count,
            "coverage_yaw_deg": self.coverage_yaw_deg,
            "coverage_pitch_deg": self._coverage_pitch_deg(),
            "coverage_body_yaw_deg": self.coverage_body_yaw_deg,
            "head_yaw_limit_deg": [YAW_MIN_DEG, YAW_MAX_DEG],
            "head_pitch_limit_deg": [PITCH_MIN_DEG, PITCH_MAX_DEG],
            "frames": [
                {
                    "index": frame.index,
                    "body_yaw_deg": frame.body_yaw_deg,
                    "pitch_deg": frame.pitch_deg,
                    "yaw_deg": frame.yaw_deg,
                    "ok": frame.ok,
                    "look_status": frame.look_result.get("status"),
                    "capture_status": frame.capture_result.get("status"),
                    "width": frame.capture_result.get("width"),
                    "height": frame.capture_result.get("height"),
                    "byte_size": len(frame.jpeg_bytes) if frame.jpeg_bytes else 0,
                }
                for frame in self.frames
            ],
        }


def decode_jpeg_from_capture(capture: dict[str, Any]) -> bytes | None:
    if capture.get("status") != "success":
        return None
    if capture.get("format") == "jpeg" or capture.get("encoding") in ("jpeg", "thumbnail"):
        raw = capture.get("data_b64")
        if not raw:
            return None
        return base64.b64decode(raw)
    return None


def default_body_yaw_steps(count: int = 3, *, full_range: bool = True) -> list[float]:
    """Evenly spaced body yaw samples (full range ≈ ±160° when *full_range*)."""
    from reachy_mini_driver.motion_limits import BODY_YAW_MAX_DEG, BODY_YAW_MIN_DEG

    if count < 2:
        return [0.0]
    if full_range:
        return _linspace_steps(BODY_YAW_MIN_DEG, BODY_YAW_MAX_DEG, count)
    return _linspace_steps(max(BODY_YAW_MIN_DEG, -120.0), min(BODY_YAW_MAX_DEG, 120.0), count)


async def capture_panorama_scan(
    driver: PanoramaDriver,
    *,
    yaw_steps: Sequence[float] | None = None,
    pitch_steps: Sequence[float] | None = None,
    body_yaw_steps: Sequence[float] | None = None,
    body_move_duration_s: float = 0.5,
    pitch_deg: float | None = None,
    roll_deg: float = 0.0,
    settle_s: float = 0.35,
    encoding: str = "jpeg",
    max_edge: int | None = None,
    quality: int | None = None,
    owner: str = "panorama",
) -> PanoramaScanResult:
    """Sweep body yaw, head pitch, and head yaw; capture a JPEG at each pose."""
    head_yaw_steps = list(yaw_steps if yaw_steps is not None else default_yaw_steps())
    if pitch_steps is not None:
        head_pitch_steps = list(pitch_steps)
    elif pitch_deg is not None:
        head_pitch_steps = [pitch_deg]
    else:
        head_pitch_steps = default_pitch_steps(3)

    if body_yaw_steps is not None:
        body_steps = list(body_yaw_steps)
    else:
        body_steps = [0.0]

    result = PanoramaScanResult(encoding=encoding, owner=owner)
    frame_index = 0

    for body_yaw_deg in body_steps:
        if body_yaw_deg != 0.0 or body_steps != [0.0]:
            await driver.set_body_yaw(
                body_yaw_deg,
                duration_s=body_move_duration_s,
                owner=owner,
            )
            wait_s = max(settle_s, body_move_duration_s)
            if wait_s > 0:
                await asyncio.sleep(wait_s)

        for pitch_target in head_pitch_steps:
            for yaw in head_yaw_steps:
                pitch, yaw_clamped = clamp_head_target(pitch_target, yaw)
                look = await driver.look_at_world(
                    pitch=pitch,
                    roll=roll_deg,
                    yaw=yaw_clamped,
                    owner=owner,
                )
                if settle_s > 0:
                    await asyncio.sleep(settle_s)
                capture = await driver.capture_video_frame(
                    encoding=encoding,
                    max_edge=max_edge,
                    quality=quality,
                )
                frame = PanoramaFrame(
                    index=frame_index,
                    body_yaw_deg=body_yaw_deg,
                    pitch_deg=pitch,
                    yaw_deg=yaw_clamped,
                    look_result=look,
                    capture_result=capture,
                    jpeg_bytes=decode_jpeg_from_capture(capture),
                )
                result.frames.append(frame)
                frame_index += 1

    return result


def stitch_horizontal_jpeg(
    frames: Sequence[PanoramaFrame],
    *,
    background: tuple[int, int, int] = (32, 32, 32),
) -> bytes:
    """Concatenate successful frames left-to-right (simple strip, not spherical)."""
    from PIL import Image

    images: list[Image.Image] = []
    for frame in frames:
        if not frame.ok or frame.jpeg_bytes is None:
            continue
        images.append(Image.open(io.BytesIO(frame.jpeg_bytes)).convert("RGB"))

    if not images:
        raise ValueError("no successful frames to stitch")

    heights = [image.height for image in images]
    target_height = max(heights)
    resized: list[Image.Image] = []
    for image in images:
        if image.height != target_height:
            scale = target_height / image.height
            new_width = max(1, int(math.floor(image.width * scale)))
            image = image.resize((new_width, target_height), Image.Resampling.LANCZOS)
        resized.append(image)

    total_width = sum(image.width for image in resized)
    canvas = Image.new("RGB", (total_width, target_height), background)
    x = 0
    for image in resized:
        canvas.paste(image, (x, 0))
        x += image.width

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()


def save_scan_artifacts(
    scan: PanoramaScanResult,
    output_dir: str,
    *,
    strip_name: str = "panorama_strip.jpg",
) -> dict[str, str]:
    """Write per-frame JPEGs, manifest JSON, and a horizontal strip."""
    import json
    from pathlib import Path

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    for frame in scan.frames:
        if frame.jpeg_bytes is None:
            continue
        path = root / (
            f"frame_{frame.index:03d}_body_{frame.body_yaw_deg:+.0f}"
            f"_pitch_{frame.pitch_deg:+.0f}_yaw_{frame.yaw_deg:+.0f}.jpg"
        )
        path.write_bytes(frame.jpeg_bytes)
        paths[f"frame_{frame.index}"] = str(path)

    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(scan.to_manifest(), indent=2), encoding="utf-8")
    paths["manifest"] = str(manifest_path)

    ok_frames = [frame for frame in scan.frames if frame.ok]
    if ok_frames:
        strips_dir = root / "strips"
        strips_dir.mkdir(exist_ok=True)
        pitches = sorted({frame.pitch_deg for frame in ok_frames})
        for pitch in pitches:
            band = [frame for frame in ok_frames if frame.pitch_deg == pitch]
            if not band:
                continue
            strip_bytes = stitch_horizontal_jpeg(band)
            pitch_path = strips_dir / f"strip_pitch_{pitch:+.0f}.jpg".replace("+", "p").replace("-", "m")
            pitch_path.write_bytes(strip_bytes)
            paths[f"strip_pitch_{pitch:+.0f}"] = str(pitch_path)

        if len(ok_frames) <= 24:
            strip_bytes = stitch_horizontal_jpeg(ok_frames)
            strip_path = root / strip_name
            strip_path.write_bytes(strip_bytes)
            paths["strip"] = str(strip_path)

    return paths
