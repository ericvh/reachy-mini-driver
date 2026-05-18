#!/usr/bin/env python3
"""Direct daemon panorama scan (bypasses Device Connect). See panorama_scan.py."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from reachy_mini_driver.panorama_direct import run_direct_panorama_scan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim", action="store_true", help="Simulated transport/media")
    parser.add_argument("--target", default="sim", help="REACHY_TARGET (host:port or sim)")
    parser.add_argument("--output", type=Path, default=Path("/tmp/reachy-panorama"))
    parser.add_argument("--yaw-steps", type=int, default=5)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--settle-s", type=float, default=0.35)
    parser.add_argument("--body-yaw-steps", type=int, default=0)
    parser.add_argument("--body-move-s", type=float, default=0.5)
    args = parser.parse_args()

    sim = args.sim or args.target.strip().lower() == "sim"
    code = asyncio.run(
        run_direct_panorama_scan(
            sim=sim,
            target=args.target,
            output=args.output,
            yaw_steps=max(2, args.yaw_steps),
            pitch=args.pitch,
            settle_s=args.settle_s,
            body_yaw_steps=args.body_yaw_steps,
            body_move_s=args.body_move_s,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
