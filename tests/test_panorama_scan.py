import io
import json
import tempfile
import unittest

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.media import SimMediaClient
from reachy_mini_driver.panorama_scan import (
    analyze_world_yaw_coverage,
    capture_panorama_scan,
    default_yaw_steps,
    recommended_steps_for_360,
    save_scan_artifacts,
    sort_frames_by_world_yaw,
    stitch_horizontal_jpeg,
    stitch_pitch_grid_jpeg,
    HOME_HEAD_PITCH_DEG,
    HOME_HEAD_YAW_DEG,
    PanoramaFrame,
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
            align_head_home_at_start=False,
            verify_body_pose=False,
        )
        await driver.disconnect()

        self.assertEqual(scan.success_count, 3)
        self.assertEqual(scan.coverage_yaw_deg, 60.0)
        self.assertTrue(all(frame.jpeg_bytes for frame in scan.frames))

    async def test_stitch_and_save_artifacts(self):
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())
        await driver.connect()
        scan = await capture_panorama_scan(
            driver,
            yaw_steps=[-20.0, 20.0],
            pitch_steps=[0.0],
            settle_s=0,
            align_head_home_at_start=False,
            verify_body_pose=False,
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

    async def test_capture_panorama_aligns_head_home_first(self):
        class RecordingDriver:
            def __init__(self) -> None:
                self.look_calls: list[dict] = []

            async def set_body_yaw(self, yaw_deg: float = 0.0, duration_s: float = 0.0, owner: str = "agent"):
                return {"status": "accepted"}

            async def look_at_world(
                self,
                pitch: float = 0.0,
                roll: float = 0.0,
                yaw: float = 0.0,
                x_mm: float = 0.0,
                y_mm: float = 0.0,
                z_mm: float = 0.0,
                owner: str = "agent",
            ):
                self.look_calls.append(
                    {"pitch": pitch, "roll": roll, "yaw": yaw, "owner": owner}
                )
                return {"status": "accepted"}

            async def capture_video_frame(self, encoding: str = "jpeg", max_edge=None, quality=None):
                return {
                    "status": "success",
                    "format": "jpeg",
                    "data_b64": "eJxiYGAEAAABAAE=",
                }

        driver = RecordingDriver()
        await capture_panorama_scan(
            driver,
            yaw_steps=[0.0],
            pitch_steps=[0.0],
            body_yaw_steps=[0.0],
            settle_s=0,
            align_head_home_at_start=True,
            home_settle_s=0,
        )
        self.assertGreaterEqual(len(driver.look_calls), 2)
        home = driver.look_calls[0]
        self.assertEqual(home["pitch"], HOME_HEAD_PITCH_DEG)
        self.assertEqual(home["yaw"], HOME_HEAD_YAW_DEG)
        self.assertEqual(home["owner"], "panorama")

    async def test_capture_panorama_skips_home_when_disabled(self):
        class RecordingDriver:
            def __init__(self) -> None:
                self.look_calls: list[dict] = []

            async def set_body_yaw(self, yaw_deg: float = 0.0, duration_s: float = 0.0, owner: str = "agent"):
                return {"status": "accepted"}

            async def look_at_world(self, pitch=0.0, roll=0.0, yaw=0.0, x_mm=0.0, y_mm=0.0, z_mm=0.0, owner="agent"):
                self.look_calls.append({"pitch": pitch, "yaw": yaw})
                return {"status": "accepted"}

            async def capture_video_frame(self, encoding: str = "jpeg", max_edge=None, quality=None):
                return {"status": "success", "format": "jpeg", "data_b64": "eJxiYGAEAAABAAE="}

        driver = RecordingDriver()
        await capture_panorama_scan(
            driver,
            yaw_steps=[10.0],
            pitch_steps=[0.0],
            body_yaw_steps=[0.0],
            settle_s=0,
            align_head_home_at_start=False,
        )
        self.assertEqual(len(driver.look_calls), 1)
        self.assertEqual(driver.look_calls[0]["yaw"], 10.0)

    def test_analyze_world_yaw_coverage_full_ring(self):
        frames = [
            PanoramaFrame(
                index=index,
                body_yaw_deg=yaw,
                pitch_deg=0.0,
                yaw_deg=0.0,
                look_result={"status": "accepted"},
                capture_result={"status": "success"},
                jpeg_bytes=b"x",
                world_yaw_deg=yaw,
            )
            for index, yaw in enumerate(i * 20.0 for i in range(18))
        ]
        coverage = analyze_world_yaw_coverage(frames)
        self.assertTrue(coverage["likely_full_360"])
        self.assertGreater(coverage["span_deg"], 300.0)

    def test_sort_frames_by_world_yaw(self):
        frames = [
            PanoramaFrame(0, 10.0, 0.0, 0.0, {}, {}, world_yaw_deg=10.0),
            PanoramaFrame(1, -50.0, 0.0, 0.0, {}, {}, world_yaw_deg=-50.0),
            PanoramaFrame(2, 90.0, 0.0, 0.0, {}, {}, world_yaw_deg=90.0),
        ]
        ordered = sort_frames_by_world_yaw(frames)
        self.assertEqual([frame.world_yaw_deg for frame in ordered], [-50.0, 10.0, 90.0])

    def test_recommended_steps_for_360(self):
        body, head = recommended_steps_for_360()
        self.assertGreaterEqual(body, 3)
        self.assertGreaterEqual(head, 3)

    async def test_body_drift_warning_when_head_moves_body(self):
        class DriftDriver:
            def __init__(self) -> None:
                self.body_yaw = 0.0

            async def set_body_yaw(self, yaw_deg: float = 0.0, duration_s: float = 0.0, owner: str = "agent"):
                self.body_yaw = yaw_deg
                return {"status": "accepted"}

            async def look_at_world(self, pitch=0.0, roll=0.0, yaw=0.0, x_mm=0.0, y_mm=0.0, z_mm=0.0, owner="agent"):
                if yaw > 20.0:
                    self.body_yaw += 10.0
                return {"status": "accepted"}

            async def get_body_yaw(self):
                return {"status": "success", "yaw_deg": self.body_yaw}

            async def capture_video_frame(self, encoding: str = "jpeg", max_edge=None, quality=None):
                return {"status": "success", "format": "jpeg", "data_b64": "eJxiYGAEAAABAAE="}

        scan = await capture_panorama_scan(
            DriftDriver(),
            yaw_steps=[0.0, 40.0],
            pitch_steps=[0.0],
            body_yaw_steps=[0.0],
            settle_s=0,
            align_head_home_at_start=False,
            verify_body_pose=True,
            body_drift_tolerance_deg=2.0,
        )
        self.assertTrue(scan.body_drift_warnings)

    def test_stitch_pitch_grid(self):
        from PIL import Image

        def frame(index: int, pitch: float, yaw: float) -> PanoramaFrame:
            buf = io.BytesIO()
            Image.new("RGB", (40, 20), (index * 20, 0, 0)).save(buf, format="JPEG")
            return PanoramaFrame(
                index=index,
                body_yaw_deg=yaw,
                pitch_deg=pitch,
                yaw_deg=0.0,
                look_result={"status": "accepted"},
                capture_result={"status": "success"},
                jpeg_bytes=buf.getvalue(),
                world_yaw_deg=yaw,
            )

        frames = [frame(0, -10.0, 0.0), frame(1, -10.0, 30.0), frame(2, 10.0, 0.0)]
        grid = stitch_pitch_grid_jpeg(frames)
        self.assertGreater(len(grid), 100)


if __name__ == "__main__":
    unittest.main()
