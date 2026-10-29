#!/usr/bin/env bash
# Stellt rclone-Mount fuer PHOTOTAG_SCAN_ROOT her (wenn PHOTOTAG_RCLONE_MOUNT gesetzt).
# Homelab: Remote-Name nur in phototag.env (z. B. nextcloud_OF: oder nextcloud_OF:OF).
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"

usage() {
  echo "Usage: $0 [--check]" >&2
  echo "  --check  exit 0 wenn Mount ok, sonst 1 (startet nicht)" >&2
  exit 2
}

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage
[[ -n "${1:-}" && "$CHECK_ONLY" -eq 0 ]] && usage

[[ -f "$ENV_FILE" ]] || { echo "Fehlt: $ENV_FILE" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MOUNT="${PHOTOTAG_SCAN_ROOT:-}"
SPEC="${PHOTOTAG_RCLONE_MOUNT:-}"

if [[ -z "$MOUNT" ]]; then
  echo "PHOTOTAG_SCAN_ROOT fehlt in $ENV_FILE" >&2
  exit 1
fi

if [[ -z "$SPEC" ]]; then
  echo "PHOTOTAG_RCLONE_MOUNT nicht gesetzt — manueller Mount noetig (siehe DEPLOY.md)." >&2
  if [[ -d "$MOUNT" ]] && mountpoint -q "$MOUNT" 2>/dev/null; then
    exit 0
  fi
  [[ -d "$MOUNT" ]] || { echo "SCAN_ROOT existiert nicht: $MOUNT" >&2; exit 1; }
  echo "Warnung: $MOUNT ist kein Mountpoint (nur lokaler Ordner?)" >&2
  exit 1
fi

if mountpoint -q "$MOUNT" 2>/dev/null; then
  exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "Mount fehlt: $MOUNT (PHOTOTAG_RCLONE_MOUNT=$SPEC)" >&2
  exit 1
fi

mkdir -p "$MOUNT"
if mountpoint -q "$MOUNT" 2>/dev/null; then
  exit 0
fi

# Alten haenger entfernen
if command -v fusermount >/dev/null 2>&1; then
  fusermount -uz "$MOUNT" 2>/dev/null || true
fi

EXTRA=()
if [[ -r /etc/fuse.conf ]] && grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null; then
  EXTRA+=(--allow-other)
fi

echo "rclone mount $SPEC -> $MOUNT"
rclone mount "$SPEC" "$MOUNT" \
  --vfs-cache-mode writes \
  --dir-cache-time 72h \
  --poll-interval 15s \
  "${EXTRA[@]}" \
  --daemon

for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if mountpoint -q "$MOUNT" 2>/dev/null; then
    echo "OK mount: $MOUNT"
    exit 0
  fi
done

echo "Mount fehlgeschlagen: $MOUNT" >&2
exit 1
