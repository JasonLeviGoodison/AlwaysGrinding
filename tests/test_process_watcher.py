from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SRC))

from lidguard import process_watcher


class ProcessWatcherTests(unittest.TestCase):
    def test_command_matches_on_binary_name(self) -> None:
        self.assertTrue(process_watcher.command_matches("/usr/local/bin/codex chat", "codex"))
        self.assertFalse(process_watcher.command_matches("/usr/local/bin/python app.py", "codex"))

    def test_any_watched_running_ignores_current_pid(self) -> None:
        process_table = [
            (os.getpid(), "/usr/local/bin/codex"),
            (4242, "/usr/local/bin/python worker.py"),
        ]
        self.assertFalse(
            process_watcher.any_watched_running(
                ["codex"],
                process_table=process_table,
                ignore_pids={os.getpid()},
            )
        )

    def test_probe_process_listing_falls_back_to_proc(self) -> None:
        with mock.patch(
            "lidguard.process_watcher._list_processes_via_ps",
            side_effect=RuntimeError("ps failed"),
        ), mock.patch(
            "lidguard.process_watcher._list_processes_via_proc",
            return_value=[(1, "launchd"), (2, "python3 -m lidguard")],
        ):
            ok, detail = process_watcher.probe_process_listing()

        self.assertTrue(ok)
        self.assertIn("/proc", detail)
