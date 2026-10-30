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
#   PHOTOTAG_NIGHT_FORCE=1          eine Runde scan+tag (ignoriert END_HOUR) — siehe run-night-catchup.sh
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
SELF_TEST=0
[[ "${1:-}" == "--self-test" ]] && SELF_TEST=1
BATCH="${PHOTOTAG_NIGHT_BATCH:-20}"
SLEEP_SEC="${PHOTOTAG_NIGHT_SLEEP:-45}"
END_HOUR="${PHOTOTAG_NIGHT_END_HOUR:-7}"
NIGHT_KIND="${PHOTOTAG_NIGHT_KIND:-}"
IMAGES_FIRST="${PHOTOTAG_NIGHT_IMAGES_FIRST:-0}"
FORCE="${PHOTOTAG_NIGHT_FORCE:-0}"

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

current_hour() {
  date +%-H 2>/dev/null || date +%H | sed 's/^0//'
}

hour_lt_end() {
  local h
  h="$(current_hour)"
  [[ "$h" -lt "$END_HOUR" ]]
}

cd "$INSTALL_DIR"
export PYTHONUNBUFFERED=1
PT="${INSTALL_DIR}/.venv/bin/phototag"
PY="${INSTALL_DIR}/.venv/bin/python"
[[ -x "$PY" ]] || { echo "FEHLT: $PY" >&2; exit 1; }
[[ -x "$PT" ]] || { echo "FEHLT: $PT" >&2; exit 1; }

pending_count() {
  local kind="${1:-}"
  "$PY" - "$kind" <<'PY'
import os
import sqlite3
import sys

db = os.environ.get("PHOTOTAG_DB") or "phototag.db"
kind = (sys.argv[1] if len(sys.argv) > 1 else "") or None
con = sqlite3.connect(db)
if kind:
    row = con.execute(
        "SELECT COUNT(*) FROM images WHERE status IN ('new','error') AND kind=?",
        (kind,),
    ).fetchone()
else:
    row = con.execute(
        "SELECT COUNT(*) FROM images WHERE status IN ('new','error')",
    ).fetchone()
print(row[0])
PY
}

if [[ "$SELF_TEST" -eq 1 ]]; then
  echo "self-test: Stunde=$(current_hour) END_HOUR=$END_HOUR FORCE=$FORCE db=${PHOTOTAG_DB:-?}"
  echo "self-test: pending new/error=$(pending_count)"
  exit 0
fi

mkdir -p "${INSTALL_DIR}/state"
LOCKFILE="${INSTALL_DIR}/state/night-tag.lock"
exec 200>"$LOCKFILE"
if ! flock -n 200; then
  echo "FEHLER: night-tag-loop läuft bereits (Lock: $LOCKFILE)." >&2
  echo "  Prüfen: systemctl status phototag-night.service" >&2
  echo "  Nicht parallel: run-night-catchup.sh oder zweites systemctl start." >&2
  exit 1
fi

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

run_tag() {
  local kind_flag=()
  if [[ -n "$1" ]]; then
    kind_flag=(--kind "$1")
  elif [[ -n "$NIGHT_KIND" ]]; then
    kind_flag=(--kind "$NIGHT_KIND")
  fi
  "$PT" tag --limit "$BATCH" "${kind_flag[@]}"
}

NOW_H="$(current_hour)"
echo "$(date -Is) night-tag-loop: Stunde=$NOW_H END_HOUR=$END_HOUR FORCE=$FORCE scan=$SCAN_ROOT db=${PHOTOTAG_DB:-?} batch=$BATCH sleep=${SLEEP_SEC}s images_first=$IMAGES_FIRST kind=${NIGHT_KIND:-all}"

if [[ "$FORCE" != 1 ]] && ! hour_lt_end; then
  echo "FEHLER: Nacht-Fenster zu — Stunde $NOW_H ist nicht < END_HOUR=$END_HOUR (läuft typisch 00:00–06:59)." >&2
  echo "  Catch-up (eine Runde scan+tag, im Vordergrund):" >&2
  echo "    ${INSTALL_DIR}/scripts/run-night-catchup.sh" >&2
  exit 1
fi

rounds=0
while [[ "$FORCE" == 1 ]] || hour_lt_end; do
  rounds=$((rounds + 1))
  echo "$(date -Is) Runde $rounds: scan $SCAN_ROOT …"
  "$PT" scan "$SCAN_ROOT"
  pending="$(pending_count)"
  if [[ "$pending" -eq 0 ]]; then
    echo "$(date -Is) nichts zu taggen — warte ${SLEEP_SEC}s (scan erneut)"
    if [[ "$FORCE" == 1 ]]; then
      echo "$(date -Is) FORCE: keine pending nach scan — Ende."
      break
    fi
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
  if [[ "$FORCE" == 1 ]]; then
    echo "$(date -Is) FORCE: eine Runde fertig (scan + tag batch)."
    break
  fi
  hour_lt_end || break
  sleep "$SLEEP_SEC"
done

if [[ "$rounds" -eq 0 ]]; then
  echo "FEHLER: 0 Runden ausgeführt (Stunde=$NOW_H END_HOUR=$END_HOUR)." >&2
  exit 1
fi

echo "$(date -Is) night-tag-loop Ende (Stunde=$(current_hour), END_HOUR=$END_HOUR, Runden=$rounds)"
