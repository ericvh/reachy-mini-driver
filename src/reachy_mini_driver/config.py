"""Runtime configuration for the Reachy Mini driver."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

PORTAL_NATS_URL = "nats://portal.deviceconnect.dev:4222"
DEFAULT_PORTAL_CREDENTIALS_GLOB = "erivan01*.json"
DEFAULT_PORTAL_CREDENTIALS_DIR = Path.home() / "Downloads"


@dataclass(frozen=True)
class PortalCredentials:
    """Metadata loaded from a Device Connect portal credentials JSON file."""

    path: Path
    device_id: str | None = None
    tenant: str | None = None
    messaging_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class DriverConfig:
    """Environment-backed runtime configuration."""

    device_id: str = "reachy-mini-1"
    tenant: str = "default"
    target: str = "reachy-mini.local"
    host: str = "reachy-mini.local"
    api_port: int = 8000
    simulate: bool = False
    prefix: str = "reachy_mini"
    transport_mode: str = "auto"
    messaging_backend: str | None = None
    messaging_urls: tuple[str, ...] = ()
    nats_credentials_file: str | None = None
    allow_insecure: bool = False
    portal: bool = False
    portal_credentials_glob: str = DEFAULT_PORTAL_CREDENTIALS_GLOB
    portal_credentials_dir: str = str(DEFAULT_PORTAL_CREDENTIALS_DIR)
    discovery_mode: str | None = None

    @classmethod
    def from_env(cls) -> "DriverConfig":
        urls = tuple(
            url.strip()
            for url in os.environ.get("MESSAGING_URLS", os.environ.get("NATS_URL", "")).split(",")
            if url.strip()
        )
        allow_insecure = os.environ.get("DEVICE_CONNECT_ALLOW_INSECURE", "").lower()
        target = os.environ.get("REACHY_TARGET", os.environ.get("REACHY_HOST", "reachy-mini.local"))
        host, api_port, simulate = parse_reachy_target(
            target,
            default_port=int(os.environ.get("REACHY_PORT", "8000")),
        )
        simulate = simulate or _truthy(os.environ.get("REACHY_SIM", ""))
        simulate = simulate or _truthy(os.environ.get("REACHY_SIMULATE", ""))
        return cls(
            device_id=os.environ.get("DEVICE_ID", "reachy-mini-1"),
            tenant=os.environ.get("TENANT", "default"),
            target=target,
            host=host,
            api_port=api_port,
            simulate=simulate,
            prefix=os.environ.get("REACHY_PREFIX", "reachy_mini"),
            transport_mode=os.environ.get("REACHY_TRANSPORT_MODE", "auto"),
            messaging_backend=os.environ.get("MESSAGING_BACKEND") or None,
            messaging_urls=urls,
            nats_credentials_file=(
                os.environ.get("NATS_CREDENTIALS_FILE")
                or os.environ.get("PORTAL_CREDENTIALS_FILE")
                or None
            ),
            allow_insecure=allow_insecure in {"1", "true", "yes"},
            portal=_truthy(os.environ.get("DEVICE_CONNECT_PORTAL", os.environ.get("REACHY_PORTAL", ""))),
            portal_credentials_glob=os.environ.get(
                "PORTAL_CREDENTIALS_GLOB",
                DEFAULT_PORTAL_CREDENTIALS_GLOB,
            ),
            portal_credentials_dir=os.environ.get(
                "PORTAL_CREDENTIALS_DIR",
                str(DEFAULT_PORTAL_CREDENTIALS_DIR),
            ),
            discovery_mode=os.environ.get("DEVICE_CONNECT_DISCOVERY_MODE") or None,
        )


def parse_reachy_target(target: str, default_port: int = 8000) -> tuple[str, int, bool]:
    """Parse a target alias or host[:port] without requiring mDNS/Avahi."""
    normalized = target.strip()
    if normalized.lower() in {"sim", "simulate", "simulation", "simulated"}:
        return "simulated-reachy-mini", default_port, True
    if normalized.startswith("http://"):
        normalized = normalized.removeprefix("http://")
    elif normalized.startswith("https://"):
        normalized = normalized.removeprefix("https://")
    normalized = normalized.split("/", 1)[0]
    if normalized.count(":") == 1:
        host, port = normalized.rsplit(":", 1)
        return host or "reachy-mini.local", int(port), False
    return normalized or "reachy-mini.local", default_port, False


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def find_portal_credentials_file(
    *,
    pattern: str = DEFAULT_PORTAL_CREDENTIALS_GLOB,
    search_dir: Path | str | None = None,
) -> Path | None:
    """Return the newest portal credentials file matching *pattern* under *search_dir*."""
    directory = Path(search_dir or DEFAULT_PORTAL_CREDENTIALS_DIR).expanduser()
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def load_portal_credentials(path: Path | str) -> PortalCredentials:
    """Load portal device metadata from a Device Connect credentials JSON file."""
    creds_path = Path(path).expanduser()
    with creds_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"portal credentials file must contain a JSON object: {creds_path}")

    nats_config = data.get("nats", {})
    urls: tuple[str, ...] = ()
    if isinstance(nats_config, dict):
        raw_urls = nats_config.get("urls", ())
        if isinstance(raw_urls, str):
            urls = (raw_urls.strip(),) if raw_urls.strip() else ()
        elif isinstance(raw_urls, list):
            urls = tuple(url.strip() for url in raw_urls if isinstance(url, str) and url.strip())

    device_id = data.get("device_id")
    tenant = data.get("tenant")
    return PortalCredentials(
        path=creds_path,
        device_id=device_id if isinstance(device_id, str) and device_id else None,
        tenant=tenant if isinstance(tenant, str) and tenant else None,
        messaging_urls=urls,
    )


def resolve_portal_credentials_file(
    *,
    explicit_path: str | None,
    portal: bool,
    pattern: str,
    search_dir: str,
) -> str | None:
    """Resolve a credentials file path from an explicit value or portal auto-discovery."""
    if explicit_path:
        return explicit_path
    if not portal:
        return None
    discovered = find_portal_credentials_file(pattern=pattern, search_dir=search_dir)
    return str(discovered) if discovered is not None else None


def apply_portal_config(
    config: DriverConfig,
    *,
    portal_credentials: PortalCredentials | None,
    explicit_device_id: str | None,
    explicit_tenant: str | None,
) -> DriverConfig:
    """Apply portal defaults and credentials metadata to a driver config."""
    if not config.portal:
        return config

    messaging_backend = config.messaging_backend or "nats"
    messaging_urls = config.messaging_urls
    if not messaging_urls:
        if portal_credentials and portal_credentials.messaging_urls:
            messaging_urls = portal_credentials.messaging_urls
        else:
            messaging_urls = (PORTAL_NATS_URL,)

    device_id = explicit_device_id
    if device_id is None and portal_credentials and portal_credentials.device_id:
        device_id = portal_credentials.device_id

    tenant = explicit_tenant
    if tenant is None and portal_credentials and portal_credentials.tenant:
        tenant = portal_credentials.tenant

    discovery_mode = config.discovery_mode or "infra"
    return DriverConfig(
        device_id=device_id or config.device_id,
        tenant=tenant or config.tenant,
        target=config.target,
        host=config.host,
        api_port=config.api_port,
        simulate=config.simulate,
        prefix=config.prefix,
        transport_mode=config.transport_mode,
        messaging_backend=messaging_backend,
        messaging_urls=messaging_urls,
        nats_credentials_file=config.nats_credentials_file,
        allow_insecure=config.allow_insecure,
        portal=config.portal,
        portal_credentials_glob=config.portal_credentials_glob,
        portal_credentials_dir=config.portal_credentials_dir,
        discovery_mode=discovery_mode,
    )
