#!/usr/bin/env python3
"""Panorama scan over Device Connect (same path as agents / MCP).

The robot driver must already be running and registered on the mesh, e.g.:

    DEVICE_CONNECT_ALLOW_INSECURE=true \\
    python -m reachy_mini_driver --target 192.168.2.156 --device-id reachy-mini-1

On your laptop (same broker / portal creds as the driver):

    pip install -e ".[media]"
    export MESSAGING_URLS=nats://…   # or your portal NATS URL + creds
  # export NATS_CREDENTIALS_FILE=…

    python examples/panorama_scan.py \\
      --device-id reachy-mini-1 \\
      --output ./panorama-out

Frames are JPEG snapshots returned by ``capture_video_frame`` over NATS RPC
(default ~640 px long edge). Stitching is local (off NATS). For higher
resolution later, see ``get_media_stream_access`` in the README.

Simulated mesh (local NATS + in-process driver) — see tests/smoke_broker_mcp_tools.py;
use ``examples/panorama_scan_direct.py --sim`` for driver-only bring-up without a broker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from reachy_mini_driver.panorama_dc_client import (
    PanoramaDeviceConnectClient,
    connect_mesh,
    disconnect_mesh,
    resolve_mesh_settings,
    wait_for_device,
)
from reachy_mini_driver.panorama_scan import (
    capture_panorama_scan,
    default_body_yaw_steps,
    default_pitch_steps,
    default_yaw_steps,
    recommended_steps_for_360,
    save_scan_artifacts,
)


async def run_scan(
    *,
    device_id: str | None,
    credentials_file: str | None,
    tenant: str | None,
    output: Path,
    yaw_steps: int,
    pitch_steps: int,
    settle_s: float,
    body_yaw_steps: int,
    body_move_s: float,
    max_edge: int | None,
    quality: int | None,
    encoding: str,
    wait_timeout_s: float,
    align_head_home_at_start: bool,
    home_settle_s: float | None,
    verify_body_pose: bool,
    body_drift_tolerance_deg: float,
) -> int:
    zone, resolved_id, _urls = resolve_mesh_settings(
        credentials_file=credentials_file,
        tenant=tenant,
        device_id=device_id,
    )
    if not resolved_id:
        raise SystemExit(
            "No device id. Pass --device-id or --credentials-file with a portal JSON "
            "that includes device_id."
        )
    zone = connect_mesh(credentials_file=credentials_file, tenant=tenant)
    try:
        device = await asyncio.to_thread(
            wait_for_device, resolved_id, timeout_s=wait_timeout_s
        )
        print(
            f"Device Connect: tenant={zone} device={device.get('device_id')} "
            f"({device.get('device_type')})"
        )

        client = PanoramaDeviceConnectClient(
            device_id=resolved_id,
            owner="panorama",
            encoding=encoding,
            max_edge=max_edge,
            quality=quality,
        )

        yaw_list = default_yaw_steps(yaw_steps)
        pitch_list = default_pitch_steps(pitch_steps)
        body_list = default_body_yaw_steps(body_yaw_steps) if body_yaw_steps > 1 else [0.0]
        total = len(body_list) * len(pitch_list) * len(yaw_list)
        print(
            f"Panorama scan via invoke_device: {len(body_list)} body "
            f"(≈±160°) × {len(pitch_list)} pitch (≈±30°) × {len(yaw_list)} yaw "
            f"(±45°) = {total} frames"
        )
        print(f"encoding={encoding} max_edge={max_edge} quality={quality}")
        if align_head_home_at_start:
            home_wait = home_settle_s if home_settle_s is not None else max(settle_s, 0.35)
            print(f"Head home alignment: pitch/roll/yaw=0° (settle {home_wait}s)")

        scan = await capture_panorama_scan(
            client,
            yaw_steps=yaw_list,
            pitch_steps=pitch_list,
            body_yaw_steps=body_list,
            body_move_duration_s=body_move_s,
            settle_s=settle_s,
            encoding=encoding,
            owner="panorama",
            align_head_home_at_start=align_head_home_at_start,
            home_settle_s=home_settle_s,
            verify_body_pose=verify_body_pose,
            body_drift_tolerance_deg=body_drift_tolerance_deg,
        )

        if scan.body_drift_warnings:
            print(
                f"Warning: {len(scan.body_drift_warnings)} body/head coupling event(s) "
                f"(see manifest body_drift_warnings)",
                file=sys.stderr,
            )
            for warning in scan.body_drift_warnings[:5]:
                print(f"  - {warning}", file=sys.stderr)
            if len(scan.body_drift_warnings) > 5:
                print("  - …", file=sys.stderr)

        manifest = scan.to_manifest()
        manifest["device_connect"] = {
            "tenant": zone,
            "device_id": resolved_id,
            "encoding": encoding,
            "max_edge": max_edge,
            "quality": quality,
            "resolution_note": (
                "JPEG RPC over NATS; increase max_edge/quality or use "
                "get_media_stream_access (WebRTC) for full-resolution video off-mesh."
            ),
        }

        paths = save_scan_artifacts(scan, str(output))
        manifest_path = Path(paths["manifest"])
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        print(json.dumps(manifest, indent=2))
        print(f"Saved {scan.success_count}/{len(scan.frames)} frames under {output}")
        strip_paths = [value for key, value in paths.items() if key.startswith("strip")]
        if "grid" in paths:
            print(f"Pitch grid: {paths['grid']}")
        for label, path in sorted(paths.items()):
            if label.startswith("strip"):
                print(f"Strip: {path}")
        coverage = manifest.get("world_yaw_coverage", {})
        if coverage:
            print(
                f"World yaw coverage (estimated): span≈{coverage.get('span_deg')}° "
                f"largest gap≈{coverage.get('largest_gap_deg')}° "
                f"full_360={coverage.get('likely_full_360')}"
            )
        if not strip_paths and "grid" not in paths:
            print("No strips written (no successful frames).", file=sys.stderr)
            return 1
        return 0
    finally:
        disconnect_mesh()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device-id",
        default=None,
        help="Device Connect device id (default: from --credentials-file or reachy-mini-1)",
    )
    parser.add_argument(
        "--credentials-file",
        default=None,
        help="Portal credentials JSON (sets NATS file, tenant, device_id if omitted)",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="Device Connect tenant zone (default: TENANT env or credentials file)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./panorama-out"),
        help="Directory for frames, manifest.json, panorama_strip.jpg",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for device to appear on the mesh",
    )
    parser.add_argument(
        "--yaw-steps",
        type=int,
        default=5,
        help="Head yaw samples across [-45, 45] degrees (max pan)",
    )
    parser.add_argument(
        "--pitch-steps",
        type=int,
        default=3,
        help="Head pitch samples: declination / level / inclination across [-30, 30]",
    )
    parser.add_argument(
        "--settle-s",
        type=float,
        default=0.5,
        help="Pause after each motion before capture (allows goto moves to finish)",
    )
    parser.add_argument(
        "--body-yaw-steps",
        type=int,
        default=5,
        help="Body yaw samples across full range (≈±160°); use 1 to keep body at 0°",
    )
    parser.add_argument(
        "--full-360",
        action="store_true",
        help="Pick body/head step counts for ~360° horizon coverage (overrides step counts)",
    )
    parser.add_argument(
        "--body-move-s",
        type=float,
        default=0.8,
        help="duration_s for each set_body_yaw (Device Connect goto)",
    )
    parser.add_argument(
        "--encoding",
        default="jpeg",
        choices=("jpeg", "thumbnail"),
        help="capture_video_frame encoding (NATS-safe)",
    )
    parser.add_argument(
        "--max-edge",
        type=int,
        default=640,
        help="Longest JPEG side in pixels (portal-safe default)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=82,
        help="JPEG quality 1–95",
    )
    parser.add_argument(
        "--no-align-head-home",
        action="store_true",
        help="Skip moving head to 0° pitch/roll/yaw before the sweep",
    )
    parser.add_argument(
        "--home-settle-s",
        type=float,
        default=None,
        help="Pause after head home (default: max(--settle-s, 0.35))",
    )
    parser.add_argument(
        "--no-verify-body",
        action="store_true",
        help="Skip get_body_yaw checks after moves (faster, no drift warnings)",
    )
    parser.add_argument(
        "--body-drift-tolerance-deg",
        type=float,
        default=3.0,
        help="Warn when observed body yaw shifts more than this after a head move",
    )
    args = parser.parse_args()

    yaw_steps = max(2, args.yaw_steps)
    pitch_steps = max(1, args.pitch_steps)
    body_yaw_steps = max(1, args.body_yaw_steps)
    if args.full_360:
        body_yaw_steps, yaw_steps = recommended_steps_for_360()
        print(f"--full-360: using body-yaw-steps={body_yaw_steps} yaw-steps={yaw_steps}")

    if args.credentials_file:
        os.environ.setdefault("NATS_CREDENTIALS_FILE", str(Path(args.credentials_file).expanduser()))

    try:
        code = asyncio.run(
            run_scan(
                device_id=args.device_id,
                credentials_file=args.credentials_file,
                tenant=args.tenant,
                output=args.output,
                yaw_steps=yaw_steps,
                pitch_steps=pitch_steps,
                settle_s=args.settle_s,
                body_yaw_steps=body_yaw_steps,
                body_move_s=args.body_move_s,
                max_edge=args.max_edge,
                quality=args.quality,
                encoding=args.encoding,
                wait_timeout_s=args.wait_timeout,
                align_head_home_at_start=not args.no_align_head_home,
                home_settle_s=args.home_settle_s,
                verify_body_pose=not args.no_verify_body,
                body_drift_tolerance_deg=args.body_drift_tolerance_deg,
            )
        )
    except TimeoutError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(code)


if __name__ == "__main__":
    main()
