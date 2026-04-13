"""Compatibility shim for older imports."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lidguard.process_watcher import *  # noqa: F401,F403
