from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from oled_app.hardware.ossila import safe_shutdown_smu, shutdown_smu_with_reconnect


class FakeSet:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def voltage(self, value, response=0):
        self.calls.append(("voltage", value, response))
        if self.fail:
            raise OSError("serial write failed")

    def enabled(self, value, response=0):
        self.calls.append(("enabled", value, response))
        if self.fail:
            raise OSError("serial write failed")


class FakeDevice:
    def __init__(self, fail: bool = False):
        self.channel1 = FakeSet(fail=fail)
        self.channel2 = FakeSet(fail=fail)
        self.smu1 = SimpleNamespace(set=self.channel1)
        self.smu2 = SimpleNamespace(set=self.channel2)
        self.closed = False

    def close(self):
        self.closed = True


class OssilaShutdownTests(unittest.TestCase):
    @patch("oled_app.hardware.ossila.time.sleep", return_value=None)
    def test_shutdown_succeeds_on_active_connection(self, _sleep) -> None:
        device = FakeDevice()

        result = safe_shutdown_smu(device)

        self.assertTrue(result)
        self.assertIn(("voltage", 0, 0), device.channel1.calls)
        self.assertIn(("enabled", False, 0), device.channel1.calls)
        self.assertIn(("voltage", 0, 0), device.channel2.calls)
        self.assertIn(("enabled", False, 0), device.channel2.calls)

    @patch("oled_app.hardware.ossila.time.sleep", return_value=None)
    def test_failed_connection_is_closed_and_reopened_for_emergency_shutdown(self, _sleep) -> None:
        broken = FakeDevice(fail=True)
        recovered = FakeDevice()
        opened_ports = []
        messages = []

        result = shutdown_smu_with_reconnect(
            broken,
            "COM3",
            log=messages.append,
            attempts=2,
            retry_delay_s=0,
            device_factory=lambda port: opened_ports.append(port) or recovered,
        )

        self.assertTrue(result)
        self.assertTrue(broken.closed)
        self.assertTrue(recovered.closed)
        self.assertEqual(opened_ports, ["COM3"])
        self.assertTrue(any("сброшены после переподключения" in message for message in messages))

    @patch("oled_app.hardware.ossila.time.sleep", return_value=None)
    def test_unrecoverable_shutdown_reports_critical_warning(self, _sleep) -> None:
        broken = FakeDevice(fail=True)
        recovered_devices = []
        messages = []

        def factory(_port):
            device = FakeDevice(fail=True)
            recovered_devices.append(device)
            return device

        result = shutdown_smu_with_reconnect(
            broken,
            "COM3",
            log=messages.append,
            attempts=2,
            retry_delay_s=0,
            device_factory=factory,
        )

        self.assertFalse(result)
        self.assertEqual(len(recovered_devices), 2)
        self.assertTrue(all(device.closed for device in recovered_devices))
        self.assertTrue(any("КРИТИЧЕСКИ" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
