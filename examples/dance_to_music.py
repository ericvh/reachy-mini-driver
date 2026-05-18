#!/usr/bin/env python3
"""Dance Reachy Mini in time with music heard on its microphone (Device Connect).

Requires the driver on the portal mesh and media (mic) available::

    export NATS_CREDENTIALS_FILE=~/Downloads/your-portal.creds.json
    python examples/dance_to_music.py --duration 60

Play music near the robot — beats are detected from mic RMS and drive motion.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from reachy_mini_driver.mesh import (
    connect_mesh,
    disconnect_mesh,
    resolve_mesh_settings,
    wait_for_device,
)
from reachy_mini_driver.panorama_dc_client import (
    PanoramaDeviceConnectClient,
    unwrap_invoke_response,
)

OWNER = "music_dance"

# Choreography pool — scaled by beat energy (0..1)
MOVES: list[tuple[float, float, float, float, float, float]] = [
    (10, 8, 35, 45, -40, 40),
    (-8, -10, -40, -50, 45, -45),
    (15, 0, 0, 60, 60, 0),
    (-12, 12, 30, -55, 55, 50),
    (6, -15, -38, 50, -50, -55),
    (0, 18, 20, -35, 35, 25),
    (-14, -6, -22, 40, 40, -20),
    (8, 8, 42, -60, 60, 35),
    (-6, 0, -45, 30, -30, -35),
    (12, -8, 15, 55, -55, 15),
    (-10, 10, -30, -40, 40, 0),
    (5, 5, 0, 0, 0, 60),
    (0, -12, 25, 35, 35, -60),
    (-8, 6, -35, -45, 45, 30),
    (14, -5, 40, 50, -50, -25),
    (0, 0, 0, 25, -25, 0),
]


@dataclass
class BeatTracker:
    """Onset detector: RMS spike vs rolling average + short-term rise."""

    window: int = 20
    sensitivity: float = 1.18
    floor: float = 0.00004
    min_interval_s: float = 0.22
    rise_ratio: float = 1.08
    absolute_min: float = 0.00003

    def __post_init__(self) -> None:
        self._history: deque[float] = deque(maxlen=self.window)
        self._last_beat = 0.0
        self._prev_rms = 0.0

    def update(self, rms: float, now: float) -> tuple[bool, float]:
        self._history.append(rms)
        if len(self._history) < 4:
            self._prev_rms = rms
            return False, 0.0

        avg = sum(self._history) / len(self._history)
        energy = min(1.0, rms / max(avg, self.floor, 1e-4))
        threshold = max(self.floor, avg * self.sensitivity, self.absolute_min)
        rising = rms > self._prev_rms * self.rise_ratio or rms > self.absolute_min * 2
        loud_enough = rms > threshold
        self._prev_rms = rms

        if loud_enough and rising and (now - self._last_beat) >= self.min_interval_s:
            self._last_beat = now
            return True, energy
        return False, energy


class DanceClient(PanoramaDeviceConnectClient):
    owner: str = OWNER

    async def wake_up(self) -> dict:
        return await asyncio.to_thread(self._call, "wake_up", {"owner": self.owner})

    async def acquire_media(self) -> dict:
        return await asyncio.to_thread(self._call, "acquire_media_hardware", None)

    async def start_audio_input(self) -> dict:
        return await asyncio.to_thread(self._call, "start_audio_input", None)

    async def detect_audio_activity(
        self, *, threshold: float = 0.02, duration_ms: int = 80
    ) -> dict:
        return await asyncio.to_thread(
            self._call,
            "detect_audio_activity",
            {"threshold": threshold, "duration_ms": duration_ms},
        )

    async def dance_pose(
        self,
        *,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        left: float = 0.0,
        right: float = 0.0,
        body_yaw: float | None = None,
        body_duration_s: float = 0.35,
    ) -> None:
        await asyncio.to_thread(
            self._call,
            "look_at_world",
            {
                "pitch": pitch,
                "roll": roll,
                "yaw": yaw,
                "owner": self.owner,
            },
        )
        await asyncio.to_thread(
            self._call,
            "antenna_pose",
            {"left": left, "right": right, "owner": self.owner},
        )
        if body_yaw is not None:
            await asyncio.to_thread(
                self._call,
                "set_body_yaw",
                {
                    "yaw_deg": body_yaw,
                    "duration_s": body_duration_s,
                    "owner": self.owner,
                },
            )

    async def home_pose(self) -> None:
        await self.dance_pose()


def scale_move(move: tuple[float, ...], energy: float) -> tuple[float, ...]:
    e = 0.35 + 0.65 * max(0.0, min(1.0, energy))
    p, r, y, la, ra, body = move
    return (p * e, r * e, y * e, la * e, ra * e, body * e)


async def run_dance(
    client: DanceClient,
    *,
    duration_s: float,
    poll_s: float,
    calibrate_s: float,
    sensitivity: float,
    metronome_bpm: float = 108.0,
) -> None:
    print("Waking up and opening mic…")
    await client.wake_up()
    await asyncio.sleep(0.8)
    await client.acquire_media()
    await asyncio.sleep(0.4)
    await client.start_audio_input()
    await asyncio.sleep(0.5)

    tracker = BeatTracker(sensitivity=sensitivity)
    calib: list[float] = []
    beat_count = 0
    move_idx = 0
    peak_rms = 0.0
    last_level_log = 0.0
    last_metronome = 0.0
    metronome_enabled = False
    music_detected = False
    start = time.monotonic()
    deadline = start + duration_s
    calib_deadline = start + calibrate_s

    print(f"Listening for {duration_s:.0f}s — play music near Reachy!")
    print(f"(calibrating levels for {calibrate_s:.1f}s…)")
    sys.stdout.flush()

    while time.monotonic() < deadline:
        now = time.monotonic()
        raw = await client.detect_audio_activity(threshold=0.01, duration_ms=80)
        if raw.get("status") != "success":
            await asyncio.sleep(poll_s)
            continue

        event = raw.get("event", {})
        rms = float(event.get("rms", 0.0))
        peak_rms = max(peak_rms, rms)

        if now - last_level_log >= 5.0:
            last_level_log = now
            print(f"  … level rms={rms:.4f} peak={peak_rms:.4f} floor={tracker.floor:.4f}")
            sys.stdout.flush()

        if now < calib_deadline:
            calib.append(rms)
            await asyncio.sleep(poll_s)
            continue

        if calib:
            noise = sorted(calib)[len(calib) // 2]
            tracker.floor = max(tracker.absolute_min, noise * 1.25)
            calib.clear()
            print(f"  noise floor ≈ {tracker.floor:.6f} (play music now)")
            sys.stdout.flush()

        if peak_rms > 0.001:
            music_detected = True
            metronome_enabled = False
        elif not music_detected and (now - start) > 8.0 and not metronome_enabled:
            metronome_enabled = True
            print("  (mic very quiet — adding metronome fallback; still reacts to loud spikes)")
            sys.stdout.flush()

        is_beat, energy = tracker.update(rms, now)
        metronome_beat = False
        if metronome_enabled:
            interval = 60.0 / metronome_bpm
            if (now - last_metronome) >= interval:
                metronome_beat = True
                last_metronome = now
                energy = 0.55

        if is_beat or metronome_beat:
            beat_count += 1
            move = scale_move(MOVES[move_idx % len(MOVES)], energy)
            move_idx += 1
            p, r, y, la, ra, body = move
            body_dur = 0.25 + 0.35 * energy
            tag = "♪" if is_beat else "⏱"
            print(f"  {tag} beat {beat_count}  rms={rms:.6f}  energy={energy:.2f}")
            sys.stdout.flush()
            await client.dance_pose(
                pitch=p,
                roll=r,
                yaw=y,
                left=la,
                right=ra,
                body_yaw=body,
                body_duration_s=body_dur,
            )
        elif rms > max(tracker.floor * 2.0, tracker.absolute_min * 3):
            # soft groove between beats when music is clearly present
            sway = 6.0 * energy * math.sin(now * 5.0)
            if abs(sway) > 2.0:
                await client.dance_pose(
                    pitch=sway * 0.25,
                    yaw=sway * 0.8,
                    left=sway,
                    right=-sway,
                )

        await asyncio.sleep(poll_s)

    print(f"Finished — {beat_count} beats (session peak rms={peak_rms:.4f}). Returning home…")
    await client.home_pose()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Mic-synced dance via Device Connect")
    parser.add_argument("--credentials-file", type=str, default=None)
    parser.add_argument("--device-id", type=str, default=None)
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="How long to listen and dance (seconds)",
    )
    parser.add_argument("--poll", type=float, default=0.08, help="Mic poll interval (s)")
    parser.add_argument(
        "--calibrate",
        type=float,
        default=2.0,
        help="Seconds of silence calibration at start",
    )
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=1.08,
        help="Beat threshold multiplier over rolling average (lower = more beats)",
    )
    parser.add_argument(
        "--bpm",
        type=float,
        default=108.0,
        help="Metronome BPM when mic level stays near silence",
    )
    args = parser.parse_args()

    zone, device_id, _urls = resolve_mesh_settings(
        credentials_file=args.credentials_file,
        device_id=args.device_id,
    )
    if not device_id:
        print("No device_id — pass --device-id or set NATS_CREDENTIALS_FILE", file=sys.stderr)
        return 1

    connect_mesh(credentials_file=args.credentials_file, tenant=zone)
    try:
        wait_for_device(device_id, timeout_s=30)
        client = DanceClient(device_id=device_id)
        await run_dance(
            client,
            duration_s=args.duration,
            poll_s=args.poll,
            calibrate_s=args.calibrate,
            sensitivity=args.sensitivity,
            metronome_bpm=args.bpm,
        )
        return 0
    finally:
        disconnect_mesh()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
