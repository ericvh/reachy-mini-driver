import unittest

from reachy_mini_driver.panorama_dc_client import (
    PanoramaDeviceConnectClient,
    resolve_mesh_settings,
    unwrap_invoke_response,
)
from reachy_mini_driver.panorama_scan import capture_panorama_scan


class PanoramaDcClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_scan_via_mock_invoke(self):
        calls: list[tuple[str, dict | None]] = []

        def fake_invoke(device_id, function, params, reasoning):
            calls.append((function, params))
            if function == "set_body_yaw":
                return {"success": True, "result": {"status": "accepted"}}
            if function == "look_at_world":
                return {"success": True, "result": {"status": "accepted"}}
            if function == "capture_video_frame":
                return {
                    "success": True,
                    "result": {
                        "status": "success",
                        "format": "jpeg",
                        "encoding": "jpeg",
                        "width": 320,
                        "height": 240,
                        "data_b64": "aGVsbG8=",  # not valid jpeg; stitch test separate
                    },
                }
            return {"success": False, "error": "unknown"}

        client = PanoramaDeviceConnectClient(
            device_id="reachy-mini-test",
            _invoke=fake_invoke,
            max_edge=320,
            quality=75,
        )
        scan = await capture_panorama_scan(
            client,
            yaw_steps=[0.0, 30.0],
            pitch_steps=[0.0],
            body_yaw_steps=[0.0],
            settle_s=0,
        )

        self.assertEqual(len(calls), 4)  # 2 look + 2 capture
        capture_calls = [params for fn, params in calls if fn == "capture_video_frame"]
        self.assertEqual(capture_calls[0]["max_edge"], 320)
        self.assertEqual(capture_calls[0]["quality"], 75)
        self.assertEqual(scan.success_count, 2)

    def test_unwrap_invoke_error(self):
        out = unwrap_invoke_response({"success": False, "error": "device offline"})
        self.assertEqual(out["status"], "error")

    def test_unwrap_invoke_result(self):
        out = unwrap_invoke_response(
            {"success": True, "result": {"status": "accepted", "target": {"yaw": 1}}}
        )
        self.assertEqual(out["status"], "accepted")

    def test_resolve_mesh_settings_reads_tenant_from_credentials(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {
                    "device_id": "portal-device-1",
                    "tenant": "erivan01",
                    "nats": {"urls": ["nats://portal.deviceconnect.dev:4222"]},
                },
                handle,
            )
            path = handle.name

        zone, device_id, urls = resolve_mesh_settings(credentials_file=path)
        self.assertEqual(zone, "erivan01")
        self.assertEqual(device_id, "portal-device-1")
        self.assertEqual(urls[0], "nats://portal.deviceconnect.dev:4222")


if __name__ == "__main__":
    unittest.main()
