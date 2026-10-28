#!/usr/bin/env bash
# Einmalig oder wenn update-from-github.sh veraltet ist (z. B. haengt an PHOTOTAG_REF=v0.3.3).
# Holt das aktuelle update-from-github.sh von GitHub main und fuehrt es aus.
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
REPO="${PHOTOTAG_GITHUB_REPO:-lastphoenx/phototag}"
URL="https://raw.githubusercontent.com/${REPO}/main/scripts/update-from-github.sh"

command -v curl >/dev/null || command -v wget >/dev/null || {
  echo "curl oder wget benoetigt" >&2
  exit 1
}

mkdir -p "${INSTALL_DIR}/scripts"
if command -v curl >/dev/null; then
  curl -fsSL "$URL" -o "${INSTALL_DIR}/scripts/update-from-github.sh"
else
  wget -qO "${INSTALL_DIR}/scripts/update-from-github.sh" "$URL"
fi
chmod +x "${INSTALL_DIR}/scripts/update-from-github.sh"

unset PHOTOTAG_REF PHOTOTAG_TAG
export PHOTOTAG_INSTALL_DIR="$INSTALL_DIR"
exec "${INSTALL_DIR}/scripts/update-from-github.sh"
