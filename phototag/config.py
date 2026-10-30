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
    """Mount / local tree root (same as phototag scan when indexing the full tree)."""
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


def telegram_bot_token() -> str:
    return os.getenv("PHOTOTAG_TELEGRAM_BOT_TOKEN", "").strip()


def telegram_chat_id() -> str:
    """Ziel-Chat: Saved Messages (deine numerische user id) oder Gruppe/Kanal-ID."""
    return os.getenv("PHOTOTAG_TELEGRAM_CHAT_ID", "").strip()


def telegram_upload_timeout_sec() -> int:
    """Upload vom Mount (rclone) kann dauern — Default wie gunicorn (600s)."""
    raw = os.getenv("PHOTOTAG_TELEGRAM_UPLOAD_TIMEOUT", "600").strip()
    try:
        return max(30, min(3600, int(raw)))
    except ValueError:
        return 600


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _env_int_mb(key: str, default_mb: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default_mb * 1024 * 1024
    try:
        if raw.isdigit():
            return max(1, int(raw)) * 1024 * 1024
        return max(1, int(float(raw)))
    except ValueError:
        return default_mb * 1024 * 1024


def _env_int_sec(key: str, default: int) -> int:
    raw = os.getenv(key, str(default)).strip()
    try:
        return max(30, min(7200, int(raw)))
    except ValueError:
        return default


def telegram_max_send_bytes() -> int:
    return _env_int_mb("PHOTOTAG_TELEGRAM_MAX_MB", 50)


def telegram_compress_enabled() -> bool:
    return _env_bool("PHOTOTAG_TELEGRAM_COMPRESS", False)


def telegram_compress_target_bytes() -> int:
    return _env_int_mb("PHOTOTAG_TELEGRAM_COMPRESS_TARGET_MB", 48)


def telegram_compress_audio() -> bool:
    return _env_bool("PHOTOTAG_TELEGRAM_COMPRESS_AUDIO", True)


def telegram_temp_dir() -> Path:
    raw = os.getenv("PHOTOTAG_TELEGRAM_TEMP_DIR", "/tmp").strip() or "/tmp"
    return Path(raw)


def telegram_staging_max_bytes() -> int:
    return _env_int_mb("PHOTOTAG_TELEGRAM_STAGING_MAX_MB", 600)


def telegram_staging_timeout_sec() -> int:
    return _env_int_sec("PHOTOTAG_TELEGRAM_STAGING_TIMEOUT", 3600)


def telegram_compress_timeout_sec() -> int:
    return _env_int_sec("PHOTOTAG_TELEGRAM_COMPRESS_TIMEOUT", 1800)


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


_ephemeral_session_secret: str | None = None


def admin_session_secret() -> str:
    """Cookie-Signatur. Env PHOTOTAG_ADMIN_SESSION_SECRET oder Zufall pro Prozess (kein fester Default)."""
    import secrets

    global _ephemeral_session_secret
    explicit = os.getenv("PHOTOTAG_ADMIN_SESSION_SECRET", "").strip()
    if explicit:
        return explicit
    if _ephemeral_session_secret is None:
        _ephemeral_session_secret = secrets.token_hex(32)
    return _ephemeral_session_secret


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


# --- Face-Blur (phototag.faceblur) ---------------------------------------------

def faceblur_model_path() -> str:
    """YuNet ONNX file; empty = Face-Blur aus (UI-Button versteckt)."""
    return os.getenv("PHOTOTAG_FACEBLUR_MODEL", "").strip()


def faceblur_enabled() -> bool:
    return bool(faceblur_model_path())


def faceblur_suffix() -> str:
    raw = os.getenv("PHOTOTAG_FACEBLUR_SUFFIX", "ai-blurred").strip().lower()
    return "".join(c for c in raw if c.isalnum() or c in "-_") or "ai-blurred"


def _env_float(key: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.getenv(key, str(default)))))
    except ValueError:
        return default


def faceblur_score() -> float:
    """Detector threshold. Low on purpose: a missed face is worse than an extra blur."""
    return _env_float("PHOTOTAG_FACEBLUR_SCORE", 0.5, 0.2, 0.95)


def faceblur_margin() -> float:
    return _env_float("PHOTOTAG_FACEBLUR_MARGIN", 0.4, 0.0, 1.5)


FACEBLUR_REGIONS = frozenset(
    ("face", "tight", "eyes", "mouth", "eyes_mouth", "eyes_mouth_ears")
)


def faceblur_region() -> str:
    """Where to blur: full face box, tight box, or YuNet landmark regions (eyes, mouth, ears approx.)."""
    raw = os.getenv("PHOTOTAG_FACEBLUR_REGION", "face").strip().lower()
    return raw if raw in FACEBLUR_REGIONS else "face"


def faceblur_strength() -> float:
    """Blur intensity (0.2–4). Higher = stärkerer Unkenntlichmachungs-Effekt."""
    return _env_float("PHOTOTAG_FACEBLUR_STRENGTH", 1.0, 0.2, 4.0)


def faceblur_mode() -> str:
    mode = os.getenv("PHOTOTAG_FACEBLUR_MODE", "blur").strip().lower()
    return mode if mode in ("blur", "pixel") else "blur"


def faceblur_max_detect_side() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_MAX_SIDE", 2560, 640, 8000))


def faceblur_video_stride() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_VIDEO_STRIDE", 2, 1, 10))


def faceblur_video_crf() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_VIDEO_CRF", 20, 14, 30))


def faceblur_video_timeout_sec() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_VIDEO_TIMEOUT", 7200, 60, 86400))


def faceblur_keep_empty() -> bool:
    """1 = auch ohne erkannte Gesichter eine Kopie schreiben."""
    return _env_bool("PHOTOTAG_FACEBLUR_KEEP_EMPTY", False)


def phototag_env_path() -> Path:
    install = os.getenv("PHOTOTAG_INSTALL_DIR", "/opt/phototag")
    raw = os.getenv("PHOTOTAG_ENV", f"{install}/phototag.env").strip()
    return Path(raw)


def read_phototag_env() -> dict[str, str]:
    """Parse phototag.env (KEY=VALUE, no shell expansion)."""
    path = phototag_env_path()
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not key.startswith("PHOTOTAG_"):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def load_phototag_env(*, override: bool = False) -> bool:
    """Apply phototag.env to os.environ (Blur-Jobs / CLI ohne ``source``)."""
    items = read_phototag_env()
    if not items:
        return False
    for key, val in items.items():
        if override or key not in os.environ:
            os.environ[key] = val
    return True


def faceblur_settings_summary() -> str:
    """Effective Face-Blur tuning (for logs / job result)."""
    return (
        f"region={faceblur_region()} margin={faceblur_margin():g} strength={faceblur_strength():g} "
        f"score={faceblur_score():g} mode={faceblur_mode()}"
    )
