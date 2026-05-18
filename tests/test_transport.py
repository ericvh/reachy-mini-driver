import json
import unittest

from reachy_mini_driver.transport import (
    ReachyHardwareTransport,
    WebSocketReachyTransport,
    _WS_CMD_MAP,
)


class TransportTests(unittest.TestCase):
    def test_websocket_head_pose_message_shape(self):
        command = {
            "head_pose": [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ]
        }
        message = _WS_CMD_MAP["head_pose"](command)
        self.assertEqual(message["type"], "set_target")
        self.assertEqual(len(message["head"]), 16)

    def test_websocket_body_yaw_message_shape(self):
        command = {"body_yaw": 0.5}
        message = _WS_CMD_MAP["body_yaw"](command)
        self.assertEqual(message["type"], "set_body_yaw")
        self.assertEqual(message["body_yaw"], 0.5)

    def test_websocket_antenna_message_shape(self):
        command = {"antennas_joint_positions": [0.1, -0.2]}
        message = _WS_CMD_MAP["antennas_joint_positions"](command)
        self.assertEqual(message["type"], "set_antennas")
        self.assertEqual(message["antennas"], [0.1, -0.2])

    def test_hardware_transport_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            ReachyHardwareTransport("reachy-mini.local", 8000, mode="invalid")


if __name__ == "__main__":
    unittest.main()
