from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SRC))

from lidguard import cli


class CliTests(unittest.TestCase):
    def test_doctor_command_returns_report_exit_code(self) -> None:
        with mock.patch("lidguard.cli.render_report", return_value=(0, "ok\n")):
            exit_code = cli.main(["doctor"])
        self.assertEqual(exit_code, 0)

    def test_service_install_prints_path(self) -> None:
        configured = {"configured": True, "watched_processes": ["codex"], "hotspot": {"enabled": False, "ssid": ""}}
        with mock.patch("lidguard.cli.load_config", return_value=configured), mock.patch(
            "lidguard.cli.install_service",
            return_value="/tmp/lid-guard.service",
        ), mock.patch("builtins.print") as print_mock:
            exit_code = cli.main(["service", "install", "--write-only"])

        self.assertEqual(exit_code, 0)
        print_mock.assert_called_once()

    def test_run_command_starts_setup_when_unconfigured_in_tty(self) -> None:
        unconfigured = {
            "configured": False,
            "watched_processes": ["codex"],
            "process_poll_interval_seconds": 2.0,
            "lid_poll_interval_seconds": 0.3,
            "hotspot": {"enabled": False, "ssid": ""},
        }
        configured = dict(unconfigured, configured=True)
        fake_stdin = _FakeTtyInput()
        fake_stdout = _FakeTtyOutput()

        with mock.patch("lidguard.cli.load_config", side_effect=[unconfigured, configured]), mock.patch(
            "lidguard.cli.run_setup"
        ) as setup_mock, mock.patch.object(cli.sys, "stdin", fake_stdin), mock.patch.object(
            cli.sys,
            "stdout",
            fake_stdout,
        ), mock.patch.object(
            cli.sys,
            "platform",
            "darwin",
        ), mock.patch("lidguard.platform_macos.MacOSLidGuard") as guard_mock:
            exit_code = cli.main(["run"])

        self.assertEqual(exit_code, 0)
        setup_mock.assert_called_once_with()
        guard_mock.assert_called_once()

    def test_run_command_requires_setup_without_tty(self) -> None:
        unconfigured = {
            "configured": False,
            "watched_processes": ["codex"],
            "process_poll_interval_seconds": 2.0,
            "lid_poll_interval_seconds": 0.3,
            "hotspot": {"enabled": False, "ssid": ""},
        }
        fake_stdin = _FakeInput()
        fake_stdout = _FakeOutput()
        fake_stderr = io.StringIO()

        with mock.patch("lidguard.cli.load_config", return_value=unconfigured), mock.patch.object(
            cli.sys,
            "stdin",
            fake_stdin,
        ), mock.patch.object(cli.sys, "stdout", fake_stdout), mock.patch.object(cli.sys, "stderr", fake_stderr):
            exit_code = cli.main(["run"])

        self.assertEqual(exit_code, 1)


class _FakeInput(io.StringIO):
    def isatty(self) -> bool:
        return False


class _FakeOutput(io.StringIO):
    def isatty(self) -> bool:
        return False


class _FakeTtyInput(_FakeInput):
    def isatty(self) -> bool:
        return True


class _FakeTtyOutput(_FakeOutput):
    def isatty(self) -> bool:
        return True
