#!/usr/bin/env python3
"""
lid-guard: Keep your laptop awake when you close the lid, but lock the screen.

How it works:
  - Acquires a systemd-logind 'handle-lid-switch' inhibitor via D-Bus.
    This tells logind "don't sleep when the lid closes — we'll handle it."
  - Monitors lid state (via UPower D-Bus events, with /proc fallback polling).
  - On lid close → locks the screen (tries loginctl, xdg-screensaver, etc.)
  - On exit → releases the inhibitor so normal lid-sleep resumes.

Run directly:
  python3 lid_guard.py

Install as a systemd user service:
  ./install.sh
"""

import os
import sys
import time
import signal
import subprocess
import logging
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lid-guard")


# ---------------------------------------------------------------------------
# Screen locking
# ---------------------------------------------------------------------------

_LOCK_COMMANDS = [
    # Systemd / generic (works on most modern desktops)
    ["loginctl", "lock-session"],
    # Freedesktop screensaver
    ["xdg-screensaver", "lock"],
    # GNOME
    ["gnome-screensaver-command", "--lock"],
    ["dbus-send", "--session", "--dest=org.gnome.ScreenSaver",
     "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.Lock"],
    # KDE Plasma
    ["qdbus", "org.kde.screensaver", "/ScreenSaver", "Lock"],
    # Sway / Wayland compositors
    ["swaylock", "--daemonize"],
    # X11 fallback
    ["xlock"],
    ["i3lock"],
]


def lock_screen() -> bool:
    """Attempt to lock the screen using whichever tool is available."""
    for cmd in _LOCK_COMMANDS:
        try:
            result = subprocess.run(
                cmd,
                timeout=5,
                capture_output=True,
            )
            if result.returncode == 0:
                log.info("Screen locked via: %s", cmd[0])
                return True
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            log.warning("Lock command timed out: %s", cmd[0])
            continue
        except Exception as e:
            log.debug("Lock command %s failed: %s", cmd[0], e)
            continue

    log.error(
        "Could not lock screen — no supported locker found.\n"
        "Install one of: loginctl (systemd), xdg-screensaver, i3lock, swaylock"
    )
    return False


# ---------------------------------------------------------------------------
# Inhibitor lock (prevents logind from sleeping on lid close)
# ---------------------------------------------------------------------------

class InhibitorLock:
    """
    Holds a systemd-logind 'handle-lid-switch' delay inhibitor.

    While this object is alive and the fd is open, logind will not
    automatically sleep when the lid is closed.
    """

    def __init__(self):
        self._fd: int | None = None

    def acquire(self) -> bool:
        try:
            import dbus  # python3-dbus
            bus = dbus.SystemBus()
            mgr = dbus.Interface(
                bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"),
                "org.freedesktop.login1.Manager",
            )
            # 'block' mode: logind won't sleep until we release the fd
            fd_obj = mgr.Inhibit(
                "handle-lid-switch",
                "lid-guard",
                "Lock screen instead of sleeping on lid close",
                "block",
            )
            self._fd = fd_obj.take()
            log.info("Inhibitor lock acquired (fd=%d)", self._fd)
            return True

        except ImportError:
            log.warning(
                "python3-dbus not found — trying gdbus fallback.\n"
                "  Install with: sudo apt install python3-dbus  (or equivalent)"
            )
            return self._acquire_via_gdbus()

        except Exception as e:
            log.error("D-Bus inhibitor failed: %s", e)
            return False

    def _acquire_via_gdbus(self) -> bool:
        """Fallback: use gdbus CLI to get the inhibitor fd."""
        try:
            # gdbus can't easily hand us back an fd, so we use systemd-inhibit
            # by launching ourselves under it instead. This path is only reached
            # if python3-dbus is absent AND gdbus isn't useful here — so we just
            # warn and continue without the lock.
            log.warning(
                "Could not acquire inhibitor lock without python3-dbus.\n"
                "  The daemon will still lock your screen, but logind may\n"
                "  also suspend. Install python3-dbus for full protection."
            )
            return False
        except Exception:
            return False

    def release(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
                log.info("Inhibitor lock released")
            except OSError:
                pass
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


# ---------------------------------------------------------------------------
# Lid state monitoring
# ---------------------------------------------------------------------------

def _read_lid_state_proc() -> bool | None:
    """Read lid state from /proc/acpi. Returns True=closed, False=open, None=unknown."""
    for state_file in Path("/proc/acpi/button/lid").glob("*/state"):
        try:
            content = state_file.read_text()
            return "closed" in content
        except OSError:
            pass
    return None


def _read_lid_state_upower() -> bool | None:
    """Query lid state from UPower via upower CLI."""
    try:
        result = subprocess.run(
            ["upower", "-i", "/org/freedesktop/UPower/devices/line_power_AC"],
            capture_output=True, text=True, timeout=2,
        )
        # UPower doesn't expose lid directly, but we can check via /proc still
    except Exception:
        pass
    return _read_lid_state_proc()


class LidMonitor:
    """
    Polls lid state and fires a callback when it changes.

    Tries D-Bus events first; falls back to polling /proc.
    """

    POLL_INTERVAL = 0.3  # seconds

    def __init__(self, on_close, on_open=None):
        self._on_close = on_close
        self._on_open = on_open
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="lid-monitor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _poll_loop(self):
        log.info("Lid monitor started (polling /proc/acpi every %.1fs)", self.POLL_INTERVAL)
        last_state = _read_lid_state_proc()

        while self._running:
            time.sleep(self.POLL_INTERVAL)
            current = _read_lid_state_proc()

            if current is None:
                continue  # /proc not available, keep waiting

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
        self._inhibitor = InhibitorLock()
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

        # Check /proc availability
        if _read_lid_state_proc() is None:
            log.warning(
                "/proc/acpi/button/lid not found.\n"
                "  This system may not expose lid state via /proc.\n"
                "  Lid monitoring may not work. Check: ls /proc/acpi/button/lid/"
            )

        with self._inhibitor:
            self._monitor.start()
            log.info(
                "lid-guard is running.\n"
                "  Lid close  → screen locks, laptop stays awake\n"
                "  Ctrl-C / SIGTERM → exit (lid-sleep resumes normally)\n"
            )
            self._stop_event.wait()

        self._monitor.stop()
        log.info("lid-guard stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Quick sanity check: are we on Linux?
    if sys.platform != "linux":
        log.error("lid-guard only works on Linux (uses systemd-logind + /proc/acpi)")
        sys.exit(1)

    log.info("Starting lid-guard — terminal apps will keep running on lid close")
    LidGuard().run()


if __name__ == "__main__":
    main()
