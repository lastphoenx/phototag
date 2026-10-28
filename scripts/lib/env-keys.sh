# Shared helpers for phototag.env vs .env.example (source from other scripts).
# Nur **aktive** Zeilen (ohne # am Anfang) — kommentierte Optionen in .env.example sind Dokumentation, kein Muss.
env_keys_from_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  grep -E '^[[:space:]]*PHOTOTAG_[A-Z0-9_]+=' "$f" \
    | sed -E 's/^[[:space:]]*(PHOTOTAG_[A-Z0-9_]+)=.*/\1/' \
    | sort -u
}
