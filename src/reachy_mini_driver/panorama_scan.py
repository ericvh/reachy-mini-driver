"""Panorama-style capture using existing Device Connect driver RPCs.

An agent (or ``examples/panorama_scan.py``) orchestrates a room scan over **Device
Connect** ``invoke_device`` with:

1. ``set_body_yaw`` / ``look_at_world`` — aim base and head.
2. ``capture_video_frame`` — JPEG still at each pose (NATS-safe over the portal).

Stitching is done locally after download; this module does not send bulk video
over NATS.

**Coverage:** the base can rotate about ±160° and the head yaws about ±45°. We
assume **body-relative** aiming for planning (world azimuth ≈ body + head) unless
the daemon couples axes — use body-drift checks to detect that. A true seamless
360° equirectangular panorama needs feature-based stitching and often more
overlap than JPEG strips provide; exports here are **contact sheets** for review,
not photogrammetry-grade maps.

**Stitching limits:** horizontal strips sort frames by estimated world yaw; a
vertical **grid** stacks one strip per pitch band. There is no feature matching,
lens correction, or spherical projection.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

STITCHING_DISCLAIMER = (
    "Preview contact sheet only: frames are resized and pasted without feature "
    "matching or spherical projection. Gaps, seams, and perspective skew are "
    "expected — use a dedicated panorama tool or higher-quality imaging for "
    "metric room maps."
)


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
HEAD_YAW_FOV_DEG = YAW_MAX_DEG - YAW_MIN_DEG

# Neutral head pose before sweeping (pitch / roll / yaw in degrees).
HOME_HEAD_PITCH_DEG = 0.0
HOME_HEAD_ROLL_DEG = 0.0
HOME_HEAD_YAW_DEG = 0.0

DEFAULT_BODY_DRIFT_TOLERANCE_DEG = 3.0


def normalize_yaw_deg(yaw_deg: float) -> float:
    """Wrap an angle to (-180, 180]."""
    return (yaw_deg + 180.0) % 360.0 - 180.0


def estimate_world_yaw_deg(body_yaw_deg: float, head_yaw_deg: float) -> float:
    """Approximate horizontal look direction (body-relative head model)."""
    return normalize_yaw_deg(body_yaw_deg + head_yaw_deg)


def yaw_delta_deg(a_deg: float, b_deg: float) -> float:
    """Shortest signed difference a → b in degrees."""
    return normalize_yaw_deg(b_deg - a_deg)


def default_yaw_steps(count: int = 5) -> list[float]:
    """Evenly spaced head yaw angles across the driver's allowed range (±45°)."""
    return _linspace_steps(YAW_MIN_DEG, YAW_MAX_DEG, count)


def default_pitch_steps(count: int = 3) -> list[float]:
    """Evenly spaced head pitch (inclination / declination) across ±30°."""
    return _linspace_steps(PITCH_MIN_DEG, PITCH_MAX_DEG, count)


def recommended_steps_for_360(
    *,
    overlap_deg: float = 15.0,
) -> tuple[int, int]:
    """Return ``(body_yaw_step_count, head_yaw_step_count)`` for ~360° coverage.

    Uses body span from motion limits and assumes each body pose exposes ±45°
    head yaw in a body-relative frame.
    """
    from reachy_mini_driver.motion_limits import BODY_YAW_MAX_DEG, BODY_YAW_MIN_DEG

    body_span = BODY_YAW_MAX_DEG - BODY_YAW_MIN_DEG
    body_stride = max(10.0, HEAD_YAW_FOV_DEG - overlap_deg)
    body_count = max(3, int(math.ceil(body_span / body_stride)) + 1)
    head_stride = max(10.0, (HEAD_YAW_FOV_DEG - overlap_deg) / 2.0)
    head_count = max(3, int(math.ceil(HEAD_YAW_FOV_DEG / head_stride)) + 1)
    return body_count, head_count


def default_body_yaw_steps(count: int = 3, *, full_range: bool = True) -> list[float]:
    """Evenly spaced body yaw samples (full range ≈ ±160° when *full_range*)."""
    from reachy_mini_driver.motion_limits import BODY_YAW_MAX_DEG, BODY_YAW_MIN_DEG

    if count < 2:
        return [0.0]
    if full_range:
        return _linspace_steps(BODY_YAW_MIN_DEG, BODY_YAW_MAX_DEG, count)
    return _linspace_steps(max(BODY_YAW_MIN_DEG, -120.0), min(BODY_YAW_MAX_DEG, 120.0), count)


def analyze_world_yaw_coverage(frames: Sequence["PanoramaFrame"]) -> dict[str, Any]:
    """Estimate how much of the horizon ring is sampled (body-relative model)."""
    yaws = [
        frame.world_yaw_deg
        for frame in frames
        if frame.ok and frame.world_yaw_deg is not None
    ]
    if len(yaws) < 2:
        return {
            "sample_count": len(yaws),
            "span_deg": 0.0,
            "largest_gap_deg": 360.0,
            "likely_full_360": False,
            "world_yaw_model": "body_relative_assumed",
        }

    pts = sorted((y % 360.0) for y in yaws)
    gaps = [pts[index + 1] - pts[index] for index in range(len(pts) - 1)]
    gaps.append(360.0 - pts[-1] + pts[0])
    largest_gap = max(gaps)
    span = 360.0 - largest_gap
    return {
        "sample_count": len(yaws),
        "span_deg": round(span, 2),
        "largest_gap_deg": round(largest_gap, 2),
        "likely_full_360": largest_gap <= 25.0,
        "world_yaw_model": "body_relative_assumed",
    }


def _linspace_steps(lo: float, hi: float, count: int) -> list[float]:
    if count < 2:
        return [(lo + hi) / 2.0] if count == 1 else [0.0]
    step = (hi - lo) / (count - 1)
    return [lo + step * index for index in range(count)]


def clamp_head_target(pitch: float, yaw: float) -> tuple[float, float]:
    pitch = max(PITCH_MIN_DEG, min(PITCH_MAX_DEG, pitch))
    yaw = max(YAW_MIN_DEG, min(YAW_MAX_DEG, yaw))
    return pitch, yaw


async def read_body_yaw_deg(driver: PanoramaDriver) -> float | None:
    getter = getattr(driver, "get_body_yaw", None)
    if getter is None:
        return None
    try:
        raw = await getter()
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("status") != "success":
        return None
    yaw_deg = raw.get("yaw_deg")
    if isinstance(yaw_deg, (int, float)):
        return float(yaw_deg)
    yaw_rad = raw.get("yaw_rad")
    if isinstance(yaw_rad, (int, float)):
        return math.degrees(float(yaw_rad))
    return None


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
    body_yaw_commanded_deg: float | None = None
    body_yaw_observed_deg: float | None = None
    body_drift_after_move_deg: float | None = None
    body_drift_after_look_deg: float | None = None
    world_yaw_deg: float | None = None

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
    body_drift_warnings: list[str] = field(default_factory=list)

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
        coverage = analyze_world_yaw_coverage(self.frames)
        return {
            "frame_count": len(self.frames),
            "success_count": self.success_count,
            "coverage_yaw_deg": self.coverage_yaw_deg,
            "coverage_pitch_deg": self._coverage_pitch_deg(),
            "coverage_body_yaw_deg": self.coverage_body_yaw_deg,
            "world_yaw_coverage": coverage,
            "body_drift_warnings": list(self.body_drift_warnings),
            "stitching_disclaimer": STITCHING_DISCLAIMER,
            "head_yaw_limit_deg": [YAW_MIN_DEG, YAW_MAX_DEG],
            "head_pitch_limit_deg": [PITCH_MIN_DEG, PITCH_MAX_DEG],
            "frames": [
                {
                    "index": frame.index,
                    "body_yaw_deg": frame.body_yaw_deg,
                    "body_yaw_commanded_deg": frame.body_yaw_commanded_deg,
                    "body_yaw_observed_deg": frame.body_yaw_observed_deg,
                    "body_drift_after_move_deg": frame.body_drift_after_move_deg,
                    "body_drift_after_look_deg": frame.body_drift_after_look_deg,
                    "world_yaw_deg": frame.world_yaw_deg,
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


async def align_head_home(
    driver: PanoramaDriver,
    *,
    owner: str = "panorama",
    roll_deg: float = HOME_HEAD_ROLL_DEG,
    settle_s: float = 0.35,
) -> dict[str, Any]:
    """Center the head (0° pitch / roll / yaw) before a panorama sweep."""
    look = await driver.look_at_world(
        pitch=HOME_HEAD_PITCH_DEG,
        roll=roll_deg,
        yaw=HOME_HEAD_YAW_DEG,
        owner=owner,
    )
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    return look


def sort_frames_by_world_yaw(frames: Sequence[PanoramaFrame]) -> list[PanoramaFrame]:
    """Order frames left-to-right by estimated world azimuth."""
    return sorted(
        frames,
        key=lambda frame: (
            frame.world_yaw_deg if frame.world_yaw_deg is not None else frame.body_yaw_deg + frame.yaw_deg,
            frame.pitch_deg,
        ),
    )


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
    align_head_home_at_start: bool = True,
    home_settle_s: float | None = None,
    verify_body_pose: bool = True,
    body_drift_tolerance_deg: float = DEFAULT_BODY_DRIFT_TOLERANCE_DEG,
) -> PanoramaScanResult:
    """Sweep body yaw, head pitch, and head yaw; capture a JPEG at each pose.

    When *align_head_home_at_start* is true (default), the head is moved to
    neutral (0° pitch / roll / yaw) and allowed to settle before the first
    capture so the sweep starts from a known pose.

    When *verify_body_pose* is true, reads ``get_body_yaw`` after body moves and
    after each head aim; large unexpected body motion is recorded in
    ``body_drift_warnings`` (common when the daemon couples head/world IK).
    """
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
    locked_body_yaw: float | None = None

    if align_head_home_at_start:
        home_wait = home_settle_s if home_settle_s is not None else max(settle_s, 0.35)
        await align_head_home(
            driver,
            owner=owner,
            roll_deg=roll_deg,
            settle_s=home_wait,
        )
        if verify_body_pose:
            locked_body_yaw = await read_body_yaw_deg(driver)

    for body_yaw_deg in body_steps:
        body_commanded = body_yaw_deg
        if body_yaw_deg != 0.0 or body_steps != [0.0]:
            await driver.set_body_yaw(
                body_yaw_deg,
                duration_s=body_move_duration_s,
                owner=owner,
            )
            wait_s = max(settle_s, body_move_duration_s)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            if verify_body_pose:
                observed = await read_body_yaw_deg(driver)
                if observed is not None:
                    move_drift = abs(yaw_delta_deg(body_commanded, observed))
                    if move_drift > body_drift_tolerance_deg:
                        msg = (
                            f"body move to {body_commanded:+.1f}° observed "
                            f"{observed:+.1f}° (drift {move_drift:.1f}°)"
                        )
                        result.body_drift_warnings.append(msg)
                    locked_body_yaw = observed
                else:
                    locked_body_yaw = body_commanded
        elif locked_body_yaw is None:
            locked_body_yaw = 0.0

        reference_body = locked_body_yaw if locked_body_yaw is not None else body_yaw_deg
        drift_after_move: float | None = None
        if verify_body_pose and locked_body_yaw is not None:
            drift_after_move = abs(yaw_delta_deg(body_commanded, locked_body_yaw))

        for pitch_target in head_pitch_steps:
            for yaw in head_yaw_steps:
                pitch, yaw_clamped = clamp_head_target(pitch_target, yaw)
                body_before_look = await read_body_yaw_deg(driver) if verify_body_pose else None
                look = await driver.look_at_world(
                    pitch=pitch,
                    roll=roll_deg,
                    yaw=yaw_clamped,
                    owner=owner,
                )
                if settle_s > 0:
                    await asyncio.sleep(settle_s)

                drift_after_look: float | None = None
                observed_after_look = await read_body_yaw_deg(driver) if verify_body_pose else None
                if (
                    verify_body_pose
                    and body_before_look is not None
                    and observed_after_look is not None
                ):
                    drift_after_look = abs(yaw_delta_deg(body_before_look, observed_after_look))
                    if drift_after_look > body_drift_tolerance_deg:
                        msg = (
                            f"head look pitch={pitch:+.0f} yaw={yaw_clamped:+.0f} moved body "
                            f"{body_before_look:+.1f}° → {observed_after_look:+.1f}° "
                            f"(Δ{drift_after_look:.1f}°)"
                        )
                        result.body_drift_warnings.append(msg)
                        locked_body_yaw = observed_after_look

                capture = await driver.capture_video_frame(
                    encoding=encoding,
                    max_edge=max_edge,
                    quality=quality,
                )
                body_for_world = (
                    observed_after_look
                    if observed_after_look is not None
                    else reference_body
                )
                frame = PanoramaFrame(
                    index=frame_index,
                    body_yaw_deg=body_for_world,
                    pitch_deg=pitch,
                    yaw_deg=yaw_clamped,
                    look_result=look,
                    capture_result=capture,
                    jpeg_bytes=decode_jpeg_from_capture(capture),
                    body_yaw_commanded_deg=body_commanded,
                    body_yaw_observed_deg=observed_after_look or reference_body,
                    body_drift_after_move_deg=drift_after_move,
                    body_drift_after_look_deg=drift_after_look,
                    world_yaw_deg=estimate_world_yaw_deg(body_for_world, yaw_clamped),
                )
                result.frames.append(frame)
                frame_index += 1

    return result


def stitch_horizontal_jpeg(
    frames: Sequence[PanoramaFrame],
    *,
    background: tuple[int, int, int] = (32, 32, 32),
    sort_by_world_yaw: bool = True,
) -> bytes:
    """Concatenate successful frames left-to-right (simple strip, not spherical)."""
    from PIL import Image

    band = list(frames)
    if sort_by_world_yaw:
        band = sort_frames_by_world_yaw(band)

    images: list[Image.Image] = []
    for frame in band:
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


def stitch_pitch_grid_jpeg(
    frames: Sequence[PanoramaFrame],
    *,
    background: tuple[int, int, int] = (32, 32, 32),
    band_gap_px: int = 4,
) -> bytes:
    """Stack one horizontal strip per pitch (top = lowest pitch / down in image coords)."""
    from PIL import Image

    ok_frames = [frame for frame in frames if frame.ok and frame.jpeg_bytes]
    if not ok_frames:
        raise ValueError("no successful frames to stitch")

    pitches = sorted({frame.pitch_deg for frame in ok_frames})
    row_images: list[Image.Image] = []
    for pitch in pitches:
        band = [frame for frame in ok_frames if frame.pitch_deg == pitch]
        row_bytes = stitch_horizontal_jpeg(band, background=background, sort_by_world_yaw=True)
        row_images.append(Image.open(io.BytesIO(row_bytes)).convert("RGB"))

    max_width = max(image.width for image in row_images)
    total_height = sum(image.height for image in row_images) + band_gap_px * (len(row_images) - 1)
    canvas = Image.new("RGB", (max_width, total_height), background)
    y = 0
    for image in row_images:
        if image.width < max_width:
            padded = Image.new("RGB", (max_width, image.height), background)
            padded.paste(image, (0, 0))
            image = padded
        canvas.paste(image, (0, y))
        y += image.height + band_gap_px

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()


def save_scan_artifacts(
    scan: PanoramaScanResult,
    output_dir: str,
    *,
    strip_name: str = "panorama_strip.jpg",
    grid_name: str = "panorama_grid.jpg",
) -> dict[str, str]:
    """Write per-frame JPEGs, manifest JSON, sorted strips, and a pitch grid."""
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
            strip_bytes = stitch_horizontal_jpeg(band, sort_by_world_yaw=True)
            pitch_path = strips_dir / f"strip_pitch_{pitch:+.0f}.jpg".replace("+", "p").replace("-", "m")
            pitch_path.write_bytes(strip_bytes)
            paths[f"strip_pitch_{pitch:+.0f}"] = str(pitch_path)

        if len(pitches) > 1:
            grid_path = root / grid_name
            grid_path.write_bytes(stitch_pitch_grid_jpeg(ok_frames))
            paths["grid"] = str(grid_path)

        if len(ok_frames) <= 24:
            strip_bytes = stitch_horizontal_jpeg(ok_frames, sort_by_world_yaw=True)
            strip_path = root / strip_name
            strip_path.write_bytes(strip_bytes)
            paths["strip"] = str(strip_path)

    return paths
