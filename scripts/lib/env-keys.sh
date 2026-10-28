# Shared helpers for phototag.env vs .env.example (source from other scripts).
env_keys_from_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  grep -E '^[[:space:]]*(#)?[[:space:]]*PHOTOTAG_[A-Z0-9_]+=' "$f" \
    | sed -E 's/^[[:space:]]*#?[[:space:]]*(PHOTOTAG_[A-Z0-9_]+)=.*/\1/' \
    | sort -u
}
