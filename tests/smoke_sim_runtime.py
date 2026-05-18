"""Smoke-test the Reachy Mini simulated target through Device Connect shapes.

This does not start the full Device Connect messaging loop or MCP bridge. It
constructs DeviceRuntime around the simulated driver, verifies generated
function/event schemas, invokes representative functions through the
DeviceDriver invocation path, and checks that simulated events are emitted.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from device_connect_edge import DeviceRuntime

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.transport import SimReachyTransport


REQUIRED_FUNCTIONS = {
    "get_status",
    "get_joints",
    "get_imu",
    "look_at_world",
    "get_body_yaw",
    "set_body_yaw",
    "antenna_pose",
    "goto_sleep",
    "wake_up",
    "stop_motion",
    "get_media_status",
    "detect_audio_activity",
    "detect_motion",
    "capture_video_frame",
    "push_video_frame",
    "start_audio_input",
    "stop_audio_input",
    "capture_audio_sample",
    "start_audio_output",
    "stop_audio_output",
    "play_audio_file",
    "push_audio_sample",
}

REQUIRED_EVENTS = {"audio_event", "motion_event", "safety_event"}


async def main() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    driver = ReachyMiniDriver(
        transport=SimReachyTransport(),
        media=SimMediaClient(),
    )
    runtime = DeviceRuntime(
        driver=driver,
        device_id="reachy-mini-sim-smoke",
        tenant="smoke",
        allow_insecure=True,
    )
    driver.set_event_callback(lambda name, payload: events.append((name, payload)))

    function_names = {func.name for func in runtime.capabilities.functions}
    event_names = {event.name for event in runtime.capabilities.events}
    missing_functions = sorted(REQUIRED_FUNCTIONS - function_names)
    missing_events = sorted(REQUIRED_EVENTS - event_names)
    if missing_functions or missing_events:
        raise AssertionError(
            {
                "missing_functions": missing_functions,
                "missing_events": missing_events,
            }
        )

    await driver.connect()
    status = await driver.invoke("get_status")
    media_status = await driver.invoke("get_media_status")
    await driver.invoke("antenna_pose", left=10, right=-10, owner="smoke")
    await driver.invoke("goto_sleep", owner="smoke")
    sleeping = await driver.invoke("get_status")
    await driver.invoke("wake_up", owner="smoke")
    awake = await driver.invoke("get_status")
    audio = await driver.invoke("detect_audio_activity", threshold=0.05)
    await driver.invoke("detect_motion", threshold=0.02)
    motion = await driver.invoke("detect_motion", threshold=0.02)
    await driver.disconnect()

    if status["daemon"]["target"] != "simulated":
        raise AssertionError("simulated daemon status was not reported")
    if media_status["target"] != "simulated":
        raise AssertionError("simulated media status was not reported")
    if not sleeping["daemon"]["sleeping"]:
        raise AssertionError("simulated goto_sleep did not set sleeping=true")
    if awake["daemon"]["sleeping"]:
        raise AssertionError("simulated wake_up did not set sleeping=false")
    if audio["event"]["kind"] != "audio_activity_detected":
        raise AssertionError("simulated audio activity was not detected")
    if motion["event"]["kind"] != "motion_detected":
        raise AssertionError("simulated motion was not detected")
    if {name for name, _ in events} != {"audio_event", "motion_event"}:
        raise AssertionError(f"unexpected emitted events: {events!r}")

    print(
        json.dumps(
            {
                "status": "ok",
                "device_id": runtime.device_id,
                "functions": sorted(function_names),
                "events": sorted(event_names),
                "emitted_events": [name for name, _ in events],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
