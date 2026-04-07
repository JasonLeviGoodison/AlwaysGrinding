#!/usr/bin/env python3
"""
lid-guard entry point — auto-detects Linux or macOS and runs the right daemon.

Usage:
  python3 run.py
"""
import sys

if sys.platform == "linux":
    from lid_guard import main
elif sys.platform == "darwin":
    from lid_guard_mac import main
else:
    print(f"Unsupported platform: {sys.platform}")
    print("lid-guard supports Linux and macOS only.")
    sys.exit(1)

main()
