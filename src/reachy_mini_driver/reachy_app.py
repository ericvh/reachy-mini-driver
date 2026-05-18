"""Reachy Mini App Store entry: Device Connect driver with local settings UI.

Install: ``pip install 'reachy-mini-driver[app]'`` (add ``[media]`` for SDK camera/mic).

The dashboard serves a small FastAPI UI on ``http://<robot-ip>:8842`` while the
driver runs. Settings are persisted under ``~/.config/reachy_mini_driver/device_connect_app.json``.
Environment variables still override defaults (see README).
"""

from __future__ import annotations

import asyncio
import logging
import threading

from pathlib import Path

from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_driver.app_settings import default_config_path, load_app_settings
from reachy_mini_driver.logging_setup import configure_driver_logging
from reachy_mini_driver.runtime_launcher import (
    log_run_config,
    merge_app_settings_with_env,
    run_device_connect,
)
from reachy_mini_driver.settings_routes import (
    SETTINGS_HOST,
    mount_settings_ui,
    register_device_connect_settings_routes,
    settings_static_dir,
)
from reachy_mini_driver.settings_ui import SETTINGS_PORT, log_settings_page

logger = logging.getLogger(__name__)

__all__ = [
    "SETTINGS_HOST",
    "SETTINGS_PORT",
    "ReachyDeviceConnectApp",
    "mount_settings_ui",
    "register_device_connect_settings_routes",
    "run_device_connect_app",
    "settings_static_dir",
]


def run_device_connect_app(reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
    """Run the Device Connect :class:`~device_connect_edge.DeviceRuntime` until *stop_event*."""
    configure_driver_logging()
    logger.info("Device Connect app run() invoked (Reachy Mini connected)")
    settings_path = default_config_path()
    logger.info("Loading settings from %s", settings_path)

    try:
        settings = load_app_settings()
        params = merge_app_settings_with_env(settings)
    except ValueError as exc:
        logger.error("Device Connect configuration error: %s", exc)
        raise

    log_run_config(params)
    asyncio.run(run_device_connect(params, stop_event))


class ReachyDeviceConnectApp(ReachyMiniApp):
    """Hosts the Device Connect :class:`~device_connect_edge.DeviceRuntime` on the robot."""

    custom_app_url: str | None = f"http://{SETTINGS_HOST}:{SETTINGS_PORT}"
    request_media_backend: str | None = "default"

    def _get_instance_path(self) -> Path:
        """Always use packaged driver static files, not the HF Space wrapper module."""
        return Path(__file__).resolve()

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless)
        register_device_connect_settings_routes(self.settings_app)

    def wrapped_run(self, *args: object, **kwargs: object) -> None:
        configure_driver_logging()
        self.logger.info("Reachy Mini Device Connect app starting")
        if self.settings_app is not None:
            log_settings_page(self.logger)
        super().wrapped_run(*args, **kwargs)

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        self.logger.info("Entering Device Connect driver main loop")
        try:
            run_device_connect_app(reachy_mini, stop_event)
        except Exception:
            self.logger.exception("Device Connect app stopped due to an error")
            raise
        self.logger.info("Device Connect app run() finished")


if __name__ == "__main__":
    configure_driver_logging()
    app = ReachyDeviceConnectApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
