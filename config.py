"""
config.py — load/save lid-guard settings and manage Keychain secrets (macOS).

Config file: ~/.config/lid-guard/config.json
Keychain:    service="lid-guard", account="hotspot-password"
"""

import json
import subprocess
import sys
import logging
from pathlib import Path

log = logging.getLogger("lid-guard.config")

CONFIG_DIR  = Path.home() / ".config" / "lid-guard"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "hotspot": {
        "enabled": False,
        "ssid": "",
    }
}


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            merged = json.loads(json.dumps(DEFAULTS))
            _deep_merge(merged, data)
            return merged
        except Exception as e:
            log.warning("Could not read config (%s) — using defaults", e)
    return json.loads(json.dumps(DEFAULTS))


def save(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    log.debug("Config saved to %s", CONFIG_FILE)


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ---------------------------------------------------------------------------
# Wi-Fi network scanning (macOS)
# ---------------------------------------------------------------------------

def scan_wifi_networks() -> list[str]:
    """Return saved Wi-Fi networks from macOS preferred network list."""
    try:
        iface = _wifi_interface()
        result = subprocess.run(
            ["networksetup", "-listpreferredwirelessnetworks", iface],
            capture_output=True, text=True, timeout=5,
        )
        ssids = []
        for line in result.stdout.splitlines()[1:]:  # skip header line
            ssid = line.strip()
            if ssid:
                ssids.append(ssid)
        return ssids
    except Exception:
        return []


def _wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i + 1, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0"


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def run_setup():
    print("\n=== lid-guard setup ===\n")
    print("lid-guard keeps your laptop awake when you close the lid so that")
    print("Claude Code, Codex, or openclaw can keep running.\n")

    cfg = load()

    # ── Hotspot question ────────────────────────────────────────────────────
    print("Would you like to auto-connect to a hotspot when you close the lid?")
    print("This keeps your processes online when you unplug and walk away.\n")

    enabled = _prompt_bool("Enable hotspot auto-connect?", cfg["hotspot"]["enabled"])
    cfg["hotspot"]["enabled"] = enabled

    if enabled:
        ssid = _pick_hotspot_ssid(cfg["hotspot"].get("ssid", ""))
        cfg["hotspot"]["ssid"] = ssid
        print(f"\n  Hotspot set to: {ssid!r}")
    else:
        print("\n  Hotspot auto-connect: off")

    # ── Save ────────────────────────────────────────────────────────────────
    save(cfg)
    print(f"\nSettings saved to {CONFIG_FILE}")
    print("Start lid-guard with:  python3 run.py\n")


def _pick_hotspot_ssid(current_ssid: str) -> str:
    """Show saved Wi-Fi networks and let the user pick one."""
    networks = scan_wifi_networks()

    if networks:
        print(f"\n  Your saved networks:\n")
        for i, ssid in enumerate(networks, 1):
            marker = "  ← current" if ssid == current_ssid else ""
            print(f"    {i:2}.  {ssid}{marker}")
        print()

        while True:
            raw = input("  Pick a number, or type a name manually: ").strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(networks):
                    return networks[idx]
                print(f"  Please enter a number between 1 and {len(networks)}.")
            elif raw:
                return raw
    else:
        print("\n  No saved networks found. Type the hotspot name manually:\n")
        while True:
            name = input("  Hotspot name: ").strip()
            if name:
                return name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prompt_bool(prompt: str, default: bool) -> bool:
    default_str = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} [{default_str}]: ").strip().lower()
        if answer == "":
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please enter y or n.")
