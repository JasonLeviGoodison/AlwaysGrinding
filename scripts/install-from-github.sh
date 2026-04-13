#!/usr/bin/env bash
# Install lid-guard directly from GitHub without cloning the repo first.

set -euo pipefail

REPO="${LID_GUARD_REPO:-JasonLeviGoodison/AlwaysGrinding}"
VERSION="${LID_GUARD_VERSION:-}"
PYTHON_BIN="${PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 is required to install lid-guard" >&2
  exit 1
fi

if [[ -n "$VERSION" ]]; then
  ARCHIVE_URL="https://github.com/${REPO}/archive/refs/tags/${VERSION}.tar.gz"
else
  ARCHIVE_URL="https://github.com/${REPO}/archive/refs/heads/main.tar.gz"
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE_FILE="$TMP_DIR/lid-guard.tar.gz"

echo "Downloading lid-guard from $ARCHIVE_URL"
curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE_FILE"
tar -xzf "$ARCHIVE_FILE" -C "$TMP_DIR"

SOURCE_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ ! -f "$SOURCE_DIR/install.sh" ]]; then
  echo "Downloaded archive did not contain install.sh" >&2
  exit 1
fi

exec env PYTHON="$PYTHON_BIN" "$SOURCE_DIR/install.sh"
