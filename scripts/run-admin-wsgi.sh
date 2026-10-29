#!/usr/bin/env bash
# Review-Admin (gunicorn) — fuer systemd phototag-admin.service und manuelle Tests.
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"

[[ -f "$ENV_FILE" ]] || {
  echo "FEHLT: $ENV_FILE (PHOTOTAG_ADMIN_USER/PASSWORD setzen)" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

BIND="${PHOTOTAG_ADMIN_BIND:-0.0.0.0}"
PORT="${PHOTOTAG_ADMIN_PORT:-8765}"
WORKERS="${PHOTOTAG_ADMIN_GUNICORN_WORKERS:-2}"
GUNICORN="${INSTALL_DIR}/.venv/bin/gunicorn"

[[ -x "$GUNICORN" ]] || {
  echo "FEHLT: $GUNICORN — pip install -e \".[admin]\" in $INSTALL_DIR" >&2
  exit 1
}

exec "$GUNICORN" \
  --bind "${BIND}:${PORT}" \
  --workers "$WORKERS" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  --factory \
  phototag.admin_app:create_app
