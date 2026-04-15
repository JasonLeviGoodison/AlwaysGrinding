#!/usr/bin/env bash
# install.sh — Install lid-guard as a standalone CLI command.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${LID_GUARD_INSTALL_ROOT:-$HOME/.local/share/lid-guard}"
BIN_DIR="${LID_GUARD_BIN_DIR:-$HOME/.local/bin}"
APP_FILE="$INSTALL_ROOT/lid-guard.pyz"
PYTHON_BIN="${PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 is required to install lid-guard" >&2
  exit 1
fi

echo "Building lid-guard standalone archive"
"$PYTHON_BIN" "$SCRIPT_DIR/scripts/build_zipapp.py" >/dev/null

mkdir -p "$INSTALL_ROOT"
cp "$SCRIPT_DIR/dist/lid-guard.pyz" "$APP_FILE"
chmod +x "$APP_FILE"

mkdir -p "$BIN_DIR"
ln -sf "$APP_FILE" "$BIN_DIR/lid-guard"

echo
echo "Installed command: $BIN_DIR/lid-guard"
echo "Archive path:      $APP_FILE"
echo
echo "Next steps:"
echo "  1. Ensure $BIN_DIR is on your PATH"
echo "  2. Run: lid-guard doctor"
echo "  3. Run: lid-guard run"
echo "     First run starts the interactive onboarding menu."
echo "  4. Optional: lid-guard service install"
