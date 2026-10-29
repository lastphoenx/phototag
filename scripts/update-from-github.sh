#!/usr/bin/env bash
# Update phototag from GitHub ZIP — no git deploy key.
# Keeps local categories.txt, phototag.env, *.db, .venv, state on the install dir.
#
# Standard: GitHub branch main. Optional Pin: PHOTOTAG_UPDATE_REF=<sha|tag>
# Private repo: export GITHUB_TOKEN=<read-only PAT>
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
GITHUB_REPO="${PHOTOTAG_GITHUB_REPO:-lastphoenx/phototag}"

# Alte Shell-Exports duerfen Updates nicht mehr steuern (nur PHOTOTAG_UPDATE_REF).
unset PHOTOTAG_REF PHOTOTAG_TAG
REF="${PHOTOTAG_UPDATE_REF:-main}"

_strip_legacy_update_pins() {
  local f="$INSTALL_DIR/phototag.env"
  [[ -f "$f" ]] || return 0
  if grep -qE '^[[:space:]]*PHOTOTAG_(REF|TAG)=' "$f" 2>/dev/null; then
    sed -i '/^[[:space:]]*PHOTOTAG_REF=/d; /^[[:space:]]*PHOTOTAG_TAG=/d' "$f"
    echo "phototag.env: veraltete PHOTOTAG_REF/PHOTOTAG_TAG Zeilen entfernt." >&2
  fi
}
_strip_legacy_update_pins

if [[ "$REF" == main || "$REF" == master ]]; then
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/refs/heads/${REF}.zip"
elif [[ "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/${REF}.zip"
else
  ARCHIVE="https://github.com/${GITHUB_REPO}/archive/refs/tags/${REF}.zip"
fi

echo "phototag update: REF=$REF → ${ARCHIVE}"

command -v wget >/dev/null || command -v curl >/dev/null || {
  echo "wget oder curl benötigt" >&2
  exit 1
}
command -v unzip >/dev/null || {
  echo "unzip benötigt (apt install unzip)" >&2
  exit 1
}
command -v rsync >/dev/null || {
  echo "rsync benötigt (apt install rsync)" >&2
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
  echo "phototag: Download fehlgeschlagen." >&2
  echo "  URL: $ARCHIVE" >&2
  echo "  Privat? GITHUB_TOKEN setzen. REF/Tag auf GitHub prüfen." >&2
  exit 1
fi

if ! unzip -t "$ZIP" >/dev/null 2>&1; then
  echo "phototag: Kein gültiges ZIP (404/HTML?) — URL oder Token prüfen." >&2
  exit 1
fi

unzip -q "$ZIP" -d "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'phototag-*' | head -1)"
[[ -n "$SRC" && -d "$SRC" ]] || {
  echo "Archiv-Layout unerwartet" >&2
  exit 1
}

SRC_VER="$(grep -E '^version = ' "$SRC/pyproject.toml" | head -1 | sed 's/version = "//;s/"//' | tr -d '\r')"
if [[ -z "$SRC_VER" ]]; then
  echo "phototag: pyproject.toml im ZIP ohne version — REF prüfen." >&2
  exit 1
fi
echo "  ZIP enthält pyproject version $SRC_VER"

mkdir -p "$INSTALL_DIR"
rsync -a \
  --exclude 'categories.txt' \
  --exclude 'categories.private.txt' \
  --exclude '.env' \
  --exclude 'phototag.env' \
  --exclude '*.db' \
  --exclude '.venv' \
  --exclude 'state' \
  --exclude 'secrets' \
  "$SRC/" "$INSTALL_DIR/"

if [[ ! -f "$INSTALL_DIR/categories.txt" ]]; then
  cp "$INSTALL_DIR/categories.example.txt" "$INSTALL_DIR/categories.txt"
  echo "Hinweis: categories.txt aus Example angelegt — bitte anpassen (bleibt nur lokal)."
fi

if ! grep -q 'PHOTOTAG_UPDATE_REF:-main' "$INSTALL_DIR/scripts/update-from-github.sh" 2>/dev/null; then
  echo "phototag: FEHLER — nach rsync ist update-from-github.sh veraltet; bootstrap erneut ausführen." >&2
  exit 1
fi

cd "$INSTALL_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -e ".[admin]"

INSTALLED="$(.venv/bin/python -c 'import importlib.metadata as m; print(m.version("phototag"))')"
if [[ "$INSTALLED" != "$SRC_VER" ]]; then
  echo "phototag: WARNUNG pip-Version ($INSTALLED) != ZIP ($SRC_VER) — prüfe .venv." >&2
fi

echo "OK phototag $INSTALLED in $INSTALL_DIR (REF=$REF). categories.txt unverändert."
if [[ "$REF" != main && "$REF" != master ]] && [[ ! "$REF" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "Hinweis: Tag-Pin — fuer main: unset PHOTOTAG_UPDATE_REF" >&2
fi

if [[ -x "$INSTALL_DIR/scripts/post-update.sh" ]]; then
  echo ""
  "$INSTALL_DIR/scripts/post-update.sh" || true
fi
