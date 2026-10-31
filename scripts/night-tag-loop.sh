#!/usr/bin/env bash
# Nacht-Lauf: nur taggen (DB status new/error) in Batches bis END-Stunde (Default 07:00).
# Kein Voll-Scan am Job-Start — neue Dateien auf dem Mount: manuell `phototag scan` tagsüber.
# Liest phototag.env; blockiert Ollama nicht absichtlich (kleine Batches + Pause).
#
# Pflicht: PHOTOTAG_SCAN_ROOT in phototag.env (Mount für tag/faceblur — kein fester Pfad im Repo)
# Optional (phototag.env oder systemd Environment=):
#   PHOTOTAG_NIGHT_BATCH=20     Dateien pro tag-Aufruf (Default 20)
#   PHOTOTAG_NIGHT_SLEEP=45     Pause Sekunden zwischen Runden (Default 45)
#   PHOTOTAG_NIGHT_END_HOUR=7   Stop ab dieser Stunde (0–6 Uhr läuft)
#   PHOTOTAG_NIGHT_KIND=image   nur Bilder | video | leer = beides
#   PHOTOTAG_NIGHT_IMAGES_FIRST=1   erst alle Bilder, dann Videos/Fehler
#   PHOTOTAG_NIGHT_FORCE=1          eine Tag-Batch (ignoriert END_HOUR) — siehe run-night-catchup.sh
#   PHOTOTAG_NIGHT_SCAN_AT_START=1  optional ein scan vor dem Taggen (Default: 0)
#   PHOTOTAG_NIGHT_RESCAN_WHEN_IDLE=1  bei leerer Queue erneut scannen (Default: 0)
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
SCAN_AT_START="${PHOTOTAG_NIGHT_SCAN_AT_START:-0}"
RESCAN_IDLE="${PHOTOTAG_NIGHT_RESCAN_WHEN_IDLE:-0}"

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
export PHOTOTAG_SCAN_PROGRESS="${INSTALL_DIR}/state/scan-progress.log"
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
  local kind_arg="${1:-}"
  local kind_flag=()
  if [[ -n "$kind_arg" ]]; then
    kind_flag=(--kind "$kind_arg")
  elif [[ -n "$NIGHT_KIND" ]]; then
    kind_flag=(--kind "$NIGHT_KIND")
  fi
  "$PT" tag --limit "$BATCH" "${kind_flag[@]}"
}

NOW_H="$(current_hour)"
START_PENDING="$(pending_count)"
echo "$(date -Is) night-tag-loop: Stunde=$NOW_H END_HOUR=$END_HOUR FORCE=$FORCE pending=$START_PENDING scan_at_start=$SCAN_AT_START rescan_idle=$RESCAN_IDLE"
echo "  scan_root=$SCAN_ROOT db=${PHOTOTAG_DB:-?} batch=$BATCH sleep=${SLEEP_SEC}s images_first=$IMAGES_FIRST kind=${NIGHT_KIND:-all}"

if [[ "$FORCE" != 1 ]] && ! hour_lt_end; then
  echo "FEHLER: Nacht-Fenster zu — Stunde $NOW_H ist nicht < END_HOUR=$END_HOUR (läuft typisch 00:00–06:59)." >&2
  echo "  Catch-up (eine Tag-Batch, im Vordergrund):" >&2
  echo "    ${INSTALL_DIR}/scripts/run-night-catchup.sh" >&2
  echo "  Länger tagsüber (END_HOUR temporär):" >&2
  echo "    PHOTOTAG_NIGHT_END_HOUR=24 ${INSTALL_DIR}/scripts/night-tag-loop.sh" >&2
  exit 1
fi

if [[ "$SCAN_AT_START" == 1 ]]; then
  echo "$(date -Is) PHOTOTAG_NIGHT_SCAN_AT_START=1 — scan $SCAN_ROOT (Fortschritt: tail -f ${PHOTOTAG_SCAN_PROGRESS})"
  "$PT" scan "$SCAN_ROOT"
else
  echo "$(date -Is) kein Nacht-Scan — tagge nur DB-Queue (status new/error). Neue Medien: tagsüber \`phototag scan\`."
fi

rounds=0
while [[ "$FORCE" == 1 ]] || hour_lt_end; do
  rounds=$((rounds + 1))
  pending="$(pending_count)"
  if [[ "$pending" -eq 0 ]]; then
    if [[ "$RESCAN_IDLE" == 1 ]] && [[ "$FORCE" != 1 ]]; then
      echo "$(date -Is) Queue leer — optional rescan (PHOTOTAG_NIGHT_RESCAN_WHEN_IDLE=1)"
      "$PT" scan "$SCAN_ROOT"
      pending="$(pending_count)"
    fi
    if [[ "$pending" -eq 0 ]]; then
      echo "$(date -Is) nichts zu taggen — Ende (kein erneuter Voll-Scan)."
      if [[ "$FORCE" == 1 ]]; then
        echo "$(date -Is) FORCE: fertig."
      fi
      break
    fi
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
    echo "$(date -Is) Runde $rounds: taggen (pending=$pending, limit=$BATCH)"
    run_tag
  fi
  if [[ "$FORCE" == 1 ]]; then
    echo "$(date -Is) FORCE: eine Tag-Batch — Ende."
    break
  fi
  hour_lt_end || break
  sleep "$SLEEP_SEC"
done

if [[ "$rounds" -eq 0 ]]; then
  echo "$(date -Is) 0 Tag-Runden — Queue leer (pending war $START_PENDING). Nacht-Job ok."
  exit 0
fi

echo "$(date -Is) night-tag-loop Ende (Stunde=$(current_hour), END_HOUR=$END_HOUR, Runden=$rounds)"
