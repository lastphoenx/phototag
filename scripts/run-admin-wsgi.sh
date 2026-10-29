#!/usr/bin/env bash
# Review-Admin (gunicorn) — fuer systemd phototag-admin.service und manuelle Tests.
set -eo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
PY="${INSTALL_DIR}/.venv/bin/python"

[[ -f "$ENV_FILE" ]] || {
  echo "FEHLT: $ENV_FILE (PHOTOTAG_ADMIN_USER/PASSWORD setzen)" >&2
  exit 1
}
[[ -x "$PY" ]] || {
  echo "FEHLT: $PY — python3 -m venv .venv && pip install -e \".[admin]\"" >&2
  exit 1
}

# phototag.env kann $ in Passwoertern enthalten — set -u beim source vermeiden.
set +u
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# run-admin-wsgi: immer LAN (phototag.env BIND=127.0.0.1 gilt nur fuer manuelles phototag serve).
export PHOTOTAG_ADMIN_BIND="${PHOTOTAG_ADMIN_SERVICE_BIND:-0.0.0.0}"
BIND="$PHOTOTAG_ADMIN_BIND"
PORT="${PHOTOTAG_ADMIN_PORT:-8765}"
WORKERS="${PHOTOTAG_ADMIN_GUNICORN_WORKERS:-1}"

if ! "$PY" -c "from phototag.admin_app import application" 2>&1; then
  echo "FEHLER: Admin-App startet nicht (Import/Login-Env). phototag.env pruefen." >&2
  exit 1
fi

echo "phototag-admin: gunicorn http://${BIND}:${PORT}/ (workers=${WORKERS})" >&2

exec "$PY" -m gunicorn \
  --bind "${BIND}:${PORT}" \
  --workers "$WORKERS" \
  --timeout 600 \
  --access-logfile - \
  --error-logfile - \
  phototag.admin_app:application
