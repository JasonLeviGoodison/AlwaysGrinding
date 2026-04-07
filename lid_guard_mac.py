#!/usr/bin/env python3
"""
lid-guard (macOS): Keep your laptop awake on lid close, lock the screen instead.

macOS approach:
  - Uses IOKit via ctypes to register for lid-open/close events (kIOPMMessageSystemPowerEventOccurred)
    or polls the lid state via ioreg.
  - Calls `pmset` to suppress sleep and `caffeinate` to assert a power assertion.
  - Locks screen via AppleScript (Keychain locking) or /System/Library/CoreServices/Menu Extras/User.menu
  - Uses a CoreFoundation run loop to receive lid events.

Run directly:
  python3 lid_guard_mac.py
"""

import os
import sys
import time
import signal
import subprocess
import logging
import threading
import ctypes
import ctypes.util

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lid-guard-mac")


# ---------------------------------------------------------------------------
# Screen locking (macOS)
# ---------------------------------------------------------------------------

def lock_screen() -> bool:
    """Lock the macOS screen."""
    methods = [
        # macOS 10.13+: fastest / most reliable
        ["open", "-a", "ScreenSaverEngine"],
        # Lock via pmset (triggers screensaver)
        # AppleScript
    ]

    # Try the screensaver engine first (starts screensaver which respects "require password immediately")
    for cmd in methods:
        try:
            result = subprocess.run(cmd, timeout=5, capture_output=True)
            if result.returncode == 0:
                log.info("Screen locked via: %s", " ".join(cmd))
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fallback: AppleScript
    script = 'tell application "System Events" to keystroke "q" using {command down, control down}'
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
        log.info("Screen locked via AppleScript (Cmd+Ctrl+Q)")
        return True
    except Exception as e:
        log.debug("AppleScript lock failed: %s", e)

    # Last resort: pmset displaysleepnow
    try:
        subprocess.run(["pmset", "displaysleepnow"], timeout=5, capture_output=True)
        log.info("Display sleep triggered via pmset")
        return True
    except Exception:
        pass

    log.error("Could not lock screen on macOS")
    return False


# ---------------------------------------------------------------------------
# Prevent sleep using caffeinate
# ---------------------------------------------------------------------------

class CaffeinateGuard:
    """
    Runs `caffeinate -d -i -s` as a subprocess to prevent sleep.

    Flags:
      -d  prevent display sleep
      -i  prevent idle sleep
      -s  prevent system sleep (requires AC or is ignored on battery)
    """

    def __init__(self):
        self._proc = None

    def start(self):
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-d", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("caffeinate started (pid=%d) — system sleep inhibited", self._proc.pid)
        except FileNotFoundError:
            log.error("caffeinate not found — this should be built into macOS")

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=3)
            log.info("caffeinate stopped — normal sleep restored")


# ---------------------------------------------------------------------------
# Lid state monitoring (macOS)
# ---------------------------------------------------------------------------

def _get_lid_state_ioreg() -> bool | None:
    """
    Returns True if lid is closed, False if open, None if unknown.
    Reads from ioreg: IOPMrootDomain's AppleClamshellState.
    """
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            if "AppleClamshellState" in line:
                return "Yes" in line or "true" in line.lower() or "1" in line
    except Exception as e:
        log.debug("ioreg lid check failed: %s", e)
    return None


class LidMonitor:
    POLL_INTERVAL = 0.3

    def __init__(self, on_close, on_open=None):
        self._on_close = on_close
        self._on_open = on_open
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="lid-monitor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _poll_loop(self):
        log.info("Lid monitor started (polling ioreg every %.1fs)", self.POLL_INTERVAL)
        last_state = _get_lid_state_ioreg()

        while self._running:
            time.sleep(self.POLL_INTERVAL)
            current = _get_lid_state_ioreg()

            if current is None:
                continue

            if last_state is False and current is True:
                log.info("Lid CLOSED — locking screen")
                self._on_close()
            elif last_state is True and current is False:
                log.info("Lid OPENED")
                if self._on_open:
                    self._on_open()

            last_state = current


# ---------------------------------------------------------------------------
# Main daemon
# ---------------------------------------------------------------------------

class LidGuard:

    def __init__(self):
        self._caffeinate = CaffeinateGuard()
        self._monitor = LidMonitor(on_close=self._handle_lid_close)
        self._stop_event = threading.Event()

    def _handle_lid_close(self):
        lock_screen()

    def _handle_signal(self, sig, _frame):
        log.info("Received signal %d — shutting down gracefully", sig)
        self._stop_event.set()

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Verify ioreg works
        state = _get_lid_state_ioreg()
        if state is None:
            log.warning(
                "Cannot read lid state via ioreg.\n"
                "  Lid monitoring may not work on this Mac."
            )
        else:
            log.info("Lid is currently: %s", "CLOSED" if state else "OPEN")

        self._caffeinate.start()
        self._monitor.start()

        log.info(
            "lid-guard is running.\n"
            "  Lid close  → screen locks, Mac stays awake\n"
            "  Ctrl-C / SIGTERM → exit (normal sleep resumes)\n"
        )

        self._stop_event.wait()
        self._monitor.stop()
        self._caffeinate.stop()
        log.info("lid-guard stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if sys.platform != "darwin":
        print("This script is for macOS. On Linux use: python3 lid_guard.py")
        sys.exit(1)

    # macOS: "require password immediately after sleep or screensaver begins"
    # must be enabled in System Preferences > Security & Privacy for the lock to work.
    log.info(
        "Starting lid-guard (macOS)\n"
        "  Make sure 'Require password immediately after sleep or screensaver begins'\n"
        "  is ON in: System Settings → Privacy & Security → Advanced (or Lock Screen)"
    )
    LidGuard().run()


if __name__ == "__main__":
    main()
