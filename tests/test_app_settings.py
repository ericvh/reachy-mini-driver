"""Tests for Reachy app persisted settings."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from reachy_mini_driver.app_settings import (
    DeviceConnectAppSettings,
    load_app_settings,
    save_app_settings,
    save_portal_credentials_upload,
)


class TestAppSettings(unittest.TestCase):
    def test_roundtrip_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            data = DeviceConnectAppSettings(
                use_portal=False,
                nats_credentials_file="/tmp/x.creds",
                reachy_target="192.168.1.5:8000",
                transport_mode="websocket",
                allow_insecure=True,
            )
            save_app_settings(data, path)
            loaded = load_app_settings(path)
            self.assertEqual(loaded.nats_credentials_file, "/tmp/x.creds")
            self.assertFalse(loaded.use_portal)
            self.assertTrue(loaded.allow_insecure)

    def test_credentials_upload_saves_file(self) -> None:
        with TemporaryDirectory() as tmp:
            import reachy_mini_driver.app_settings as app_settings_mod

            config_dir = Path(tmp) / "cfg"
            config_dir.mkdir()
            creds_dir = config_dir / "credentials"
            original_default = app_settings_mod.default_config_path

            def _config_path() -> Path:
                return config_dir / "device_connect_app.json"

            app_settings_mod.default_config_path = _config_path  # type: ignore[assignment]
            try:
                dest = save_portal_credentials_upload(
                    "../../evil.json",
                    b'{"device_id":"d1","tenant":"t1","nats":{"urls":["nats://x"]}}',
                )
                self.assertEqual(dest.name, "evil.json")
                self.assertTrue(dest.is_relative_to(creds_dir.resolve()))
            finally:
                app_settings_mod.default_config_path = original_default  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
