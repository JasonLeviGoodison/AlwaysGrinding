from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import APP_NAME
from .setup_ui import MenuOption, PromptSetupUI, TerminalSetupUI

log = logging.getLogger("lidguard.config")

DEFAULT_WATCHED_PROCESSES = ["claude", "codex", "openclaw"]
COMMON_PROCESS_OPTIONS = ["codex", "claude", "openclaw", "aider", "cursor"]
DEFAULT_CONFIG = {
    "configured": False,
    "watched_processes": DEFAULT_WATCHED_PROCESSES,
    "process_poll_interval_seconds": 2.0,
    "lid_poll_interval_seconds": 0.3,
    "hotspot": {
        "enabled": False,
        "ssid": "",
    },
}


def config_dir() -> Path:
    override = os.environ.get("LID_GUARD_CONFIG_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".config" / APP_NAME


def data_dir() -> Path:
    override = os.environ.get("LID_GUARD_DATA_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.json"


def legacy_config_file() -> Path | None:
    if os.environ.get("LID_GUARD_CONFIG_HOME") or sys.platform != "darwin":
        return None
    return Path.home() / ".config" / APP_NAME / "config.json"


def config_file_candidates() -> tuple[Path, ...]:
    primary = config_file()
    legacy = legacy_config_file()
    if legacy is None or legacy == primary:
        return (primary,)
    return (primary, legacy)


def existing_config_file() -> Path | None:
    for path in config_file_candidates():
        if path.exists():
            return path
    return None


def active_config_file() -> Path:
    return existing_config_file() or config_file()


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config() -> dict[str, Any]:
    path = existing_config_file()
    if path is None:
        return default_config()

    legacy = legacy_config_file()
    if legacy is not None and path == legacy:
        log.info("Loaded legacy macOS config from %s.", path)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read %s: %s. Using defaults.", path, exc)
        return default_config()

    try:
        return normalize_config(raw)
    except ValueError as exc:
        log.warning("Invalid config in %s: %s. Using defaults.", path, exc)
        return default_config()


def save_config(config: dict[str, Any]) -> Path:
    normalized = normalize_config(config)
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        prefix=path.name,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as handle:
        json.dump(normalized, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    temp_path.replace(path)
    log.debug("Saved config to %s", path)
    return path


def normalize_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")

    config = default_config()

    configured = raw.get("configured")
    if configured is not None:
        if not isinstance(configured, bool):
            raise ValueError("'configured' must be true or false")
        config["configured"] = configured

    watched = raw.get("watched_processes")
    if watched is not None:
        if not isinstance(watched, list):
            raise ValueError("'watched_processes' must be a list")
        normalized = [name for name in (_normalize_process_name(item) for item in watched) if name]
        if normalized:
            config["watched_processes"] = normalized

    process_interval = raw.get("process_poll_interval_seconds")
    if process_interval is not None:
        config["process_poll_interval_seconds"] = _positive_float(
            process_interval,
            "process_poll_interval_seconds",
        )

    lid_interval = raw.get("lid_poll_interval_seconds")
    if lid_interval is not None:
        config["lid_poll_interval_seconds"] = _positive_float(
            lid_interval,
            "lid_poll_interval_seconds",
        )

    hotspot = raw.get("hotspot")
    if hotspot is not None:
        if not isinstance(hotspot, dict):
            raise ValueError("'hotspot' must be an object")
        enabled = hotspot.get("enabled", config["hotspot"]["enabled"])
        if not isinstance(enabled, bool):
            raise ValueError("'hotspot.enabled' must be true or false")
        ssid = hotspot.get("ssid", config["hotspot"]["ssid"])
        if not isinstance(ssid, str):
            raise ValueError("'hotspot.ssid' must be a string")
        config["hotspot"] = {
            "enabled": enabled,
            "ssid": ssid.strip(),
        }

    return config


def parse_process_names(raw: str) -> list[str]:
    names = [name for name in (_normalize_process_name(part) for part in raw.split(",")) if name]
    if not names:
        raise ValueError("at least one watched process name is required")
    return names


def scan_wifi_networks() -> list[str]:
    """Return saved Wi-Fi networks on macOS."""
    if sys.platform != "darwin":
        return []

    try:
        iface = _wifi_interface()
        result = subprocess.run(
            ["networksetup", "-listpreferredwirelessnetworks", iface],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    networks: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        ssid = line.strip()
        if ssid:
            networks.append(ssid)
    return networks


def run_setup(
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    service_installer: Callable[[bool], Path] | None = None,
    use_menu: bool | None = None,
) -> dict[str, Any]:
    config = load_config()
    ui = _build_setup_ui(input_func, output_func, use_menu)

    ui.message("")
    ui.message("Configure when lid-guard should stay active and whether it should recover to a hotspot after a real Wi-Fi disconnect.")

    config["watched_processes"] = _configure_watched_processes(config, ui)
    if sys.platform == "darwin":
        _configure_hotspot(config, ui)
    else:
        ui.message("")
        ui.message("Hotspot recovery is only available on macOS.")

    config["configured"] = True
    path = save_config(config)
    ui.message("")
    ui.message(f"Saved settings to {path}")
    installed_service = False
    if sys.platform in {"linux", "darwin"}:
        install_background = ui.confirm(
            "Install the background service now?",
            default=False,
            yes_label="Install service",
            no_label="Not now",
        )
        if install_background:
            installer = service_installer
            if installer is None:
                from .service import install_service as installer

            try:
                service_path = installer(True)
            except RuntimeError as exc:
                ui.message(f"Could not install background service: {exc}")
            else:
                installed_service = True
                ui.message(f"Installed background service: {service_path}")

    if not installed_service:
        ui.message("Start lid-guard with: lid-guard run")
        if sys.platform in {"linux", "darwin"}:
            ui.message("Optional: lid-guard service install")
    ui.message("")
    return config


def is_configured(config: dict[str, Any] | None = None) -> bool:
    current = load_config() if config is None else config
    return bool(current.get("configured"))


def _normalize_process_name(value: Any) -> str:
    text = str(value).strip().lower()
    return text


def _positive_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{label}' must be a positive number") from exc
    if result <= 0:
        raise ValueError(f"'{label}' must be a positive number")
    return result


def _wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        lines = result.stdout.splitlines()
        for index, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for offset in range(index + 1, min(index + 4, len(lines))):
                    if lines[offset].startswith("Device:"):
                        return lines[offset].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0"


def _build_setup_ui(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    use_menu: bool | None,
):
    if use_menu is True:
        return TerminalSetupUI()
    if use_menu is None and input_func is input and output_func is print and TerminalSetupUI.available():
        return TerminalSetupUI()
    return PromptSetupUI(input_func=input_func, output_func=output_func)


def _configure_watched_processes(config: dict[str, Any], ui) -> list[str]:
    ordered = _ordered_process_options(config["watched_processes"])
    selected = ui.multi_select(
        "Select the processes that should keep lid-guard active.",
        [MenuOption(label=name, value=name) for name in ordered],
        selected_values=config["watched_processes"],
        min_selected=1,
    )
    extras = ui.text(
        "Add any extra process names.",
        "Extra process names (comma-separated, optional)",
        allow_empty=True,
    )
    merged = list(selected)
    if extras:
        for name in parse_process_names(extras):
            if name not in merged:
                merged.append(name)
    return merged


def _configure_hotspot(config: dict[str, Any], ui) -> None:
    enabled = ui.confirm(
        "Reconnect to a hotspot only when Wi-Fi disconnects?",
        default=config["hotspot"]["enabled"],
        yes_label="Enable hotspot recovery",
        no_label="Disable hotspot recovery",
    )
    config["hotspot"]["enabled"] = enabled
    if not enabled:
        config["hotspot"]["ssid"] = ""
        return

    config["hotspot"]["ssid"] = _pick_hotspot_ssid(
        current_ssid=config["hotspot"].get("ssid", ""),
        ui=ui,
    )


def _ordered_process_options(current: list[str]) -> list[str]:
    ordered: list[str] = []
    for name in [*current, *COMMON_PROCESS_OPTIONS]:
        normalized = _normalize_process_name(name)
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _pick_hotspot_ssid(
    current_ssid: str,
    ui,
) -> str:
    networks = scan_wifi_networks()
    ordered = [ssid for ssid in networks if ssid]
    if current_ssid and current_ssid not in ordered:
        ordered.insert(0, current_ssid)

    if ordered:
        options = [MenuOption(label=ssid, value=ssid) for ssid in ordered]
        options.append(MenuOption(label="Enter SSID manually", value="__manual__"))
        selected = ui.select(
            "Choose the hotspot lid-guard should join after a real Wi-Fi disconnect.",
            options,
            default_index=max(0, ordered.index(current_ssid)) if current_ssid in ordered else 0,
        )
        if selected != "__manual__":
            return str(selected)
    else:
        ui.message("")
        ui.message("No saved Wi-Fi networks were found. Enter the hotspot SSID manually.")

    return ui.text(
        "Enter the hotspot SSID.",
        "Hotspot SSID",
        default=current_ssid,
        allow_empty=False,
    )


load = load_config
save = save_config
