"""FastAPI settings UI routes (no Reachy Mini SDK dependency).

Used by :mod:`reachy_mini_driver.reachy_app` and tests without importing ``reachy_mini``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reachy_mini_driver.app_settings import (
    DeviceConnectAppSettings,
    load_app_settings,
    save_app_settings,
    save_portal_credentials_upload,
)
from reachy_mini_driver.config import PortalCredentials, load_portal_credentials
from reachy_mini_driver.runtime_launcher import merge_app_settings_with_env

logger = logging.getLogger(__name__)

SETTINGS_HOST = "0.0.0.0"


def settings_static_dir() -> Path:
    """Directory containing ``index.html`` for the dashboard settings UI."""
    return Path(__file__).resolve().parent / "static"


def _settings_route_paths(settings_app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in settings_app.routes}


def mount_settings_ui(settings_app: FastAPI | None) -> None:
    """Serve ``/`` and ``/static`` from ``reachy_mini_driver/static`` (packaged data)."""
    if settings_app is None:
        return

    static_dir = settings_static_dir()
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        logger.error("Settings UI missing at %s — / will not be available", index_file)
        return

    paths = _settings_route_paths(settings_app)
    if "/static" not in paths:
        settings_app.mount(
            "/static",
            StaticFiles(directory=static_dir),
            name="reachy_mini_driver_static",
        )

    if "/" not in paths:

        @settings_app.get("/")
        async def settings_index() -> FileResponse:
            return FileResponse(index_file)


def register_device_connect_settings_routes(settings_app: FastAPI | None) -> None:
    """Register ``/api/settings`` and related routes on the given FastAPI app."""
    if settings_app is None:
        return

    mount_settings_ui(settings_app)

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
