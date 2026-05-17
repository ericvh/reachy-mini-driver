import unittest

from reachy_mini_driver.device_connect import ReachyMiniDriver
from reachy_mini_driver.config import parse_reachy_target
from reachy_mini_driver.media import NullMediaClient, SimMediaClient
from reachy_mini_driver.transport import NullReachyTransport, SimReachyTransport


class FakeMediaClient:
    def __init__(self):
        self.played = []
        self.pushed = []

    def status(self):
        return {
            "status": "available",
            "video_input": True,
            "audio_input": True,
            "audio_output": True,
        }

    def get_video_frame(self, encoding="raw"):
        return {
            "status": "success",
            "kind": "video_frame",
            "encoding": encoding,
            "data_b64": "AA==",
        }

    def push_video_frame(self, data_b64, width, height, channels=3, dtype="uint8"):
        return {
            "status": "accepted",
            "width": width,
            "height": height,
            "channels": channels,
            "dtype": dtype,
        }

    def start_audio_input(self):
        return {"status": "started"}

    def stop_audio_input(self):
        return {"status": "stopped"}

    def get_audio_sample(self, encoding="float32"):
        return {
            "status": "success",
            "kind": "audio_sample",
            "encoding": encoding,
            "data_b64": "AA==",
        }

    def start_audio_output(self):
        return {"status": "started"}

    def stop_audio_output(self):
        return {"status": "stopped"}

    def play_audio_file(self, sound_file):
        self.played.append(sound_file)
        return {"status": "accepted", "sound_file": sound_file}

    def push_audio_sample(self, data_b64, sample_rate, channels, dtype="float32"):
        self.pushed.append((data_b64, sample_rate, channels, dtype))
        return {"status": "accepted", "frames": 1}


class ReachyMiniDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_look_at_world_writes_bounded_command(self):
        transport = NullReachyTransport()
        driver = ReachyMiniDriver(transport=transport)
        await driver.connect()

        result = await driver.look_at_world(pitch=5, yaw=10, owner="test")

        self.assertEqual(result["status"], "accepted")
        self.assertTrue(transport.commands)
        self.assertIn("head_pose", transport.commands[-1])
        self.assertEqual(driver.mhp.snapshot()["command_owner"], "test")

    async def test_look_at_world_rejects_out_of_range_command(self):
        transport = NullReachyTransport()
        driver = ReachyMiniDriver(transport=transport)

        with self.assertRaises(ValueError):
            await driver.look_at_world(yaw=90)

        self.assertEqual(transport.commands, [])

    async def test_goto_sleep_uses_daemon_api(self):
        transport = NullReachyTransport()
        driver = ReachyMiniDriver(transport=transport)

        result = await driver.goto_sleep(owner="test")

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(transport.api_calls[-1], ("/api/move/play/goto_sleep", "POST", None))

    async def test_wake_up_uses_daemon_api(self):
        transport = NullReachyTransport()
        driver = ReachyMiniDriver(transport=transport)

        result = await driver.wake_up(owner="test")

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(transport.api_calls[-1], ("/api/move/play/wake_up", "POST", None))

    async def test_media_status_updates_mhp_state(self):
        driver = ReachyMiniDriver(transport=NullReachyTransport(), media=FakeMediaClient())

        result = await driver.get_media_status()

        self.assertEqual(result["status"], "available")
        snapshot = driver.mhp.snapshot()
        self.assertTrue(snapshot["media_ready"])
        self.assertEqual(snapshot["video_input_state"], "available")

    async def test_capture_video_frame_uses_media_client(self):
        driver = ReachyMiniDriver(transport=NullReachyTransport(), media=FakeMediaClient())

        result = await driver.capture_video_frame()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["kind"], "video_frame")

    async def test_push_video_frame_uses_media_client(self):
        driver = ReachyMiniDriver(transport=NullReachyTransport(), media=FakeMediaClient())

        result = await driver.push_video_frame("AA==", width=1, height=1, channels=1)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["width"], 1)

    async def test_play_audio_file_uses_media_client(self):
        media = FakeMediaClient()
        driver = ReachyMiniDriver(transport=NullReachyTransport(), media=media)

        result = await driver.play_audio_file("/tmp/test.wav")

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(media.played, ["/tmp/test.wav"])

    async def test_null_media_returns_explicit_unsupported(self):
        driver = ReachyMiniDriver(
            transport=NullReachyTransport(),
            media=NullMediaClient("no media in test"),
        )

        result = await driver.capture_audio_sample()

        self.assertEqual(result["status"], "unsupported")

    async def test_simulated_transport_reports_status_and_state(self):
        transport = SimReachyTransport()
        driver = ReachyMiniDriver(transport=transport, media=SimMediaClient())
        await driver.connect()

        await driver.antenna_pose(left=10, right=-10, owner="sim-test")
        status = await driver.get_status()
        joints = await driver.get_joints()
        imu = await driver.get_imu()

        self.assertEqual(status["daemon"]["target"], "simulated")
        self.assertEqual(status["mhp"]["command_owner"], "sim-test")
        self.assertEqual(joints["antenna_degrees"], [10.0, -10.0])
        self.assertTrue(imu["imu"]["simulated"])

    async def test_simulated_sleep_and_wake_toggle_status(self):
        transport = SimReachyTransport()
        driver = ReachyMiniDriver(transport=transport, media=SimMediaClient())

        await driver.goto_sleep(owner="sim-test")
        sleeping = await driver.get_status()
        await driver.wake_up(owner="sim-test")
        awake = await driver.get_status()

        self.assertTrue(sleeping["daemon"]["sleeping"])
        self.assertFalse(awake["daemon"]["sleeping"])

    async def test_simulated_media_accepts_input_and_output(self):
        media = SimMediaClient()
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=media)

        status = await driver.get_media_status()
        frame = await driver.capture_video_frame()
        pushed = await driver.push_video_frame("AA==", width=1, height=1, channels=1)
        audio = await driver.capture_audio_sample()
        played = await driver.play_audio_file("/tmp/sim.wav")

        self.assertEqual(status["target"], "simulated")
        self.assertEqual(frame["status"], "success")
        self.assertEqual(pushed["status"], "accepted")
        self.assertEqual(audio["kind"], "audio_sample")
        self.assertEqual(played["sound_file"], "/tmp/sim.wav")

    async def test_detect_audio_activity_emits_event_and_updates_state(self):
        events = []
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())
        driver.set_event_callback(lambda name, payload: events.append((name, payload)))

        result = await driver.detect_audio_activity(threshold=0.05)

        self.assertEqual(result["event"]["kind"], "audio_activity_detected")
        self.assertEqual(events[-1][0], "audio_event")
        self.assertEqual(events[-1][1]["state"], "active")
        self.assertTrue(driver.mhp.snapshot()["audio_activity"])

    async def test_detect_motion_emits_event_after_baseline_frame(self):
        events = []
        driver = ReachyMiniDriver(transport=SimReachyTransport(), media=SimMediaClient())
        driver.set_event_callback(lambda name, payload: events.append((name, payload)))

        baseline = await driver.detect_motion(threshold=0.02)
        detected = await driver.detect_motion(threshold=0.02)

        self.assertEqual(baseline["event"]["kind"], "motion_ended")
        self.assertEqual(detected["event"]["kind"], "motion_detected")
        self.assertEqual(events[-1][0], "motion_event")
        self.assertEqual(driver.mhp.snapshot()["video_motion_state"], "active")

    def test_parse_reachy_target_supports_sim_and_host_port(self):
        self.assertEqual(parse_reachy_target("sim"), ("simulated-reachy-mini", 8000, True))
        self.assertEqual(parse_reachy_target("192.168.4.12:8010"), ("192.168.4.12", 8010, False))
        self.assertEqual(
            parse_reachy_target("http://reachy-mini.local:8000"),
            ("reachy-mini.local", 8000, False),
        )


if __name__ == "__main__":
    unittest.main()
