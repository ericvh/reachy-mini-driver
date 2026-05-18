import json
import tempfile
import unittest

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.panorama_scan import (
    capture_panorama_scan,
    default_yaw_steps,
    save_scan_artifacts,
    stitch_horizontal_jpeg,
    YAW_MAX_DEG,
    YAW_MIN_DEG,
)
from reachy_mini_driver.transport import SimReachyTransport


class PanoramaScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_panorama_scan_simulated(self):
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())
        await driver.connect()

        scan = await capture_panorama_scan(
            driver,
            yaw_steps=[-30.0, 0.0, 30.0],
            pitch_steps=[0.0],
            settle_s=0,
        )
        await driver.disconnect()

        self.assertEqual(scan.success_count, 3)
        self.assertEqual(scan.coverage_yaw_deg, 60.0)
        self.assertTrue(all(frame.jpeg_bytes for frame in scan.frames))

    async def test_stitch_and_save_artifacts(self):
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())
        await driver.connect()
        scan = await capture_panorama_scan(
            driver, yaw_steps=[-20.0, 20.0], pitch_steps=[0.0], settle_s=0
        )
        await driver.disconnect()

        strip = stitch_horizontal_jpeg(scan.frames)
        self.assertGreater(len(strip), 100)

        with tempfile.TemporaryDirectory() as tmp:
            paths = save_scan_artifacts(scan, tmp)
            self.assertIn("strip", paths)
            from pathlib import Path

            manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["success_count"], 2)
            self.assertIn("head_yaw_limit_deg", manifest)

    def test_default_yaw_steps_span_driver_limits(self):
        steps = default_yaw_steps(5)
        self.assertEqual(steps[0], YAW_MIN_DEG)
        self.assertEqual(steps[-1], YAW_MAX_DEG)


if __name__ == "__main__":
    unittest.main()
