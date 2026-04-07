#!/usr/bin/env python3
"""
lid-guard entry point — auto-detects Linux or macOS and runs the right daemon.

Usage:
  python3 run.py            # start the daemon
  python3 run.py --setup    # configure settings (hotspot, etc.)
"""
import sys

if "--setup" in sys.argv:
    from config import run_setup
    run_setup()
    sys.exit(0)

if sys.platform == "linux":
    from lid_guard import main
elif sys.platform == "darwin":
    from lid_guard_mac import main
else:
    print(f"Unsupported platform: {sys.platform}")
    print("lid-guard supports Linux and macOS only.")
    sys.exit(1)

main()
