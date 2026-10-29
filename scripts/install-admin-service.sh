#!/usr/bin/env bash
# Einmalig auf dem CT: Review-Admin als Dauer-Webdienst (systemd + gunicorn).
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
UNIT_SRC="${INSTALL_DIR}/scripts/systemd/phototag-admin.service"
UNIT_DST="/etc/systemd/system/phototag-admin.service"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"

[[ -f "$UNIT_SRC" ]] || { echo "FEHLT: $UNIT_SRC" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "FEHLT: $ENV_FILE — zuerst anlegen (siehe .env.example)" >&2; exit 1; }

cd "$INSTALL_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -e ".[admin]"

au="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_USER=' "$ENV_FILE" | head -1 || true)"
ap="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_PASSWORD=' "$ENV_FILE" | head -1 || true)"
ah="$(grep -E '^[[:space:]]*PHOTOTAG_ADMIN_PASSWORD_HASH=' "$ENV_FILE" | head -1 || true)"
if [[ -z "$au" ]]; then
  echo "FEHLT: PHOTOTAG_ADMIN_USER= in $ENV_FILE" >&2
  exit 1
fi
if [[ -z "$ap" && -z "$ah" ]]; then
  echo "FEHLT: Admin-Passwort in phototag.env (Klartext- oder Hash-Key, siehe DEPLOY.md)" >&2
  exit 1
fi

if grep -E '^[[:space:]]*PHOTOTAG_ADMIN_BIND=127\.0\.0\.1' "$ENV_FILE" >/dev/null 2>&1; then
  echo "Hinweis: phototag.env PHOTOTAG_ADMIN_BIND=127.0.0.1 — gilt nur fuer manuelles «phototag serve»."
  echo "  systemd/run-admin-wsgi.sh lauscht trotzdem auf 0.0.0.0 (LAN)."
fi

install -m 644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
systemctl enable phototag-admin.service
systemctl restart phototag-admin.service
sleep 2
if ! systemctl is-active --quiet phototag-admin.service; then
  echo "" >&2
  echo "FEHLER: phototag-admin.service startet nicht. Letzte Logs:" >&2
  journalctl -u phototag-admin.service -n 40 --no-pager >&2 || true
  echo "" >&2
  echo "Manuell testen (nur wenn Dienst gestoppt): systemctl stop phototag-admin.service && ${INSTALL_DIR}/scripts/run-admin-wsgi.sh" >&2
  exit 1
fi

echo ""
echo "OK: phototag-admin.service aktiv."
echo "  status: systemctl status phototag-admin.service"
echo "  logs:   journalctl -u phototag-admin.service -f"
echo "  Browser (LAN): http://<CT-IP>:8765/ — Login User/Passwort aus phototag.env"
echo "  2FA spaeter: Reverse-Proxy davor (DEPLOY.md), nicht in phototag selbst."
