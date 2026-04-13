from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .config import DEFAULT_WATCHED_PROCESSES

log = logging.getLogger("lidguard.process_watcher")
WATCHED_PROCESSES = tuple(DEFAULT_WATCHED_PROCESSES)


def list_processes() -> list[tuple[int, str]]:
    try:
        return _list_processes_via_ps()
    except Exception as exc:
        log.debug("Process listing via ps failed: %s", exc)

    try:
        return _list_processes_via_proc()
    except Exception as exc:
        log.debug("Process listing via /proc failed: %s", exc)

    return []


def probe_process_listing() -> tuple[bool, str]:
    try:
        processes = _list_processes_via_ps()
        return True, f"Enumerated {len(processes)} processes with ps."
    except Exception as exc:
        log.debug("ps probe failed: %s", exc)

    try:
        processes = _list_processes_via_proc()
        return True, f"Enumerated {len(processes)} processes from /proc."
    except Exception as exc:
        log.debug("/proc probe failed: %s", exc)

    return False, "Could not enumerate processes via ps or /proc."


def find_matching_processes(
    candidates: Sequence[str],
    processes: Iterable[tuple[int, str]] | None = None,
    ignore_pids: set[int] | None = None,
) -> list[tuple[int, str]]:
    process_table = list(processes) if processes is not None else list_processes()
    ignored = ignore_pids or {os.getpid()}
    matches: list[tuple[int, str]] = []

    for pid, command in process_table:
        if pid in ignored:
            continue
        if any(command_matches(command, candidate) for candidate in candidates):
            matches.append((pid, command))

    return matches


def any_watched_running(
    processes: Sequence[str] = WATCHED_PROCESSES,
    process_table: Iterable[tuple[int, str]] | None = None,
    ignore_pids: set[int] | None = None,
) -> bool:
    return bool(find_matching_processes(processes, processes=process_table, ignore_pids=ignore_pids))


def command_matches(command: str, candidate: str) -> bool:
    target = candidate.strip().lower()
    if not target:
        return False

    normalized_command = command.lower()
    if target in normalized_command:
        return True

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for token in tokens:
        basename = Path(token).name.lower()
        if basename == target or basename.startswith(f"{target}-"):
            return True

    return False


class ProcessWatcher:
    """Poll for watched processes on a background thread."""

    def __init__(
        self,
        on_active: Callable[[], None],
        on_idle: Callable[[], None],
        processes: Sequence[str] = WATCHED_PROCESSES,
        poll_interval: float = 2.0,
    ) -> None:
        self._on_active = on_active
        self._on_idle = on_idle
        self._processes = list(processes)
        self._poll_interval = poll_interval
        self._active: bool | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_active(self) -> bool:
        return bool(self._active)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="lidguard-process-watcher",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 1)

    def _poll_loop(self) -> None:
        log.info(
            "Watching for processes: %s (polling every %.1fs)",
            ", ".join(self._processes),
            self._poll_interval,
        )

        while not self._stop_event.is_set():
            current = any_watched_running(self._processes)

            if self._active is None:
                self._active = current
                if current:
                    log.info("Watched process already running. Protection is active.")
                    self._call(self._on_active, "on_active")
                else:
                    log.info("No watched process running. Protection is inactive.")
            elif current and not self._active:
                self._active = True
                log.info("Watched process detected. Protection activated.")
                self._call(self._on_active, "on_active")
            elif not current and self._active:
                self._active = False
                log.info("All watched processes stopped. Protection deactivated.")
                self._call(self._on_idle, "on_idle")

            self._stop_event.wait(self._poll_interval)

    def _call(self, callback: Callable[[], None], label: str) -> None:
        try:
            callback()
        except Exception:
            log.exception("Process watcher callback %s failed.", label)


def _list_processes_via_ps() -> list[tuple[int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown ps error"
        raise RuntimeError(stderr)

    processes: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes


def _list_processes_via_proc() -> list[tuple[int, str]]:
    processes: list[tuple[int, str]] = []
    proc_dir = Path("/proc")
    if not proc_dir.exists():
        raise FileNotFoundError("/proc is not available")

    for cmdline_path in proc_dir.glob("*/cmdline"):
        try:
            command = cmdline_path.read_bytes().replace(b"\x00", b" ").decode(errors="ignore").strip()
        except OSError:
            continue
        if not command:
            continue
        try:
            pid = int(cmdline_path.parent.name)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes

