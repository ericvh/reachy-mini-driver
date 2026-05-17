"""Helpers for the Device Connect dashboard settings web UI."""

from __future__ import annotations

import logging
import socket

SETTINGS_PORT = 8842


def _lan_ip() -> str | None:
    """Best-effort primary LAN address for log messages (not for binding)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    if ip and not ip.startswith("127."):
        return ip
    return None


def settings_page_urls(port: int = SETTINGS_PORT) -> list[str]:
    """URLs users can open for the configuration page (deduplicated, ordered)."""
    urls: list[str] = []
    lan = _lan_ip()
    if lan:
        urls.append(f"http://{lan}:{port}")
    urls.append(f"http://127.0.0.1:{port}")
    try:
        host = socket.gethostname()
        if host and host != "localhost":
            urls.append(f"http://{host}:{port}")
    except OSError:
        pass

    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def settings_page_log_message(port: int = SETTINGS_PORT) -> str:
    """Single log line pointing at the configuration UI."""
    urls = settings_page_urls(port)
    primary = urls[0]
    if len(urls) == 1:
        return f"Device Connect configuration UI: {primary}"
    also = ", ".join(urls[1:])
    return f"Device Connect configuration UI: {primary} (also {also})"


def log_settings_page(log: logging.Logger, port: int = SETTINGS_PORT) -> None:
    log.info(settings_page_log_message(port))
