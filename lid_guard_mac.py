#!/usr/bin/env python3
"""Compatibility shim for older macOS-specific entrypoints."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lidguard.cli import main as _main


def main() -> int:
    return _main(["run"])


if __name__ == "__main__":
    raise SystemExit(main())
