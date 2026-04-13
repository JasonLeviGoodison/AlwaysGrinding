from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .process_watcher import ProcessWatcher

log = logging.getLogger("lidguard.linux")

LOCK_COMMANDS = [
    ["loginctl", "lock-session"],
    ["xdg-screensaver", "lock"],
    ["gnome-screensaver-command", "--lock"],
    [
        "dbus-send",
        "--session",
        "--dest=org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver.Lock",
    ],
    ["qdbus", "org.kde.screensaver", "/ScreenSaver", "Lock"],
    ["swaylock", "--daemonize"],
    ["xlock"],
    ["i3lock"],
]


def lock_screen() -> bool:
    for command in LOCK_COMMANDS:
        try:
            result = subprocess.run(command, timeout=5, capture_output=True, check=False)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            log.warning("Lock command timed out: %s", command[0])
            continue
        except Exception as exc:
            log.debug("Lock command failed for %s: %s", command[0], exc)
            continue

        if result.returncode == 0:
            log.info("Screen locked via %s", command[0])
            return True

    log.error("Could not lock the screen. Install loginctl, swaylock, i3lock, or another supported locker.")
    return False


def read_lid_state() -> bool | None:
    """Return True for closed, False for open, or None if unavailable."""
    for state_file in Path("/proc/acpi/button/lid").glob("*/state"):
        try:
            return "closed" in state_file.read_text(encoding="utf-8").lower()
        except OSError:
            continue
    return None


class InhibitorLock:
    """Hold a logind handle-lid-switch inhibitor while protection is active."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._lock = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        with self._lock:
            if self._fd is not None:
                return True
            try:
                import dbus

                bus = dbus.SystemBus()
                manager = dbus.Interface(
                    bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"),
                    "org.freedesktop.login1.Manager",
                )
                fd = manager.Inhibit(
                    "handle-lid-switch",
                    "lid-guard",
                    "Lock screen instead of sleeping on lid close",
                    "block",
                )
                self._fd = fd.take()
                log.info("Acquired systemd inhibitor lock.")
                return True
            except ImportError:
                log.warning(
                    "python3-dbus is not installed. Install it so lid-guard can hold a logind inhibitor."
                )
            except Exception as exc:
                log.error("Could not acquire logind inhibitor: %s", exc)
            return False

    def release(self) -> None:
        with self._lock:
            if self._fd is None:
                return
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            log.info("Released systemd inhibitor lock.")


class LidMonitor:
    def __init__(
        self,
        on_close: Callable[[], None],
        on_open: Callable[[], None] | None = None,
        poll_interval: float = 0.3,
    ) -> None:
        self._on_close = on_close
        self._on_open = on_open
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="lidguard-lid-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 1)

    def _poll_loop(self) -> None:
        log.info("Started Linux lid monitor (polling every %.1fs).", self._poll_interval)
        last_state = read_lid_state()

        while not self._stop_event.is_set():
            current = read_lid_state()
            if current is not None:
                if last_state is False and current is True:
                    self._call(self._on_close, "on_close")
                elif last_state is True and current is False and self._on_open is not None:
                    self._call(self._on_open, "on_open")
                last_state = current
            self._stop_event.wait(self._poll_interval)

    def _call(self, callback: Callable[[], None], label: str) -> None:
        try:
            callback()
        except Exception:
            log.exception("Lid monitor callback %s failed.", label)


class LinuxLidGuard:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._inhibitor = InhibitorLock()
        self._monitor = LidMonitor(
            on_close=self._handle_lid_close,
            on_open=self._handle_lid_open,
            poll_interval=float(config["lid_poll_interval_seconds"]),
        )
        self._watcher = ProcessWatcher(
            on_active=self._on_processes_active,
            on_idle=self._on_processes_idle,
            processes=config["watched_processes"],
            poll_interval=float(config["process_poll_interval_seconds"]),
        )
        self._stop_event = threading.Event()

    def _on_processes_active(self) -> None:
        self._inhibitor.acquire()

    def _on_processes_idle(self) -> None:
        self._inhibitor.release()

    def _handle_lid_close(self) -> None:
        if self._inhibitor.held:
            log.info("Lid closed while protection is active. Locking screen.")
            lock_screen()
        else:
            log.info("Lid closed with no watched process running. Allowing normal OS behavior.")

    def _handle_lid_open(self) -> None:
        log.info("Lid opened.")

    def _handle_signal(self, signum: int, _frame: object) -> None:
        log.info("Received signal %s. Shutting down.", signum)
        self._stop_event.set()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if read_lid_state() is None:
            log.warning("Could not detect /proc/acpi/button/lid. Lid monitoring may not work on this machine.")

        self._watcher.start()
        self._monitor.start()
        log.info(
            "lid-guard is running on Linux. Watched processes: %s",
            ", ".join(self._config["watched_processes"]),
        )

        self._stop_event.wait()
        self._monitor.stop()
        self._watcher.stop()
        self._inhibitor.release()
        log.info("lid-guard stopped.")

