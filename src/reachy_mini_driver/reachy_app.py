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

from fastapi import Body, FastAPI, File, UploadFile
from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_driver.app_settings import (
    DeviceConnectAppSettings,
    load_app_settings,
    save_app_settings,
    save_portal_credentials_upload,
)
from reachy_mini_driver.config import PortalCredentials, load_portal_credentials
from reachy_mini_driver.runtime_launcher import merge_app_settings_with_env, run_device_connect

logger = logging.getLogger(__name__)

SETTINGS_HOST = "0.0.0.0"
SETTINGS_PORT = 8842


def register_device_connect_settings_routes(settings_app: FastAPI | None) -> None:
    """Register ``/api/settings`` and related routes on the given FastAPI app."""

    if settings_app is None:
        return

    @settings_app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "reachy_mini_device_connect_settings"}

    @settings_app.get("/api/settings")
    def api_get_settings() -> dict:
        return load_app_settings().model_dump()

    @settings_app.put("/api/settings")
    def api_put_settings(body: DeviceConnectAppSettings) -> dict:
        save_app_settings(body)
        return {"saved": True, "settings": body.model_dump()}

    @settings_app.post("/api/validate")
    def api_validate(body: DeviceConnectAppSettings | None = Body(default=None)) -> dict:
        try:
            to_check = body or load_app_settings()
            merge_app_settings_with_env(to_check)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    @settings_app.post("/api/credentials/upload")
    async def api_upload_credentials(file: UploadFile = File(...)) -> dict:
        """Save portal credentials to disk and point settings at the new path."""
        if not file.filename:
            return {"ok": False, "error": "missing filename"}
        try:
            raw = await file.read()
            dest = save_portal_credentials_upload(file.filename, raw)
            settings = load_app_settings()
            settings = settings.model_copy(
                update={
                    "use_portal": True,
                    "nats_credentials_file": str(dest),
                }
            )
            try:
                meta = load_portal_credentials(dest)
            except (ValueError, OSError):
                meta = PortalCredentials(path=dest)
            if meta.device_id and not settings.device_id.strip():
                settings = settings.model_copy(update={"device_id": meta.device_id})
            if meta.tenant and not settings.tenant.strip():
                settings = settings.model_copy(update={"tenant": meta.tenant})
            save_app_settings(settings)
            merge_app_settings_with_env(settings)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("credentials upload failed")
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "path": str(dest),
            "device_id": meta.device_id,
            "tenant": meta.tenant,
            "settings": settings.model_dump(),
        }


def run_device_connect_app(reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
    """Run the Device Connect :class:`~device_connect_edge.DeviceRuntime` until *stop_event*."""
    _ = reachy_mini

    try:
        params = merge_app_settings_with_env(load_app_settings())
    except ValueError as exc:
        logger.error("Device Connect configuration error: %s", exc)
        raise

    asyncio.run(run_device_connect(params, stop_event))


class ReachyDeviceConnectApp(ReachyMiniApp):
    """Hosts the Device Connect :class:`~device_connect_edge.DeviceRuntime` on the robot."""

    custom_app_url: str | None = f"http://{SETTINGS_HOST}:{SETTINGS_PORT}"
    request_media_backend: str | None = "default"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless)
        register_device_connect_settings_routes(self.settings_app)

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        run_device_connect_app(reachy_mini, stop_event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ReachyDeviceConnectApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
