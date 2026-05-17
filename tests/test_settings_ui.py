"""Tests for settings UI URL helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from reachy_mini_driver.settings_ui import settings_page_log_message, settings_page_urls


class TestSettingsUi(unittest.TestCase):
    def test_urls_include_localhost(self) -> None:
        with patch("reachy_mini_driver.settings_ui._lan_ip", return_value=None):
            urls = settings_page_urls(port=8842)
        self.assertIn("http://127.0.0.1:8842", urls)

    def test_urls_prefer_lan_ip(self) -> None:
        with patch("reachy_mini_driver.settings_ui._lan_ip", return_value="192.168.2.156"):
            urls = settings_page_urls(port=8842)
        self.assertEqual(urls[0], "http://192.168.2.156:8842")

    def test_log_message(self) -> None:
        with patch(
            "reachy_mini_driver.settings_ui.settings_page_urls",
            return_value=["http://192.168.2.156:8842", "http://127.0.0.1:8842"],
        ):
            msg = settings_page_log_message()
        self.assertIn("http://192.168.2.156:8842", msg)
        self.assertIn("also http://127.0.0.1:8842", msg)


if __name__ == "__main__":
    unittest.main()
