"""Shared async startup for CLI and Reachy Mini app."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from argparse import Namespace
from dataclasses import dataclass

from device_connect_edge import DeviceRuntime

from reachy_mini_driver.app_settings import DeviceConnectAppSettings
from reachy_mini_driver.config import (
    DriverConfig,
    PortalCredentials,
    apply_portal_config,
    load_portal_credentials,
    parse_reachy_target,
    resolve_portal_credentials_file,
)
from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import NullMediaClient, SimMediaClient
from reachy_mini_driver.mhp_state import MhpStateStore
from reachy_mini_driver.transport import SimReachyTransport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceConnectRunParams:
    """Fully-resolved inputs for launching :class:`DeviceRuntime`."""

    driver_config: DriverConfig
    portal_credentials: PortalCredentials | None
    transport_mode_override: str | None
    no_media: bool


def gather_cli_run_params(args: Namespace) -> DeviceConnectRunParams:
    env = DriverConfig.from_env()
    portal = args.portal or env.portal
    messaging_urls = tuple(args.messaging_url) if args.messaging_url else env.messaging_urls
    allow_insecure = args.allow_insecure or env.allow_insecure
    target = args.target or args.host or env.target
    host, api_port, simulate = parse_reachy_target(target, args.api_port or env.api_port)
    if args.host is not None:
        host = args.host
    if args.api_port is not None:
        api_port = args.api_port
    simulate = args.sim or simulate or env.simulate
    credentials_file = resolve_portal_credentials_file(
        explicit_path=(
            args.portal_credentials or args.nats_credentials_file or env.nats_credentials_file
        ),
        portal=portal,
        pattern=args.portal_credentials_glob or env.portal_credentials_glob,
        search_dir=args.portal_credentials_dir or env.portal_credentials_dir,
    )
    if portal and not credentials_file:
        print(
            "Portal mode requires credentials. Provide --portal-credentials, "
            "--nats-credentials-file, NATS_CREDENTIALS_FILE, or place a matching "
            f"file under {args.portal_credentials_dir or env.portal_credentials_dir} "
            f"({args.portal_credentials_glob or env.portal_credentials_glob}).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    portal_credentials = None
    if credentials_file:
        portal_credentials = load_portal_credentials(credentials_file)

    config = DriverConfig(
        device_id=args.device_id or env.device_id,
        tenant=args.tenant or env.tenant,
        target="sim" if simulate else target,
        host=host,
        api_port=api_port,
        simulate=simulate,
        prefix=env.prefix,
        transport_mode=env.transport_mode,
        messaging_backend=args.messaging_backend or env.messaging_backend,
        messaging_urls=messaging_urls,
        nats_credentials_file=credentials_file,
        allow_insecure=allow_insecure,
        mhp_rig=args.mhp_rig or env.mhp_rig,
        portal=portal,
        portal_credentials_glob=args.portal_credentials_glob or env.portal_credentials_glob,
        portal_credentials_dir=args.portal_credentials_dir or env.portal_credentials_dir,
        discovery_mode=env.discovery_mode,
    )
    config = apply_portal_config(
        config,
        portal_credentials=portal_credentials,
        explicit_device_id=args.device_id,
        explicit_tenant=args.tenant,
    )
    return DeviceConnectRunParams(
        driver_config=config,
        portal_credentials=portal_credentials,
        transport_mode_override=args.transport_mode,
        no_media=args.no_media,
    )


def merge_app_settings_with_env(settings: DeviceConnectAppSettings) -> DeviceConnectRunParams:
    """Build run params for the dashboard app (JSON + env fallbacks)."""
    env = DriverConfig.from_env()

    tgt = settings.reachy_target.strip() or env.target
    host, api_port, sim_from_target = parse_reachy_target(tgt, env.api_port)
    simulate = settings.simulate or sim_from_target or env.simulate

    messaging_urls = tuple(
        u.strip() for u in settings.messaging_urls.split(",") if u.strip()
    ) or env.messaging_urls

    mb = settings.messaging_backend.strip() or (env.messaging_backend or "")
    messaging_backend = mb or None

    nc = settings.nats_credentials_file.strip() or (
        env.nats_credentials_file or ""
    ) or None

    portal = settings.use_portal or env.portal
    credentials_file = resolve_portal_credentials_file(
        explicit_path=nc,
        portal=portal,
        pattern=settings.portal_credentials_glob.strip() or env.portal_credentials_glob,
        search_dir=settings.portal_credentials_dir.strip() or env.portal_credentials_dir,
    )
    if portal and not credentials_file:
        raise ValueError(
            "Portal mode requires a credentials file path. "
            "Set it in the app settings or NATS_CREDENTIALS_FILE."
        )

    portal_credentials = None
    if credentials_file:
        portal_credentials = load_portal_credentials(credentials_file)

    config = DriverConfig(
        device_id=settings.device_id.strip() or env.device_id,
        tenant=settings.tenant.strip() or env.tenant,
        target="sim" if simulate else tgt,
        host=host,
        api_port=api_port,
        simulate=simulate,
        prefix=settings.reachy_prefix.strip() or env.prefix,
        transport_mode=settings.transport_mode.strip() or env.transport_mode,
        messaging_backend=messaging_backend or env.messaging_backend,
        messaging_urls=messaging_urls,
        nats_credentials_file=credentials_file,
        allow_insecure=settings.allow_insecure or env.allow_insecure,
        mhp_rig=settings.mhp_rig.strip() or env.mhp_rig,
        portal=portal,
        portal_credentials_glob=settings.portal_credentials_glob.strip()
        or env.portal_credentials_glob,
        portal_credentials_dir=settings.portal_credentials_dir.strip()
        or env.portal_credentials_dir,
        discovery_mode=settings.discovery_mode.strip() or env.discovery_mode,
    )
    config = apply_portal_config(
        config,
        portal_credentials=portal_credentials,
        explicit_device_id=settings.device_id.strip() or None,
        explicit_tenant=settings.tenant.strip() or None,
    )
    return DeviceConnectRunParams(
        driver_config=config,
        portal_credentials=portal_credentials,
        transport_mode_override=None,
        no_media=settings.no_media,
    )


async def _run_until_stopped(runtime: DeviceRuntime, stop_event: threading.Event) -> None:
    run_task = asyncio.create_task(runtime.run())
    try:
        while not stop_event.is_set():
            if run_task.done():
                await run_task
                return
            await asyncio.sleep(0.12)
        await runtime.stop()
        await run_task
    except Exception:
        if not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
        raise


async def run_device_connect(params: DeviceConnectRunParams, stop_event: threading.Event | None) -> None:
    """Start :class:`ReachyMiniDriver` and block until shutdown."""
    cfg = params.driver_config
    if cfg.discovery_mode:
        os.environ.setdefault("DEVICE_CONNECT_DISCOVERY_MODE", cfg.discovery_mode)

    media = None
    transport = None
    if cfg.simulate:
        transport = SimReachyTransport()
        media = SimMediaClient()
    if params.no_media:
        media = NullMediaClient("media disabled")

    tm = params.transport_mode_override or cfg.transport_mode

    driver = ReachyMiniDriver(
        host=cfg.host,
        api_port=cfg.api_port,
        transport=transport,
        transport_mode=tm,
        prefix=cfg.prefix,
        media=media,
        mhp_state=MhpStateStore(cfg.mhp_rig),
    )
    runtime = DeviceRuntime(
        driver=driver,
        device_id=cfg.device_id,
        tenant=cfg.tenant,
        messaging_backend=cfg.messaging_backend,
        messaging_urls=list(cfg.messaging_urls) or None,
        nats_credentials_file=cfg.nats_credentials_file,
        allow_insecure=cfg.allow_insecure,
    )

    logger.info(
        "Device Connect driver starting (device_id=%s tenant=%s target=%s:%s portal=%s)",
        cfg.device_id,
        cfg.tenant,
        cfg.host,
        cfg.api_port,
        cfg.portal,
    )

    if stop_event is None:
        await runtime.run()
    else:
        await _run_until_stopped(runtime, stop_event)
