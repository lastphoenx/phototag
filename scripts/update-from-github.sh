#!/usr/bin/env bash
# Update phototag from GitHub ZIP — no git deploy key.
# Keeps local categories.txt, .env, *.db, .venv on the install dir.
#
# Private repo: export GITHUB_TOKEN=<read-only PAT> (nicht committen).
# Standard: main. Optional Pin nur fuer dieses Skript: PHOTOTAG_UPDATE_REF=<sha|tag>
# (PHOTOTAG_REF/PHOTOTAG_TAG in phototag.env werden absichtlich ignoriert — sonst pinnt source phototag.env Updates.)
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
GITHUB_REPO="${PHOTOTAG_GITHUB_REPO:-lastphoenx/phototag}"

REF="${PHOTOTAG_UPDATE_REF:-main}"

_strip_legacy_update_pins() {
  local f="$INSTALL_DIR/phototag.env"
  [[ -f "$f" ]] || return 0
  if grep -qE '^[[:space:]]*PHOTOTAG_(REF|TAG)=' "$f" 2>/dev/null; then
    sed -i '/^[[:space:]]*PHOTOTAG_REF=/d; /^[[:space:]]*PHOTOTAG_TAG=/d' "$f"
    echo "phototag.env: PHOTOTAG_REF/PHOTOTAG_TAG entfernt (veralteter Update-Pin; Pin nur: PHOTOTAG_UPDATE_REF=tag ./scripts/update-from-github.sh)." >&2
  fi
}
_strip_legacy_update_pins

if [[ -n "${PHOTOTAG_REF:-}" || -n "${PHOTOTAG_TAG:-}" ]]; then
  echo "Hinweis: PHOTOTAG_REF/PHOTOTAG_TAG noch in dieser Shell — Updates nutzen trotzdem main. Einmal: unset PHOTOTAG_REF PHOTOTAG_TAG (oder neues ssh)." >&2
fi

if [[ "$REF" == main || "$REF" == master ]]; then
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/refs/heads/${REF}.zip"
elif [[ "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/${REF}.zip"
else
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/refs/tags/${REF}.zip"
fi

command -v wget >/dev/null || command -v curl >/dev/null || {
  echo "wget oder curl benötigt" >&2
  exit 1
}
command -v unzip >/dev/null || {
  echo "unzip benötigt (apt install unzip)" >&2
  exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ZIP="$TMP/phototag.zip"

download() {
  if command -v wget >/dev/null; then
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
      wget -qO "$ZIP" --header="Authorization: Bearer ${GITHUB_TOKEN}" "$ARCHIVE"
    else
      wget -qO "$ZIP" "$ARCHIVE"
    fi
  else
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
      curl -fsSL -H "Authorization: Bearer ${GITHUB_TOKEN}" -o "$ZIP" "$ARCHIVE"
    else
      curl -fsSL -o "$ZIP" "$ARCHIVE"
    fi
  fi
}

if ! download; then
  echo "phototag: Download fehlgeschlagen ($ARCHIVE)." >&2
  echo "Häufig: REF/Tag existiert nicht — Branch main oder git tag auf GitHub prüfen." >&2
  echo "Oder: privates Repo → GITHUB_TOKEN; oder git clone + git pull statt ZIP." >&2
  exit 1
fi

if ! unzip -t "$ZIP" >/dev/null 2>&1; then
  echo "phototag: Kein gültiges ZIP (404/HTML?) — Privatheit oder REF prüfen." >&2
  exit 1
fi

unzip -q "$ZIP" -d "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'phototag-*' | head -1)"
[[ -n "$SRC" && -d "$SRC" ]] || {
  echo "Archiv-Layout unerwartet" >&2
  exit 1
}

mkdir -p "$INSTALL_DIR"
rsync -a \
  --exclude 'categories.txt' \
  --exclude 'categories.private.txt' \
  --exclude '.env' \
  --exclude '*.db' \
  --exclude '.venv' \
  --exclude 'state' \
  "$SRC/" "$INSTALL_DIR/"

if [[ ! -f "$INSTALL_DIR/categories.txt" ]]; then
  cp "$INSTALL_DIR/categories.example.txt" "$INSTALL_DIR/categories.txt"
  echo "Hinweis: categories.txt aus Example angelegt — bitte anpassen (bleibt nur lokal)."
fi

cd "$INSTALL_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -e .
echo "phototag aktualisiert in $INSTALL_DIR (REF=$REF). categories.txt unverändert gelassen."
if [[ "$REF" != main && "$REF" != master ]] && [[ ! "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "Hinweis: Tag-Pin REF=$REF — fuer main: PHOTOTAG_UPDATE_REF leer lassen oder scripts/bootstrap-update-from-main.sh" >&2
fi
