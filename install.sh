#!/usr/bin/env bash
# install.sh — Install lid-guard as a systemd user service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/lid-guard.service"

echo "=== lid-guard installer ==="
echo ""

# ── Dependency check ─────────────────────────────────────────────────────────

check_dep() {
    if ! command -v "$1" &>/dev/null; then
        echo "  [MISSING] $1 — $2"
        return 1
    else
        echo "  [OK]      $1"
        return 0
    fi
}

echo "Checking dependencies..."
check_dep python3         "required — install python3" || MISSING=1
check_dep loginctl        "recommended locker — part of systemd" || true
check_dep xdg-screensaver "fallback locker" || true

# Check python3-dbus
if python3 -c "import dbus" 2>/dev/null; then
    echo "  [OK]      python3-dbus"
else
    echo "  [MISSING] python3-dbus — install with:"
    echo "            Ubuntu/Debian: sudo apt install python3-dbus"
    echo "            Fedora:        sudo dnf install python3-dbus"
    echo "            Arch:          sudo pacman -S python-dbus"
    echo ""
    echo "  Without python3-dbus the inhibitor lock won't work and"
    echo "  logind may still sleep on lid close."
fi

echo ""

# Check /proc/acpi lid support
if ls /proc/acpi/button/lid/*/state &>/dev/null 2>&1; then
    STATE=$(cat /proc/acpi/button/lid/*/state 2>/dev/null | head -1)
    echo "  [OK]      /proc/acpi/button/lid — current state: $STATE"
else
    echo "  [WARN]    /proc/acpi/button/lid not found"
    echo "            Lid events may not be detectable on this machine"
fi

echo ""

# ── Install systemd user service ─────────────────────────────────────────────

mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=lid-guard — lock screen on lid close, keep laptop awake
Documentation=https://github.com/jasonlevigoodison/alwaysgrinding
After=graphical-session.target

[Service]
Type=simple
ExecStart=${PYTHON:-python3} ${SCRIPT_DIR}/lid_guard.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=graphical-session.target
EOF

echo "Installed service → $SERVICE_FILE"

# ── Enable and start ──────────────────────────────────────────────────────────

systemctl --user daemon-reload
systemctl --user enable lid-guard.service
systemctl --user start  lid-guard.service

echo ""
echo "=== lid-guard is running! ==="
echo ""
echo "Useful commands:"
echo "  Status  : systemctl --user status lid-guard"
echo "  Logs    : journalctl --user -u lid-guard -f"
echo "  Stop    : systemctl --user stop lid-guard"
echo "  Disable : systemctl --user disable --now lid-guard"
echo ""
echo "To run without installing:"
echo "  python3 ${SCRIPT_DIR}/lid_guard.py"
