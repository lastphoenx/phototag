#!/usr/bin/env bash
# Nach update-from-github.sh: Version, phototag.env vs .env.example, categories-Check.
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
ENV_FILE="${PHOTOTAG_ENV:-${INSTALL_DIR}/phototag.env}"
EXAMPLE="${INSTALL_DIR}/.env.example"
REQ_TAGS="${INSTALL_DIR}/scripts/required-category-tags.txt"

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
echo "=== categories.txt (privat, wird beim Update nicht ueberschrieben) ==="
CAT_FILE="${PHOTOTAG_CATEGORIES:-${INSTALL_DIR}/categories.txt}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  CAT_FILE="${PHOTOTAG_CATEGORIES:-$CAT_FILE}"
fi
if [[ ! -f "$CAT_FILE" ]]; then
  echo "FEHLT: $CAT_FILE — anlegen (cp categories.example.txt categories.txt)" >&2
elif [[ ! -f "$REQ_TAGS" ]]; then
  echo "Hinweis: $REQ_TAGS fehlt — Category-Check uebersprungen."
else
  lack=()
  while IFS= read -r tag || [[ -n "${tag:-}" ]]; do
    [[ -z "${tag:-}" || "$tag" =~ ^# ]] && continue
    if ! grep -qE "^[[:space:]]*${tag}[[:space:]]*:" "$CAT_FILE"; then
      lack+=("$tag")
    fi
  done < "$REQ_TAGS"
  if [[ ${#lack[@]} -gt 0 ]]; then
    echo "Fehlende Tag-Zeilen in $CAT_FILE:"
    printf '  %s: …\n' "${lack[@]}"
    echo ""
    echo "Vorlagen (Beispiel-Hinweis — in deine Taxonomie einfuegen):"
    while IFS= read -r tag || [[ -n "${tag:-}" ]]; do
      [[ -z "${tag:-}" || "$tag" =~ ^# ]] && continue
      for x in "${lack[@]}"; do
        if [[ "$x" == "$tag" ]]; then
          grep -E "^[[:space:]]*${tag}:" "${INSTALL_DIR}/scripts/category-tag-snippets.txt" 2>/dev/null \
            || echo "  ${tag}: (Hinweis in privatem categories-Master nachziehen)"
        fi
      done
    done < "$REQ_TAGS"
    echo ""
    echo "Danach optional: phototag tag --retag --limit … fuer betroffene Bilder."
  else
    echo "OK: required-category-tags in $CAT_FILE"
  fi
fi

echo ""
echo "=== fertig ==="
