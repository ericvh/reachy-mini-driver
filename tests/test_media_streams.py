import unittest

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.media_streams import (
    build_stream_access_info,
    is_loopback_host,
    local_ipc_camera_available,
)
from reachy_mini_driver.transport import NullReachyTransport, SimReachyTransport


class MediaStreamsTests(unittest.IsolatedAsyncioTestCase):
    def test_loopback_detection(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertFalse(is_loopback_host("192.168.2.156"))

    async def test_build_stream_access_remote_host_prefers_webrtc(self):
        transport = NullReachyTransport()

        async def api_call(path, method="GET", data=None):
            return await transport.api(path, method, data)

        info = await build_stream_access_info(
            host="192.168.2.156",
            api_port=8000,
            api_call=api_call,
        )

        self.assertEqual(info["status"], "success")
        self.assertEqual(info["recommended_backend"], "webrtc")
        self.assertFalse(info["backends"]["local"]["available"])
        self.assertIn("ws://192.168.2.156:8443", info["backends"]["webrtc"]["signaling_url"])

    async def test_release_and_acquire_media_hardware(self):
        transport = SimReachyTransport()
        driver = ReachyMiniDriver(transport=transport, media=SimMediaClient())

        released = await driver.release_media_hardware()
        access = await driver.get_media_stream_access()
        acquired = await driver.acquire_media_hardware()

        self.assertEqual(released["status"], "success")
        self.assertTrue(access["daemon_media"]["released"])
        self.assertEqual(access["recommended_backend"], "direct_hardware")
        self.assertEqual(acquired["status"], "success")
        self.assertFalse(transport.media_released)

    async def test_get_media_stream_access_simulated(self):
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())

        info = await driver.get_media_stream_access()

        self.assertEqual(info["target"], "simulated")
        self.assertIn("webrtc", info["backends"])
        self.assertEqual(info["daemon_api_url"], "http://reachy-mini.local:8000")


class MediaStreamsUnitTests(unittest.TestCase):
    def test_local_ipc_check_is_boolean(self):
        self.assertIsInstance(local_ipc_camera_available(), bool)


if __name__ == "__main__":
    unittest.main()
