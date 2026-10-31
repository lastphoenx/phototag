#!/usr/bin/env bash
# Eine Tag-Batch tagsüber (Nacht-Fenster ignorieren). Kein systemd nötig.
# Nutzen wenn der Timer um 00:00 fehlgeschlagen ist oder nach 07:00 noch Batch nötig.
# Optional vorher indexieren: PHOTOTAG_NIGHT_SCAN_AT_START=1 phototag.env oder einmal `phototag scan`.
set -euo pipefail
INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
export PHOTOTAG_NIGHT_FORCE=1
exec "${INSTALL_DIR}/scripts/night-tag-loop.sh"
