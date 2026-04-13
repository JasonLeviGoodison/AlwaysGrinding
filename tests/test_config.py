from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SRC))

from lidguard import config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {
                "LID_GUARD_CONFIG_HOME": self.temp_dir.name,
                "LID_GUARD_DATA_HOME": self.temp_dir.name,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_load_config_returns_defaults_when_missing(self) -> None:
        loaded = config.load_config()
        self.assertEqual(loaded["watched_processes"], ["claude", "codex", "openclaw"])
        self.assertFalse(loaded["hotspot"]["enabled"])

    def test_save_config_normalizes_values(self) -> None:
        path = config.save_config(
            {
                "watched_processes": [" Codex ", "claude"],
                "process_poll_interval_seconds": 4,
                "lid_poll_interval_seconds": 0.5,
                "hotspot": {
                    "enabled": True,
                    "ssid": "Phone",
                    "force_on_network_loss": True,
                    "internet_check_failures_before_force": 3,
                },
            }
        )

        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(saved["watched_processes"], ["codex", "claude"])
        self.assertEqual(saved["hotspot"]["ssid"], "Phone")
        self.assertEqual(saved["hotspot"]["internet_check_failures_before_force"], 3)

    def test_run_setup_updates_watched_processes(self) -> None:
        answers = iter(["codex, aider"])
        output: list[str] = []

        with mock.patch.object(config.sys, "platform", "linux"):
            result = config.run_setup(
                input_func=lambda prompt: next(answers),
                output_func=output.append,
            )

        self.assertEqual(result["watched_processes"], ["codex", "aider"])
        self.assertIn("Saved settings", "\n".join(output))

    def test_normalize_config_populates_hotspot_failover_defaults(self) -> None:
        normalized = config.normalize_config({"hotspot": {"enabled": True, "ssid": "Phone"}})
        self.assertTrue(normalized["hotspot"]["force_on_network_loss"])
        self.assertEqual(normalized["hotspot"]["internet_check_match"], "Success")
