from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

from .config import load_config
from .process_watcher import ProcessWatcher

log = logging.getLogger("lidguard.macos")
HOTSPOT_CONNECT_TIMEOUT_SECONDS = 12.0
HOTSPOT_DISCONNECT_CONFIRMATION_POLLS = 2
HOTSPOT_POLL_INTERVAL_SECONDS = 1.0
HOTSPOT_RETRY_COOLDOWN_SECONDS = 15.0
HOTSPOT_SETTLE_SECONDS = 15.0


def lock_screen() -> bool:
    script = 'tell application "System Events" to keystroke "q" using {command down, control down}'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            timeout=5,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            log.info("Screen locked with AppleScript.")
            return True
    except Exception as exc:
        log.debug("AppleScript lock failed: %s", exc)

    try:
        result = subprocess.run(
            ["open", "-a", "ScreenSaverEngine"],
            timeout=5,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            log.info("ScreenSaverEngine started.")
            return True
    except Exception as exc:
        log.debug("ScreenSaverEngine fallback failed: %s", exc)

    try:
        result = subprocess.run(
            ["pmset", "displaysleepnow"],
            timeout=5,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            log.info("Display sleep triggered.")
            return True
    except Exception as exc:
        log.debug("pmset fallback failed: %s", exc)

    log.error("Could not lock the screen on macOS.")
    return False


def connect_hotspot(ssid: str) -> bool:
    interface = _wifi_interface()
    command = ["networksetup", "-setairportnetwork", interface, ssid]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=HOTSPOT_CONNECT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("Hotspot connection timed out for %r.", ssid)
        return False
    except Exception as exc:
        log.warning("Hotspot connection failed for %r: %s", ssid, exc)
        return False

    if result.returncode == 0:
        log.info("Requested hotspot connection to %r via %s.", ssid, interface)
        return True

    detail = result.stderr.strip() or result.stdout.strip() or f"{command[0]} exited with status {result.returncode}"
    log.warning("Hotspot connection failed for %r: %s", ssid, detail)
    return False


def maybe_connect_hotspot(
    config: dict | None = None,
    reason: str = "manual request",
) -> bool:
    current = config if config is not None else load_config()
    hotspot = current.get("hotspot", {})
    if not hotspot.get("enabled"):
        return False

    ssid = str(hotspot.get("ssid", "")).strip()
    if not ssid:
        log.warning("Hotspot recovery is enabled but no SSID is configured.")
        return False

    current_ssid = current_wifi_ssid()
    if current_ssid == ssid:
        log.info("Already connected to hotspot %r.", ssid)
        return True
    if current_ssid:
        log.info("Already connected to Wi-Fi network %r. Leaving hotspot unchanged.", current_ssid)
        return True

    current_ip = current_ip_address()
    if current_ip:
        log.info("Wi-Fi already has IP %s. Leaving hotspot unchanged.", current_ip)
        return True

    log.info("Attempting hotspot connection to %r (%s).", ssid, reason)
    return connect_hotspot(ssid)


@dataclass(slots=True)
class NetworkStatus:
    associated: bool
    ssid: str
    ip_address: str


def current_wifi_ssid() -> str | None:
    interface = _wifi_interface()
    try:
        result = subprocess.run(
            ["networksetup", "-getairportnetwork", interface],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        log.debug("Could not read current Wi-Fi network: %s", exc)
        return None

    output = result.stdout.strip()
    prefix = "Current Wi-Fi Network: "
    if output.startswith(prefix):
        return output[len(prefix) :].strip() or None
    return None


def current_ip_address() -> str | None:
    interface = _wifi_interface()
    try:
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        log.debug("Could not read Wi-Fi IP address: %s", exc)
        result = None

    if result is not None and result.returncode == 0:
        address = result.stdout.strip()
        if address:
            return address

    return _current_ip_address_via_ifconfig(interface)


def current_network_status(config: dict) -> NetworkStatus:
    ssid = current_wifi_ssid() or ""
    ip_address = current_ip_address() or ""
    associated = bool(ssid or ip_address)

    return NetworkStatus(
        associated=associated,
        ssid=ssid,
        ip_address=ip_address,
    )


def read_lid_state() -> bool | None:
    """Return True for closed, False for open, or None if unavailable."""
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:
        log.debug("ioreg lid state check failed: %s", exc)
        return None

    for line in result.stdout.splitlines():
        if "AppleClamshellState" in line:
            lowered = line.lower()
            return "yes" in lowered or "true" in lowered
    return None


class CaffeinateGuard:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.active:
                return
            try:
                self._proc = subprocess.Popen(
                    ["caffeinate", "-d", "-i"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Started caffeinate (pid=%s).", self._proc.pid)
            except FileNotFoundError:
                log.error("caffeinate is not available on this macOS system.")

    def stop(self) -> None:
        with self._lock:
            if self._proc is None:
                return
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            log.info("Stopped caffeinate.")


class HotspotRecoveryMonitor:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._hotspot = config.get("hotspot", {})
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._lock = threading.Lock()
        self._last_attempt = 0.0
        self._settle_until = 0.0
        self._attempt_in_progress = False
        self._disconnect_observations = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="lidguard-hotspot-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval() + 1)

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = active
            if not active:
                self._disconnect_observations = 0

    def _poll_loop(self) -> None:
        if not self._recovery_enabled():
            return
        log.info(
            "Started hotspot recovery monitor (checking every %.1fs).",
            self._poll_interval(),
        )
        while not self._stop_event.is_set():
            self._maybe_recover()
            self._stop_event.wait(self._poll_interval())

    def _maybe_recover(self) -> None:
        if not self._recovery_enabled():
            return
        with self._lock:
            active = self._active
            settling = time.monotonic() < self._settle_until
            attempt_in_progress = self._attempt_in_progress
        if not active:
            return
        if settling or attempt_in_progress:
            return

        target_ssid = str(self._hotspot.get("ssid", "")).strip()
        if not target_ssid:
            return

        status = current_network_status(self._config)
        if status.associated:
            self._disconnect_observations = 0
            return
        self._disconnect_observations += 1
        if self._disconnect_observations < self._disconnect_confirmation_threshold():
            return

        now = time.monotonic()
        if now - self._last_attempt < self._reconnect_interval():
            return

        with self._lock:
            if self._attempt_in_progress:
                return
            self._attempt_in_progress = True

        success = False
        try:
            success = maybe_connect_hotspot(
                self._config,
                reason="Wi-Fi disconnected",
            )
        finally:
            completed_at = time.monotonic()
            with self._lock:
                self._attempt_in_progress = False
                self._last_attempt = completed_at
                if success:
                    self._settle_until = completed_at + self._settle_interval()
                self._disconnect_observations = 0

    def _hotspot_enabled(self) -> bool:
        return bool(self._hotspot.get("enabled"))

    def _recovery_enabled(self) -> bool:
        return self._hotspot_enabled()

    def _poll_interval(self) -> float:
        return float(self._hotspot.get("network_check_interval_seconds", HOTSPOT_POLL_INTERVAL_SECONDS))

    def _reconnect_interval(self) -> float:
        return float(self._hotspot.get("reconnect_interval_seconds", HOTSPOT_RETRY_COOLDOWN_SECONDS))

    def _disconnect_confirmation_threshold(self) -> int:
        return int(
            self._hotspot.get(
                "disconnect_confirmation_polls",
                HOTSPOT_DISCONNECT_CONFIRMATION_POLLS,
            )
        )

    def _settle_interval(self) -> float:
        return max(self._reconnect_interval(), HOTSPOT_SETTLE_SECONDS)


class LidMonitor:
    def __init__(self, on_close, on_open=None, poll_interval: float = 0.3) -> None:
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
        log.info("Started macOS lid monitor (polling every %.1fs).", self._poll_interval)
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

    def _call(self, callback, label: str) -> None:
        try:
            callback()
        except Exception:
            log.exception("Lid monitor callback %s failed.", label)


class MacOSLidGuard:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._caffeinate = CaffeinateGuard()
        self._hotspot_recovery = HotspotRecoveryMonitor(config)
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
        self._caffeinate.start()
        self._hotspot_recovery.set_active(True)

    def _on_processes_idle(self) -> None:
        self._hotspot_recovery.set_active(False)
        self._caffeinate.stop()

    def _handle_lid_close(self) -> None:
        if self._caffeinate.active:
            log.info("Lid closed while protection is active. Locking screen.")
            lock_screen()
        else:
            log.info("Lid closed with no watched process running. Allowing normal macOS behavior.")

    def _handle_lid_open(self) -> None:
        log.info("Lid opened.")

    def _handle_signal(self, signum: int, _frame: object) -> None:
        log.info("Received signal %s. Shutting down.", signum)
        self._stop_event.set()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        state = read_lid_state()
        if state is None:
            log.warning("Could not read AppleClamshellState. Lid monitoring may not work on this Mac.")

        self._watcher.start()
        self._hotspot_recovery.start()
        self._monitor.start()
        log.info(
            "lid-guard is running on macOS. Watched processes: %s",
            ", ".join(self._config["watched_processes"]),
        )

        self._stop_event.wait()
        self._monitor.stop()
        self._watcher.stop()
        self._hotspot_recovery.stop()
        self._caffeinate.stop()
        log.info("lid-guard stopped.")


def _wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "en0"

    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if "Wi-Fi" in line or "AirPort" in line:
            for offset in range(index + 1, min(index + 4, len(lines))):
                if lines[offset].startswith("Device:"):
                    return lines[offset].split(":", 1)[1].strip()
    return "en0"


def _current_ip_address_via_ifconfig(interface: str) -> str | None:
    try:
        result = subprocess.run(
            ["ifconfig", interface],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        log.debug("Could not read Wi-Fi IP address via ifconfig: %s", exc)
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("inet "):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    return None
