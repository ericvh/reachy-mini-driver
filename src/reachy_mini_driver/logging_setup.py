"""Logging configuration for CLI and Reachy Mini app runs."""

from __future__ import annotations

import logging
import os

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DRIVER_LOGGER = "reachy_mini_driver"
_EDGE_LOGGER = "device_connect_edge"


def configure_driver_logging(level: int | None = None) -> None:
    """Ensure driver (and edge) loggers emit to stderr with a readable format.

    The Reachy dashboard app often configures only ``reachy_mini.app``; without
    this, ``reachy_mini_driver`` messages can be lost.
    """
    if level is None:
        level_name = os.environ.get("REACHY_MINI_DRIVER_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

    driver_log = logging.getLogger(_DRIVER_LOGGER)
    driver_log.setLevel(level)

    if not driver_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
        driver_log.addHandler(handler)
        driver_log.propagate = False

    edge_log = logging.getLogger(_EDGE_LOGGER)
    edge_log.setLevel(level)
    if not edge_log.handlers:
        edge_log.propagate = True
        if logging.getLogger().handlers:
            return
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
        edge_log.addHandler(handler)
        edge_log.propagate = False
