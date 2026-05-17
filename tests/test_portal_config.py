import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from reachy_mini_driver.config import (
    PORTAL_NATS_URL,
    apply_portal_config,
    find_portal_credentials_file,
    load_portal_credentials,
    resolve_portal_credentials_file,
)
from reachy_mini_driver.config import DriverConfig


class PortalConfigTests(unittest.TestCase):
    def test_load_portal_credentials_reads_device_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds_path = Path(tmp) / "device.creds.json"
            creds_path.write_text(
                json.dumps(
                    {
                        "device_id": "tenant-device-1",
                        "tenant": "tenant",
                        "nats": {
                            "urls": ["nats://portal.deviceconnect.dev:4222"],
                            "jwt": "test-jwt",
                            "nkey_seed": "test-seed",
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_portal_credentials(creds_path)

            self.assertEqual(loaded.device_id, "tenant-device-1")
            self.assertEqual(loaded.tenant, "tenant")
            self.assertEqual(loaded.messaging_urls, ("nats://portal.deviceconnect.dev:4222",))

    def test_find_portal_credentials_file_returns_newest_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            older = directory / "erivan01-old.creds.json"
            newer = directory / "erivan01-new.creds.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            old_time = time.time() - 100
            os.utime(older, (old_time, old_time))
            os.utime(newer, (time.time(), time.time()))

            found = find_portal_credentials_file(
                pattern="erivan01*.json",
                search_dir=directory,
            )

            self.assertEqual(found, newer)

    def test_resolve_portal_credentials_file_prefers_explicit_path(self):
        resolved = resolve_portal_credentials_file(
            explicit_path="/tmp/explicit.creds.json",
            portal=True,
            pattern="erivan01*.json",
            search_dir="/tmp",
        )

        self.assertEqual(resolved, "/tmp/explicit.creds.json")

    def test_apply_portal_config_uses_credentials_metadata(self):
        base = DriverConfig(portal=True)
        portal_credentials = load_portal_credentials(
            self._write_credentials(
                {
                    "device_id": "portal-device-1",
                    "tenant": "portal-tenant",
                    "nats": {"urls": ["nats://portal.deviceconnect.dev:4222"]},
                }
            )
        )

        applied = apply_portal_config(
            base,
            portal_credentials=portal_credentials,
            explicit_device_id=None,
            explicit_tenant=None,
        )

        self.assertEqual(applied.device_id, "portal-device-1")
        self.assertEqual(applied.tenant, "portal-tenant")
        self.assertEqual(applied.messaging_backend, "nats")
        self.assertEqual(applied.messaging_urls, ("nats://portal.deviceconnect.dev:4222",))
        self.assertEqual(applied.discovery_mode, "infra")

    def test_apply_portal_config_keeps_explicit_device_id(self):
        base = DriverConfig(portal=True, device_id="reachy-mini-1")
        portal_credentials = load_portal_credentials(
            self._write_credentials({"device_id": "portal-device-1", "tenant": "portal-tenant"})
        )

        applied = apply_portal_config(
            base,
            portal_credentials=portal_credentials,
            explicit_device_id="reachy-mini-1",
            explicit_tenant=None,
        )

        self.assertEqual(applied.device_id, "reachy-mini-1")

    def test_apply_portal_config_falls_back_to_default_portal_url(self):
        base = DriverConfig(portal=True)

        applied = apply_portal_config(
            base,
            portal_credentials=None,
            explicit_device_id=None,
            explicit_tenant=None,
        )

        self.assertEqual(applied.messaging_urls, (PORTAL_NATS_URL,))

    def _write_credentials(self, payload: dict) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".creds.json", delete=False)
        with handle:
            json.dump(payload, handle)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()
