from __future__ import annotations

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
        with mock.patch("lidguard.cli.install_service", return_value="/tmp/lid-guard.service"), mock.patch(
            "builtins.print"
        ) as print_mock:
            exit_code = cli.main(["service", "install", "--write-only"])

        self.assertEqual(exit_code, 0)
        print_mock.assert_called_once()
