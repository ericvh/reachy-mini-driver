"""Tests for settings web UI route registration."""

from __future__ import annotations

import unittest

from fastapi import FastAPI

from reachy_mini_driver.reachy_app import (
    ReachyDeviceConnectApp,
    mount_settings_ui,
    register_device_connect_settings_routes,
    settings_static_dir,
)


class TestSettingsUiRoutes(unittest.TestCase):
    def test_static_dir_has_index(self) -> None:
        self.assertTrue((settings_static_dir() / "index.html").is_file())

    def test_mount_settings_ui_registers_root(self) -> None:
        app = FastAPI()
        mount_settings_ui(app)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/", paths)
        self.assertIn("/static", paths)

    def test_app_entry_serves_root(self) -> None:
        space = ReachyDeviceConnectApp()
        assert space.settings_app is not None
        paths = {getattr(route, "path", "") for route in space.settings_app.routes}
        self.assertIn("/", paths)
        self.assertIn("/api/settings", paths)

    def test_register_is_idempotent(self) -> None:
        app = FastAPI()
        register_device_connect_settings_routes(app)
        register_device_connect_settings_routes(app)
        root_routes = [r for r in app.routes if getattr(r, "path", "") == "/"]
        self.assertEqual(len(root_routes), 1)


if __name__ == "__main__":
    unittest.main()
