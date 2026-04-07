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
            # Merge with defaults so new keys are always present
            merged = json.loads(json.dumps(DEFAULTS))  # deep copy
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
# Keychain (macOS only)
# ---------------------------------------------------------------------------

_KEYCHAIN_SERVICE = "lid-guard"
_KEYCHAIN_ACCOUNT = "hotspot-password"


def keychain_save_password(password: str) -> bool:
    """Store the hotspot password in macOS Keychain."""
    if sys.platform != "darwin":
        return False
    try:
        # Delete existing entry first (ignore errors)
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT],
            capture_output=True,
        )
        result = subprocess.run(
            ["security", "add-generic-password",
             "-s", _KEYCHAIN_SERVICE,
             "-a", _KEYCHAIN_ACCOUNT,
             "-w", password],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception as e:
        log.error("Keychain save failed: %s", e)
        return False


def keychain_load_password() -> str | None:
    """Retrieve the hotspot password from macOS Keychain."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE,
             "-a", _KEYCHAIN_ACCOUNT,
             "-w"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        log.error("Keychain load failed: %s", e)
    return None


def keychain_delete_password():
    if sys.platform != "darwin":
        return
    subprocess.run(
        ["security", "delete-generic-password",
         "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def run_setup():
    """Interactive setup wizard — configures hotspot auto-connect."""
    print("\n=== lid-guard setup ===\n")

    cfg = load()

    print("Auto-connect to hotspot on lid close?")
    print("  This connects your Mac to your phone's hotspot when you")
    print("  close the lid, so processes keep their network connection.\n")

    enabled = _prompt_bool("Enable hotspot auto-connect?", cfg["hotspot"]["enabled"])
    cfg["hotspot"]["enabled"] = enabled

    if enabled:
        current_ssid = cfg["hotspot"]["ssid"]
        prompt = f"Hotspot SSID [{current_ssid}]: " if current_ssid else "Hotspot SSID: "
        ssid = input(prompt).strip()
        if not ssid and current_ssid:
            ssid = current_ssid
        cfg["hotspot"]["ssid"] = ssid

        print("\nHotspot password (stored securely in Keychain, leave blank if already saved):")
        import getpass
        password = getpass.getpass("Password (Enter to skip): ").strip()
        if password:
            if keychain_save_password(password):
                print("  Password saved to Keychain.")
            else:
                print("  Warning: could not save to Keychain.")
        else:
            existing = keychain_load_password()
            if existing:
                print("  Using existing password from Keychain.")
            else:
                print("  No password saved — connection will only work if SSID is in Known Networks.")

        print(f"\n  Hotspot: {ssid!r}  |  auto-connect: ON")
    else:
        print("  Hotspot auto-connect: OFF")

    save(cfg)
    print(f"\nSettings saved to {CONFIG_FILE}")
    print("Run 'python3 run.py' to start lid-guard.\n")


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
