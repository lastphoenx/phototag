#!/usr/bin/env bash
# Ergaenzt phototag.env um fehlende Keys aus .env.example (aus .example als Kommentarzeilen).
# Ueberschreibt keine bestehenden Werte.
#
#   ./scripts/sync-phototag-env.sh           # zeigt Diff
#   ./scripts/sync-phototag-env.sh --apply   # haengt fehlende Example-Zeilen an
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
EXAMPLE="${INSTALL_DIR}/.env.example"
APPLY=0
CATALOG=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --catalog) CATALOG=1 ;;
  esac
done

[[ -f "$EXAMPLE" ]] || { echo "Fehlt: $EXAMPLE" >&2; exit 1; }

if [[ "$CATALOG" -eq 1 ]]; then
  echo "Optionale / dokumentierte Zeilen in .env.example (muessen nicht in phototag.env stehen):"
  echo "---"
  grep -E '^[[:space:]]*#' "$EXAMPLE" | grep -E 'PHOTOTAG_|Pin|Nextcloud|Nacht|Ollama' || true
  echo "---"
  echo "Vollstaendige Referenz: docs/phototag-env.reference.txt und DEPLOY.md"
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  echo "Angelegt: $ENV_FILE (aus .env.example) — Werte anpassen, chmod 600."
  exit 0
fi

# shellcheck source=lib/env-keys.sh
source "${INSTALL_DIR}/scripts/lib/env-keys.sh"

mapfile -t have < <(env_keys_from_file "$ENV_FILE")
mapfile -t want < <(env_keys_from_file "$EXAMPLE")

missing=()
for k in "${want[@]}"; do
  found=0
  for h in "${have[@]}"; do
    [[ "$h" == "$k" ]] && { found=1; break; }
  done
  [[ "$found" -eq 0 ]] && missing+=("$k")
done

# Nextcloud: Token-Datei oder Inline-App-Passwort — eines reicht
if printf '%s\n' "${have[@]}" | grep -qx 'PHOTOTAG_NC_TOKEN_FILE'; then
  tmp=()
  for k in "${missing[@]}"; do
    [[ "$k" == "PHOTOTAG_NC_APP_PASSWORD" ]] && continue
    tmp+=("$k")
  done
  missing=("${tmp[@]}")
fi
if printf '%s\n' "${have[@]}" | grep -qx 'PHOTOTAG_NC_APP_PASSWORD'; then
  tmp=()
  for k in "${missing[@]}"; do
    [[ "$k" == "PHOTOTAG_NC_TOKEN_FILE" ]] && continue
    tmp+=("$k")
  done
  missing=("${tmp[@]}")
fi

if [[ ${#missing[@]} -eq 0 ]]; then
  echo "phototag.env: Key-Namen aus .env.example sind vorhanden (Kommentarzeilen zaehlen)."
  echo "  Das heisst NICHT, dass Werte gesetzt sind — z.B. PHOTOTAG_ADMIN_USER/PASSWORD fuer phototag serve pruefen."
  exit 0
fi

echo "Fehlende Keys in phototag.env (stehen in .env.example):"
printf '  %s\n' "${missing[@]}"

if [[ "$APPLY" -ne 1 ]]; then
  echo ""
  echo "Anhaengen: ./scripts/sync-phototag-env.sh --apply"
  exit 0
fi

{
  echo ""
  echo "# --- ergaenzt $(date -Is) aus .env.example (phototag sync) ---"
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && { echo "$line"; continue; }
    [[ "$line" =~ ^[[:space:]]*PHOTOTAG_[A-Z0-9_]+= ]] || continue
    key="${line%%=*}"
    key="${key// /}"
    for m in "${missing[@]}"; do
      if [[ "$key" == "$m" ]]; then
        echo "# $line"
        break
      fi
    done
  done < "$EXAMPLE"
} >> "$ENV_FILE"

echo "Angehaengt an $ENV_FILE — bitte Kommentare (#) bei Bedarf entfernen und Werte setzen."
