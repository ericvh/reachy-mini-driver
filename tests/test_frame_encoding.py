"""Tests for video frame encoding and NATS payload limits."""

from __future__ import annotations

import base64
import unittest

import numpy as np

from reachy_mini_driver.frame_encoding import (
    DEFAULT_MAX_RPC_PAYLOAD_BYTES,
    encode_from_media_result,
    encode_video_frame_payload,
)


class TestFrameEncoding(unittest.TestCase):
    def test_jpeg_encoding_stays_under_limit(self) -> None:
        array = np.zeros((720, 1280, 3), dtype=np.uint8)
        payload = encode_video_frame_payload(array, encoding="jpeg", max_edge=320)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["encoding"], "jpeg")
        self.assertLessEqual(payload["rpc_json_bytes"], DEFAULT_MAX_RPC_PAYLOAD_BYTES)

    def test_thumbnail_preset_smaller_than_full_jpeg(self) -> None:
        array = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        thumb = encode_video_frame_payload(array, encoding="thumbnail")
        full = encode_video_frame_payload(array, encoding="jpeg")
        self.assertEqual(thumb["status"], "success")
        self.assertEqual(full["status"], "success")
        self.assertLess(thumb["byte_size"], full["byte_size"])

    def test_raw_rejected_when_over_limit(self) -> None:
        array = np.zeros((600, 800, 3), dtype=np.uint8)
        payload = encode_video_frame_payload(
            array, encoding="raw", max_payload_bytes=10_000, allow_oversized_raw=False
        )
        self.assertEqual(payload["status"], "error")
        self.assertIn("too large", payload["reason"])

    def test_raw_allowed_with_warning_when_oversized(self) -> None:
        array = np.zeros((100, 100, 3), dtype=np.uint8)
        payload = encode_video_frame_payload(
            array, encoding="raw", max_payload_bytes=10_000, allow_oversized_raw=True
        )
        self.assertEqual(payload["status"], "success")
        self.assertIn("nats_payload_warning", payload)

    def test_encode_from_media_result(self) -> None:
        array = np.full((64, 64, 3), 128, dtype=np.uint8)
        media_result = {
            "status": "success",
            "dtype": "uint8",
            "shape": [64, 64, 3],
            "data_b64": base64.b64encode(array.tobytes()).decode("ascii"),
        }
        encoded = encode_from_media_result(media_result, encoding="jpeg")
        self.assertEqual(encoded["status"], "success")
        self.assertEqual(encoded["format"], "jpeg")


if __name__ == "__main__":
    unittest.main()
