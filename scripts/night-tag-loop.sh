#!/usr/bin/env bash
# Nacht-Lauf: scan + tag in Batches bis END-Stunde (Default 07:00) oder leere Queue.
# Liest phototag.env; blockiert Ollama nicht absichtlich (kleine Batches + Pause).
#
# Pflicht: PHOTOTAG_SCAN_ROOT in phototag.env (Mount — kein fester Pfad im Repo)
# Optional (phototag.env oder systemd Environment=):
#   PHOTOTAG_NIGHT_BATCH=20     Dateien pro tag-Aufruf (Default 20)
#   PHOTOTAG_NIGHT_SLEEP=45     Pause Sekunden zwischen Runden (Default 45)
#   PHOTOTAG_NIGHT_END_HOUR=7   Stop ab dieser Stunde (0–6 Uhr läuft)
#   PHOTOTAG_NIGHT_KIND=image   nur Bilder | video | leer = beides
#   PHOTOTAG_NIGHT_IMAGES_FIRST=1   erst alle Bilder, dann Videos/Fehler
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
BATCH="${PHOTOTAG_NIGHT_BATCH:-20}"
SLEEP_SEC="${PHOTOTAG_NIGHT_SLEEP:-45}"
END_HOUR="${PHOTOTAG_NIGHT_END_HOUR:-7}"
NIGHT_KIND="${PHOTOTAG_NIGHT_KIND:-}"
IMAGES_FIRST="${PHOTOTAG_NIGHT_IMAGES_FIRST:-0}"

[[ -f "$ENV_FILE" ]] || { echo "Fehlt: $ENV_FILE" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

SCAN_ROOT="${PHOTOTAG_SCAN_ROOT:-}"
[[ -n "$SCAN_ROOT" ]] || {
  echo "PHOTOTAG_SCAN_ROOT fehlt in $ENV_FILE (Mount-Pfad zum Medienbaum, nicht der Remote-Name)." >&2
  exit 1
}

if [[ -x "${INSTALL_DIR}/scripts/ensure-rclone-mount.sh" ]]; then
  "${INSTALL_DIR}/scripts/ensure-rclone-mount.sh" || exit 1
fi

if ! mountpoint -q "$SCAN_ROOT" 2>/dev/null; then
  if [[ ! -d "$SCAN_ROOT" ]]; then
    echo "SCAN_ROOT fehlt: $SCAN_ROOT (PHOTOTAG_RCLONE_MOUNT setzen oder manuell mounten)" >&2
    exit 1
  fi
  echo "Warnung: $SCAN_ROOT ist kein Mountpoint — scan/tag liest evtl. leeren Ordner." >&2
fi

hour_lt_end() {
  local h
  h="$(date +%-H 2>/dev/null || date +%H | sed 's/^0//')"
  [[ "$h" -lt "$END_HOUR" ]]
}

cd "$INSTALL_DIR"
PT="${INSTALL_DIR}/.venv/bin/phototag"
command -v sqlite3 >/dev/null || { echo "sqlite3 fehlt" >&2; exit 1; }

run_tag() {
  local kind_flag=()
  if [[ -n "$1" ]]; then
    kind_flag=(--kind "$1")
  elif [[ -n "$NIGHT_KIND" ]]; then
    kind_flag=(--kind "$NIGHT_KIND")
  fi
  "$PT" tag --limit "$BATCH" "${kind_flag[@]}"
}

pending_count() {
  local kind_sql="${1:-}"
  if [[ -n "$kind_sql" ]]; then
    sqlite3 "$PHOTOTAG_DB" \
      "SELECT COUNT(*) FROM images WHERE status IN ('new','error') AND kind='$kind_sql';"
  else
    sqlite3 "$PHOTOTAG_DB" \
      "SELECT COUNT(*) FROM images WHERE status IN ('new','error');"
  fi
}

echo "phototag night: scan=$SCAN_ROOT db=${PHOTOTAG_DB:-?} batch=$BATCH sleep=${SLEEP_SEC}s bis ${END_HOUR}:00 images_first=$IMAGES_FIRST kind=${NIGHT_KIND:-all}"

while hour_lt_end; do
  "$PT" scan "$SCAN_ROOT"
  pending="$(pending_count)"
  if [[ "$pending" -eq 0 ]]; then
    echo "$(date -Is) nichts zu taggen — warte ${SLEEP_SEC}s (scan erneut)"
    sleep "$SLEEP_SEC"
    hour_lt_end || break
    continue
  fi
  if [[ "$IMAGES_FIRST" == 1 ]]; then
    pimg="$(pending_count image)"
    if [[ "$pimg" -gt 0 ]]; then
      echo "$(date -Is) taggen Bilder (pending image=$pimg, gesamt=$pending, limit=$BATCH)"
      run_tag image
    else
      echo "$(date -Is) taggen Rest (Videos/Fehler, pending=$pending, limit=$BATCH)"
      run_tag
    fi
  else
    echo "$(date -Is) taggen (pending=$pending, limit=$BATCH)"
    run_tag
  fi
  hour_lt_end || break
  sleep "$SLEEP_SEC"
done

echo "$(date -Is) night-tag-loop Ende (Stunde >= ${END_HOUR})"
