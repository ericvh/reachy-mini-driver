"""In-process panorama scan (ReachyMiniDriver → daemon). Not for portal use."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reachy_mini_driver.config import parse_reachy_target
from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.panorama_scan import (
    capture_panorama_scan,
    default_body_yaw_steps,
    default_yaw_steps,
    save_scan_artifacts,
)
from reachy_mini_driver.transport import ReachyHardwareTransport, SimReachyTransport


async def run_direct_panorama_scan(
    *,
    sim: bool,
    target: str,
    output: Path,
    yaw_steps: int,
    pitch: float,
    settle_s: float,
    body_yaw_steps: int,
    body_move_s: float,
) -> int:
    host, api_port, _ = parse_reachy_target(target if not sim else "sim")

    if sim:
        transport = SimReachyTransport()
        media = SimMediaClient()
    else:
        transport = ReachyHardwareTransport(host, api_port, mode="auto")
        media = None

    driver = ReachyMiniDriver(host=host, api_port=api_port, transport=transport, media=media)
    await driver.connect()

    steps = default_yaw_steps(yaw_steps)
    body_steps = default_body_yaw_steps(body_yaw_steps) if body_yaw_steps > 1 else [0.0]
    scan = await capture_panorama_scan(
        driver,
        yaw_steps=steps,
        body_yaw_steps=body_steps,
        body_move_duration_s=body_move_s,
        pitch_deg=pitch,
        settle_s=settle_s,
        encoding="jpeg",
        owner="panorama-direct",
    )
    await driver.disconnect()

    paths = save_scan_artifacts(scan, str(output))
    print(json.dumps(scan.to_manifest(), indent=2))
    print(f"Saved {scan.success_count}/{len(scan.frames)} frames under {output}")
    if "strip" in paths:
        print(f"Horizontal strip: {paths['strip']}")
        return 0
    return 1
