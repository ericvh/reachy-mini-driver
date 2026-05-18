#!/usr/bin/env python3
"""Exercise panorama capture: look around + JPEG frames + horizontal strip.

Works without hardware (--sim) or against a running driver target.

Sim / in-process (no Device Connect broker):

    pip install -e ".[media]"
    python examples/panorama_scan.py --sim --output /tmp/reachy-panorama

Robot on LAN (driver must be running, e.g. python -m reachy_mini_driver --target …):

    python examples/panorama_scan.py --target 192.168.2.156:8000 --output ./panorama-out

Via Device Connect MCP (agent-shaped): call the same RPC sequence your agent would use:

    look_at_world(pitch=0, yaw=-45, owner="panorama")
    capture_video_frame(encoding="jpeg")
    … repeat yaw steps …
    # stitch client-side with Pillow or OpenCV

Limits (see README / panorama_scan module docstring):

- ``look_at_world`` yaw is ±45° → ~90° head sweep, not a true 360° sphere.
- Use ``--body-yaw-steps`` for base rotation (see ``set_body_yaw`` RPC).
- Strip stitch is a flat concat; real panoramas need overlap + calibration (OpenCV).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from repo root without install
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

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


async def run_scan(
    *,
    sim: bool,
    host: str,
    api_port: int,
    output: Path,
    yaw_steps: int,
    pitch: float,
    settle_s: float,
    body_yaw_steps: int,
    body_move_s: float,
) -> int:
    if sim:
        transport = SimReachyTransport()
        media = SimMediaClient()
    else:
        transport = ReachyHardwareTransport(host, api_port, mode="auto")
        media = None  # SdkMediaClient via driver default when reachy_mini installed

    driver = ReachyMiniDriver(
        host=host,
        api_port=api_port,
        transport=transport,
        media=media,
    )
    await driver.connect()

    steps = default_yaw_steps(yaw_steps)
    body_steps = default_body_yaw_steps(body_yaw_steps) if body_yaw_steps > 1 else [0.0]
    print(
        f"Panorama scan: {len(body_steps)} body × {len(steps)} head yaw "
        f"(head {steps[0]:.0f}° … {steps[-1]:.0f}°)"
    )
    scan = await capture_panorama_scan(
        driver,
        yaw_steps=steps,
        body_yaw_steps=body_steps,
        body_move_duration_s=body_move_s,
        pitch_deg=pitch,
        settle_s=settle_s,
        encoding="jpeg",
        owner="panorama-example",
    )
    await driver.disconnect()

    paths = save_scan_artifacts(scan, str(output))
    manifest = scan.to_manifest()
    print(json.dumps(manifest, indent=2))
    print(f"Saved {scan.success_count}/{len(scan.frames)} frames under {output}")
    if "strip" in paths:
        print(f"Horizontal strip: {paths['strip']}")
    else:
        print("No strip written (no successful frames).", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Use simulated transport/media (no robot)",
    )
    parser.add_argument(
        "--target",
        default="sim",
        help="REACHY_TARGET form: sim, host:port, or http://host:port",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/reachy-panorama"),
        help="Directory for frames, manifest.json, panorama_strip.jpg",
    )
    parser.add_argument(
        "--yaw-steps",
        type=int,
        default=5,
        help="Number of yaw samples across [-45, 45] degrees",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=0.0,
        help="Fixed pitch in degrees for every shot",
    )
    parser.add_argument(
        "--settle-s",
        type=float,
        default=0.35,
        help="Seconds to wait after each look_at_world before capture",
    )
    parser.add_argument(
        "--body-yaw-steps",
        type=int,
        default=0,
        help="If >1, sweep body yaw with this many steps (±120°); 0 = body fixed at 0°",
    )
    parser.add_argument(
        "--body-move-s",
        type=float,
        default=0.5,
        help="Duration for each set_body_yaw goto move",
    )
    args = parser.parse_args()

    sim = args.sim or args.target.strip().lower() == "sim"
    host, api_port, _ = parse_reachy_target(args.target if not sim else "sim")

    try:
        code = asyncio.run(
            run_scan(
                sim=sim,
                host=host,
                api_port=api_port,
                output=args.output,
                yaw_steps=max(2, args.yaw_steps),
                pitch=args.pitch,
                settle_s=args.settle_s,
                body_yaw_steps=args.body_yaw_steps,
                body_move_s=args.body_move_s,
            )
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(code)


if __name__ == "__main__":
    main()
