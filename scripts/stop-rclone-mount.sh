#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
[[ -f "$ENV_FILE" ]] || exit 0
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
MOUNT="${PHOTOTAG_SCAN_ROOT:-}"
[[ -n "$MOUNT" ]] || exit 0
if mountpoint -q "$MOUNT" 2>/dev/null; then
  if command -v fusermount >/dev/null 2>&1; then
    fusermount -u "$MOUNT" || fusermount -uz "$MOUNT"
  else
    umount "$MOUNT"
  fi
fi
