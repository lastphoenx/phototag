#!/usr/bin/env bash
# Nacht-Lauf: scan + tag in Batches bis END-Stunde (Default 07:00) oder leere Queue.
# Liest phototag.env; blockiert Ollama nicht absichtlich (kleine Batches + Pause).
#
# Pflicht: PHOTOTAG_SCAN_ROOT (Mount/Cache mit gleichen Pfaden wie NC OF/…)
# Optional: PHOTOTAG_NIGHT_BATCH=20  PHOTOTAG_NIGHT_SLEEP=45  PHOTOTAG_NIGHT_END_HOUR=7
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
SCAN_ROOT="${PHOTOTAG_SCAN_ROOT:?PHOTOTAG_SCAN_ROOT setzen (z.B. /mnt/nextcloud-of)}"
BATCH="${PHOTOTAG_NIGHT_BATCH:-20}"
SLEEP_SEC="${PHOTOTAG_NIGHT_SLEEP:-45}"
END_HOUR="${PHOTOTAG_NIGHT_END_HOUR:-7}"

[[ -f "$ENV_FILE" ]] || { echo "Fehlt: $ENV_FILE" >&2; exit 1; }
[[ -d "$SCAN_ROOT" ]] || { echo "SCAN_ROOT nicht lesbar: $SCAN_ROOT" >&2; exit 1; }

hour_lt_end() {
  local h
  h="$(date +%-H 2>/dev/null || date +%H | sed 's/^0//')"
  [[ "$h" -lt "$END_HOUR" ]]
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$INSTALL_DIR"
PT="${INSTALL_DIR}/.venv/bin/phototag"
command -v sqlite3 >/dev/null || { echo "sqlite3 fehlt" >&2; exit 1; }

echo "phototag night: scan=$SCAN_ROOT db=${PHOTOTAG_DB:-?} batch=$BATCH bis ${END_HOUR}:00"

while hour_lt_end; do
  "$PT" scan "$SCAN_ROOT"
  pending="$(sqlite3 "$PHOTOTAG_DB" "SELECT COUNT(*) FROM images WHERE status IN ('new','error');")"
  if [[ "$pending" -eq 0 ]]; then
    echo "$(date -Is) nichts zu taggen — warte ${SLEEP_SEC}s (neue Dateien via scan)"
    sleep "$SLEEP_SEC"
    hour_lt_end || break
    continue
  fi
  echo "$(date -Is) taggen (pending=$pending, limit=$BATCH)"
  "$PT" tag --limit "$BATCH"
  hour_lt_end || break
  sleep "$SLEEP_SEC"
done

echo "$(date -Is) night-tag-loop Ende (Stunde >= ${END_HOUR})"
