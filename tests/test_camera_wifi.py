from __future__ import annotations

import subprocess
import unittest

from oled_app.camera.wifi import (
    WifiConnectionSession,
    WindowsWifiController,
    parse_netsh_wlan_interfaces,
)
from oled_app.gui.camera_window import connect_camera_service_with_wifi


ENGLISH_LAB = b"""
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : Laboratory
    Profile                : Laboratory
"""

ENGLISH_PI = b"""
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : OLED-Camera
    Profile                : OLED-Camera
"""

RUSSIAN_PI = """
    Имя                   : Wi-Fi
    Состояние             : подключено
    SSID                  : OLED-Camera
    Профиль               : OLED-Camera
"""


class _SequencedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, arguments, **_kwargs):
        self.calls.append(list(arguments))
        if not self.outputs:
            raise AssertionError(f"unexpected command: {arguments}")
        return self.outputs.pop(0)


def _completed(stdout: bytes = b"", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=b"")


class WindowsWifiControllerTests(unittest.TestCase):
    def test_interface_parser_supports_russian_windows_output(self):
        interfaces = parse_netsh_wlan_interfaces(RUSSIAN_PI)

        self.assertEqual(len(interfaces), 1)
        self.assertEqual(interfaces[0].name, "Wi-Fi")
        self.assertTrue(interfaces[0].connected)
        self.assertEqual(interfaces[0].profile, "OLED-Camera")

    def test_connects_saved_profile_and_remembers_previous_network(self):
        runner = _SequencedRunner(
            [
                _completed(ENGLISH_LAB),
                _completed(b"Connection request was completed successfully."),
                _completed(ENGLISH_PI),
            ]
        )
        controller = WindowsWifiController(
            runner=runner,
            sleep=lambda _seconds: None,
            platform_name="nt",
        )

        session = controller.connect_saved_profile("OLED-Camera", timeout_s=5)

        self.assertTrue(session.switched)
        self.assertEqual(session.previous_profile, "Laboratory")
        self.assertEqual(session.interface_name, "Wi-Fi")
        self.assertEqual(
            runner.calls[1],
            [
                "netsh",
                "wlan",
                "connect",
                "name=OLED-Camera",
                "interface=Wi-Fi",
            ],
        )

    def test_does_not_reconnect_when_target_profile_is_already_active(self):
        runner = _SequencedRunner([_completed(ENGLISH_PI)])
        controller = WindowsWifiController(runner=runner, platform_name="nt")

        session = controller.connect_saved_profile("OLED-Camera")

        self.assertFalse(session.switched)
        self.assertEqual(len(runner.calls), 1)

    def test_restores_profile_active_before_camera(self):
        session = WifiConnectionSession(
            target_profile="OLED-Camera",
            interface_name="Wi-Fi",
            previous_profile="Laboratory",
            switched=True,
        )
        runner = _SequencedRunner(
            [
                _completed(ENGLISH_PI),
                _completed(b"Connection request was completed successfully."),
                _completed(ENGLISH_LAB),
            ]
        )
        controller = WindowsWifiController(
            runner=runner,
            sleep=lambda _seconds: None,
            platform_name="nt",
        )

        restored = controller.restore(session, timeout_s=5)

        self.assertTrue(restored)
        self.assertIn("name=Laboratory", runner.calls[1])


class CameraWifiWorkflowTests(unittest.TestCase):
    def test_camera_service_is_used_without_switch_when_already_reachable(self):
        class Client:
            def health(self):
                return {"success": True}

        class Controller:
            def connect_saved_profile(self, *_args, **_kwargs):
                raise AssertionError("Wi-Fi must not be switched")

        session = connect_camera_service_with_wifi(
            Client(),
            {
                "auto_connect_wifi": True,
                "wifi_profile": "OLED-Camera",
            },
            wifi_controller=Controller(),
        )

        self.assertIsNone(session)

    def test_camera_service_switches_wifi_after_direct_connection_failure(self):
        expected = WifiConnectionSession(
            target_profile="OLED-Camera",
            interface_name="Wi-Fi",
            previous_profile="Laboratory",
            switched=True,
        )

        class Client:
            def __init__(self):
                self.calls = 0

            def health(self):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("network unreachable")
                return {"success": True}

        class Controller:
            def connect_saved_profile(self, profile, **_kwargs):
                self.profile = profile
                return expected

        controller = Controller()
        session = connect_camera_service_with_wifi(
            Client(),
            {
                "auto_connect_wifi": True,
                "wifi_profile": "OLED-Camera",
                "wifi_connect_timeout_s": 5,
            },
            wifi_controller=controller,
        )

        self.assertEqual(controller.profile, "OLED-Camera")
        self.assertEqual(session, expected)


if __name__ == "__main__":
    unittest.main()
