"""Persisted settings for the Reachy Mini Device Connect app (dashboard UI)."""

from __future__ import annotations

from pathlib import Path
import json

from pydantic import BaseModel, Field


DEFAULT_CONFIG_REL = Path(".config") / "reachy_mini_driver" / "device_connect_app.json"
CREDENTIALS_SUBDIR = "credentials"
ALLOWED_CREDENTIAL_SUFFIXES = frozenset({".json", ".creds"})
MAX_CREDENTIALS_UPLOAD_BYTES = 256 * 1024


class DeviceConnectAppSettings(BaseModel):
    """User-editable settings stored on the robot (or dev machine).

    Environment variables still override most keys at process start when using
    the CLI; the Reachy app reads this file unless overridden by env.
    """

    use_portal: bool = Field(
        default=False,
        description="Use Device Connect cloud portal (NATS + credentials file).",
    )
    nats_credentials_file: str = Field(
        default="",
        description="Path to portal credentials JSON or .creds on this machine.",
    )
    device_id: str = Field(default="", description="Leave empty to use credentials or env.")
    tenant: str = Field(default="", description="Leave empty to use credentials or env.")
    reachy_target: str = Field(
        default="127.0.0.1:8000",
        description="Reachy daemon host:port (use 127.0.0.1 on-robot).",
    )
    transport_mode: str = Field(
        default="websocket",
        description="Real-time transport: auto, websocket, zenoh, http.",
    )
    allow_insecure: bool = Field(
        default=False,
        description="DEVICE_CONNECT_ALLOW_INSECURE (dev only).",
    )
    simulate: bool = Field(default=False, description="Driver-level simulation target.")
    no_media: bool = Field(
        default=False,
        description="Disable SDK media (no camera/mic Device Connect functions).",
    )
    messaging_backend: str = Field(
        default="",
        description="When not using portal: nats, zenoh, or empty for default.",
    )
    messaging_urls: str = Field(
        default="",
        description="Comma-separated broker URLs when not using portal.",
    )
    discovery_mode: str = Field(
        default="",
        description="e.g. d2d, infra — maps to DEVICE_CONNECT_DISCOVERY_MODE when set.",
    )
    portal_credentials_glob: str = Field(default="")
    portal_credentials_dir: str = Field(default="")
    mhp_rig: str = Field(default="reachy_mini")
    reachy_prefix: str = Field(default="reachy_mini")


def default_config_path() -> Path:
    return Path.home() / DEFAULT_CONFIG_REL


def credentials_storage_dir() -> Path:
    """Directory for portal credential files uploaded from the settings UI."""
    directory = default_config_path().parent / CREDENTIALS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_portal_credentials_upload(filename: str, data: bytes) -> Path:
    """Write an uploaded portal credentials file and return its absolute path."""
    if len(data) > MAX_CREDENTIALS_UPLOAD_BYTES:
        raise ValueError(
            f"credentials file too large (max {MAX_CREDENTIALS_UPLOAD_BYTES // 1024} KiB)"
        )
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("invalid credentials filename")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_CREDENTIAL_SUFFIXES:
        raise ValueError("credentials file must be .json or .creds")
    dest = credentials_storage_dir() / safe_name
    dest.write_bytes(data)
    return dest.resolve()


def load_app_settings(path: Path | None = None) -> DeviceConnectAppSettings:
    p = path or default_config_path()
    if not p.is_file():
        return DeviceConnectAppSettings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return DeviceConnectAppSettings()
        return DeviceConnectAppSettings.model_validate(data)
    except Exception:
        return DeviceConnectAppSettings()


def save_app_settings(settings: DeviceConnectAppSettings, path: Path | None = None) -> None:
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(settings.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
