"""Space entry: assistant check requires ``class ...(ReachyMiniApp)``."""

from __future__ import annotations

import logging
import threading

from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_driver.reachy_app import (
    SETTINGS_HOST,
    SETTINGS_PORT,
    register_device_connect_settings_routes,
    run_device_connect_app,
)

logger = logging.getLogger(__name__)


class ReachyMiniDeviceConnectSpace(ReachyMiniApp):
    """HF Space shell listing; implementation lives in ``reachy_mini_driver.reachy_app``."""

    custom_app_url: str | None = f"http://{SETTINGS_HOST}:{SETTINGS_PORT}"
    request_media_backend: str | None = "default"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless)
        register_device_connect_settings_routes(self.settings_app)

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        run_device_connect_app(reachy_mini, stop_event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ReachyMiniDeviceConnectSpace()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
