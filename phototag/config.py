"""Environment-backed settings — read at call time, not import time."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_example_warned = False


def db_path() -> str:
    return os.getenv("PHOTOTAG_DB", "phototag.db")


def api_url() -> str:
    return os.getenv("PHOTOTAG_API", "http://127.0.0.1:8080/v1").rstrip("/")


def model_name() -> str:
    return os.getenv("PHOTOTAG_MODEL", "qwen3.8-27b")


def max_side() -> int:
    return int(os.getenv("PHOTOTAG_MAX_SIDE", "1024"))


def video_frames() -> int:
    return max(5, min(8, int(os.getenv("PHOTOTAG_VIDEO_FRAMES", "6"))))


def video_side() -> int:
    return int(os.getenv("PHOTOTAG_VIDEO_SIDE", "768"))


def max_tags() -> int:
    return int(os.getenv("PHOTOTAG_MAX_TAGS", "12"))


def caption_max() -> int:
    """Max caption length stored and sent to the vision model (chars)."""
    return max(80, min(500, int(os.getenv("PHOTOTAG_CAPTION_MAX", "320"))))


def reasoning_effort() -> str:
    """Ollama /v1: 'none' deaktiviert Denk-Modus (chat_template_kwargs/think oft ignoriert).

    PHOTOTAG_REASONING_EFFORT leer = Feld nicht senden (Backends ohne reasoning_effort).
    """
    return os.getenv("PHOTOTAG_REASONING_EFFORT", "none").strip()


def max_completion_tokens() -> int:
    return max(256, int(os.getenv("PHOTOTAG_MAX_TOKENS", "2048")))


def tag_sanitize_enabled() -> bool:
    return os.getenv("PHOTOTAG_TAG_SANITIZE", "1").strip().lower() not in ("0", "false", "no")


def prompt_lang() -> str:
    """Prompt-Sprache: de (Standard) oder en — nur Anweisungstext, Kategorie-Hinweise bleiben wie in categories.txt."""
    lang = os.getenv("PHOTOTAG_PROMPT_LANG", "de").strip().lower()
    return lang if lang in ("de", "en") else "de"


def cli_verbose() -> bool:
    return os.getenv("PHOTOTAG_VERBOSE", "").strip().lower() in ("1", "true", "yes")


def thumb_side() -> int:
    return int(os.getenv("PHOTOTAG_THUMB_SIDE", "320"))


def thumb_quality() -> int:
    return int(os.getenv("PHOTOTAG_THUMB_QUALITY", "70"))


def nc_url() -> str:
    return os.getenv("PHOTOTAG_NC_URL", "").strip()


def nc_user() -> str:
    return os.getenv("PHOTOTAG_NC_USER", "").strip()


def scan_root() -> str:
    """Mount / local tree root (same as phototag scan when indexing full OF)."""
    return os.getenv("PHOTOTAG_SCAN_ROOT", "").strip()


def nc_dav_prefix() -> str:
    """Path under .../remote.php/dav/files/USER/ matching phototag scan root."""
    return os.getenv("PHOTOTAG_NC_DAV_PREFIX", "").strip()


def nc_app_token() -> str:
    direct = os.getenv("PHOTOTAG_NC_APP_PASSWORD", "").strip()
    if direct:
        return direct
    path = os.getenv("PHOTOTAG_NC_TOKEN_FILE", "").strip() or os.getenv(
        "PHOTOTAG_NC_PASSWORD_FILE", ""
    ).strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return ""


def nc_tag_level() -> str:
    """Nextcloud system tag visibility: public | restricted | invisible."""
    level = os.getenv("PHOTOTAG_NC_TAG_LEVEL", "public").strip().lower()
    return level if level in ("public", "restricted", "invisible") else "public"


def nc_tls_insecure() -> bool:
    return os.getenv("PHOTOTAG_NC_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes")


def nc_tls_ca_file() -> str:
    return os.getenv("PHOTOTAG_NC_CA_FILE", "").strip()


def admin_token() -> str:
    """Optional API-Token (Header); Login primär über User/Passwort."""
    return os.getenv("PHOTOTAG_ADMIN_TOKEN", "").strip()


def admin_user() -> str:
    return os.getenv("PHOTOTAG_ADMIN_USER", "").strip()


def admin_password() -> str:
    return os.getenv("PHOTOTAG_ADMIN_PASSWORD", "").strip()


def admin_password_hash() -> str:
    """Optional statt Klartext: werkzeug hash (generate_password_hash auf dem CT)."""
    return os.getenv("PHOTOTAG_ADMIN_PASSWORD_HASH", "").strip()


def verify_admin_password(candidate: str) -> bool:
    import secrets

    from werkzeug.security import check_password_hash

    user = admin_user()
    if not user:
        return False
    h = admin_password_hash()
    if h:
        return check_password_hash(h, candidate)
    plain = admin_password()
    if not plain:
        return False
    return secrets.compare_digest(candidate, plain)


def admin_session_secret() -> str:
    explicit = os.getenv("PHOTOTAG_ADMIN_SESSION_SECRET", "").strip()
    if explicit:
        return explicit
    tok = admin_token()
    if tok:
        return tok
    return "phototag-admin-change-me"


def validate_admin_auth_config() -> None:
    user = admin_user()
    if not user:
        raise RuntimeError(
            "Admin-Login fehlt: PHOTOTAG_ADMIN_USER in phototag.env setzen "
            "(nur auf dem Server, nicht committen)."
        )
    if not admin_password_hash() and not admin_password():
        raise RuntimeError(
            "Admin-Passwort fehlt: PHOTOTAG_ADMIN_PASSWORD oder PHOTOTAG_ADMIN_PASSWORD_HASH "
            "in phototag.env (siehe DEPLOY.md — Hash erzeugen mit werkzeug)."
        )


def admin_bind() -> str:
    """Default 0.0.0.0 = LAN-Zugriff (nur mit Login); 127.0.0.1 für reine Konsole."""
    return os.getenv("PHOTOTAG_ADMIN_BIND", "0.0.0.0").strip() or "0.0.0.0"


def admin_port() -> int:
    return max(1, min(65535, int(os.getenv("PHOTOTAG_ADMIN_PORT", "8765"))))


def categories_path() -> Path:
    """PHOTOTAG_CATEGORIES, else ./categories.txt, else packaged categories.example.txt."""
    global _example_warned
    explicit = os.getenv("PHOTOTAG_CATEGORIES")
    if explicit:
        return Path(explicit)
    local = Path.cwd() / "categories.txt"
    if local.is_file():
        return local
    example = _PACKAGE_DIR / "categories.example.txt"
    if not _example_warned:
        print(
            f"phototag: Warnung: keine categories.txt im Arbeitsverzeichnis — "
            f"nutze Demo-Kategorien ({example}). "
            f"Eigene Liste: cp categories.example.txt categories.txt oder PHOTOTAG_CATEGORIES setzen.",
            file=sys.stderr,
        )
        _example_warned = True
    return example
