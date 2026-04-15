from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SRC))

from lidguard import config as config_module
from lidguard import platform_macos


class MacOSHotspotTests(unittest.TestCase):
    def test_current_network_status_treats_ip_as_associated_without_ssid(self) -> None:
        config = config_module.default_config()
        with mock.patch("lidguard.platform_macos.current_wifi_ssid", return_value=None), mock.patch(
            "lidguard.platform_macos.current_ip_address",
            return_value="172.20.10.2",
        ):
            status = platform_macos.current_network_status(config)

        self.assertTrue(status.associated)
        self.assertEqual(status.ip_address, "172.20.10.2")

    def test_maybe_connect_hotspot_leaves_existing_wifi_alone(self) -> None:
        config = config_module.default_config()
        config["hotspot"]["enabled"] = True
        config["hotspot"]["ssid"] = "Phone"

        with mock.patch("lidguard.platform_macos.current_wifi_ssid", return_value="Office"), mock.patch(
            "lidguard.platform_macos.connect_hotspot",
        ) as connect_mock:
            result = platform_macos.maybe_connect_hotspot(config, reason="Wi-Fi disconnected")

        self.assertTrue(result)
        connect_mock.assert_not_called()

    def test_maybe_connect_hotspot_uses_hotspot_when_disconnected(self) -> None:
        config = config_module.default_config()
        config["hotspot"]["enabled"] = True
        config["hotspot"]["ssid"] = "Phone"

        with mock.patch("lidguard.platform_macos.current_wifi_ssid", return_value=None), mock.patch(
            "lidguard.platform_macos.current_ip_address",
            return_value=None,
        ), mock.patch("lidguard.platform_macos.connect_hotspot", return_value=True) as connect_mock:
            result = platform_macos.maybe_connect_hotspot(config, reason="Wi-Fi disconnected")

        self.assertTrue(result)
        connect_mock.assert_called_once_with("Phone")

    def test_recovery_monitor_requires_confirmed_disconnect(self) -> None:
        config = config_module.default_config()
        config["hotspot"]["enabled"] = True
        config["hotspot"]["ssid"] = "Phone"
        monitor = platform_macos.HotspotRecoveryMonitor(config)
        monitor.set_active(True)
        disconnected = platform_macos.NetworkStatus(associated=False, ssid="", ip_address="")

        with mock.patch("lidguard.platform_macos.current_network_status", return_value=disconnected), mock.patch(
            "lidguard.platform_macos.maybe_connect_hotspot",
            return_value=True,
        ) as connect_mock, mock.patch(
            "lidguard.platform_macos.time.monotonic",
            side_effect=[100.0, 101.0, 102.0, 110.0, 118.0, 126.0, 127.0, 128.0, 129.0],
        ):
            monitor._maybe_recover()
            monitor._maybe_recover()
            monitor._maybe_recover()
            monitor._maybe_recover()
            monitor._maybe_recover()

        self.assertEqual(connect_mock.call_count, 2)

    def test_recovery_monitor_resets_disconnect_counter_when_wifi_returns(self) -> None:
        config = config_module.default_config()
        config["hotspot"]["enabled"] = True
        config["hotspot"]["ssid"] = "Phone"
        monitor = platform_macos.HotspotRecoveryMonitor(config)
        monitor.set_active(True)
        disconnected = platform_macos.NetworkStatus(associated=False, ssid="", ip_address="")
        connected = platform_macos.NetworkStatus(associated=True, ssid="Office", ip_address="192.168.1.10")

        with mock.patch(
            "lidguard.platform_macos.current_network_status",
            side_effect=[disconnected, connected, disconnected, disconnected],
        ), mock.patch("lidguard.platform_macos.maybe_connect_hotspot", return_value=True) as connect_mock, mock.patch(
            "lidguard.platform_macos.time.monotonic",
            side_effect=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        ):
            monitor._maybe_recover()
            monitor._maybe_recover()
            monitor._maybe_recover()
            monitor._maybe_recover()

        connect_mock.assert_called_once()

    def test_recovery_monitor_avoids_parallel_connect_attempts(self) -> None:
        config = config_module.default_config()
        config["hotspot"]["enabled"] = True
        config["hotspot"]["ssid"] = "Phone"
        config["hotspot"]["disconnect_confirmation_polls"] = 1
        monitor = platform_macos.HotspotRecoveryMonitor(config)
        monitor.set_active(True)
        status = platform_macos.NetworkStatus(associated=False, ssid="", ip_address="")
        started = threading.Event()
        release = threading.Event()
        call_count = 0

        def connect_side_effect(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            started.set()
            self.assertTrue(release.wait(timeout=1.0))
            return True

        with mock.patch("lidguard.platform_macos.current_network_status", return_value=status), mock.patch(
            "lidguard.platform_macos.maybe_connect_hotspot",
            side_effect=connect_side_effect,
        ):
            worker = threading.Thread(target=monitor._maybe_recover)
            worker.start()
            self.assertTrue(started.wait(timeout=1.0))
            monitor._maybe_recover()
            release.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(call_count, 1)
