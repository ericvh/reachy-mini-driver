"""Tests for Reachy app persisted settings."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from reachy_mini_driver.app_settings import (
    DeviceConnectAppSettings,
    load_app_settings,
    save_app_settings,
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


if __name__ == "__main__":
    unittest.main()
