#!/usr/bin/env bash
# Nach update-from-github.sh: Version, phototag.env vs .env.example.
# Keine Tag-Inhalte aus dem Repo — categories.txt ist privat (Homelab/doku).
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
EXAMPLE="${INSTALL_DIR}/.env.example"

cd "$INSTALL_DIR"
VER="$(.venv/bin/python -c 'import importlib.metadata as m; print(m.version("phototag"))' 2>/dev/null || echo '?')"
echo "=== phototag post-update ($VER) ==="

if [[ -f CHANGELOG.md ]]; then
  echo ""
  echo "Release-Hinweise: less CHANGELOG.md  (Abschnitt $VER)"
fi
if [[ -f DEPLOY.md ]]; then
  echo "Deploy-Runbook:   cat DEPLOY.md"
fi

echo ""
if [[ -x scripts/sync-phototag-env.sh ]]; then
  scripts/sync-phototag-env.sh || true
else
  echo "Warnung: scripts/sync-phototag-env.sh fehlt" >&2
fi

# shellcheck source=lib/env-keys.sh
source "${INSTALL_DIR}/scripts/lib/env-keys.sh"
if [[ -f "$ENV_FILE" && -f "$EXAMPLE" ]]; then
  mapfile -t have < <(env_keys_from_file "$ENV_FILE")
  mapfile -t want < <(env_keys_from_file "$EXAMPLE")
  miss=0
  for k in "${want[@]}"; do
    ok=0
    for h in "${have[@]}"; do [[ "$h" == "$k" ]] && ok=1; done
    if [[ "$ok" -eq 0 ]]; then miss=1; fi
  done
  if [[ "$miss" -eq 1 ]]; then
    echo ""
    echo ">>> phototag.env nachziehen: ./scripts/sync-phototag-env.sh --apply && nano phototag.env"
  fi
fi

echo ""
echo "=== categories.txt (nur lokal auf dem Server, nie im GitHub-Repo) ==="
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
CAT_FILE="${PHOTOTAG_CATEGORIES:-${INSTALL_DIR}/categories.txt}"
if [[ ! -f "$CAT_FILE" ]]; then
  echo "FEHLT: $CAT_FILE — cp categories.example.txt categories.txt && anpassen" >&2
else
  n="$(grep -cE '^[[:space:]]*[a-z][a-z0-9_]*[[:space:]]*:' "$CAT_FILE" 2>/dev/null || echo 0)"
  echo "OK: $CAT_FILE ($n Tag-Zeilen). Taxonomie-Aenderungen: privates Homelab-doku, nicht phototag GitHub."
fi

REQ_LOCAL="${PHOTOTAG_REQUIRED_TAGS_FILE:-}"
if [[ -n "$REQ_LOCAL" && -f "$REQ_LOCAL" ]]; then
  lack=0
  while IFS= read -r tag || [[ -n "${tag:-}" ]]; do
    [[ -z "${tag:-}" || "$tag" =~ ^# ]] && continue
    grep -qE "^[[:space:]]*${tag}[[:space:]]*:" "$CAT_FILE" 2>/dev/null || lack=$((lack + 1))
  done < "$REQ_LOCAL"
  if [[ "$lack" -gt 0 ]]; then
    echo "Hinweis: $lack Tag-ID(s) aus PHOTOTAG_REQUIRED_TAGS_FILE fehlen in categories.txt (Details nur lokal)."
  fi
fi

echo ""
echo "=== Review-Admin (Web-UI) ==="
if [[ -f "$ENV_FILE" ]]; then
  au="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_USER=' "$ENV_FILE" | head -1 || true)"
  ap="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_PASSWORD=' "$ENV_FILE" | head -1 || true)"
  ah="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_PASSWORD_HASH=' "$ENV_FILE" | head -1 || true)"
  ab="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_BIND=' "$ENV_FILE" | head -1 || true)"
  if [[ -z "$au" ]]; then
    echo "FEHLT: PHOTOTAG_ADMIN_USER=... in phototag.env (Web-UI-Login)."
  else
    echo "OK: PHOTOTAG_ADMIN_USER gesetzt."
  fi
  if [[ -z "$ap" && -z "$ah" ]]; then
    echo "FEHLT: PHOTOTAG_ADMIN_PASSWORD=... oder PHOTOTAG_ADMIN_PASSWORD_HASH=..."
  else
    echo "OK: Admin-Passwort (Klartext oder Hash) gesetzt."
  fi
  if [[ "$ab" == *"127.0.0.1"* ]]; then
    echo "HINWEIS: phototag.env BIND=127.0.0.1 — manuelles serve nur lokal; Dienst: ./scripts/install-admin-service.sh (BIND 0.0.0.0)."
  elif [[ -n "$ab" ]]; then
    echo "BIND: $ab"
  else
    echo "BIND: nicht gesetzt → Code/systemd-Default 0.0.0.0 (LAN)."
  fi
  echo "Dauerbetrieb: ./scripts/install-admin-service.sh  |  Browser: http://<CT-IP>:8765/"
  if systemctl is-enabled phototag-admin.service >/dev/null 2>&1; then
    echo "phototag-admin.service: enabled — nach Code-Update: systemctl restart phototag-admin.service"
  else
    echo "phototag-admin.service: noch nicht installiert (install-admin-service.sh)."
  fi
fi

echo ""
echo "=== Nacht-Timer (optional) ==="
if systemctl is-enabled phototag-night.timer >/dev/null 2>&1; then
  echo "phototag-night.timer: enabled — Self-Test: ${INSTALL_DIR}/scripts/night-tag-loop.sh --self-test"
  echo "  Catch-up tagsüber: ${INSTALL_DIR}/scripts/run-night-catchup.sh"
else
  echo "phototag-night.timer: nicht aktiv (DEPLOY.md §5)."
fi

echo ""
echo "=== fertig ==="
