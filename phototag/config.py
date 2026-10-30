"""Environment-backed settings — read at call time, not import time."""
from __future__ import annotations

import os
import re
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


FACEBLUR_PARTS = frozenset(("face", "tight", "eyes", "eyebar", "nose", "mouth", "ears", "chin", "forehead"))
FACEBLUR_ALIASES = {"eyes_mouth": ("eyes", "mouth"), "eyes_mouth_ears": ("eyes", "mouth", "ears")}
FACEBLUR_REGIONS = FACEBLUR_PARTS | frozenset(FACEBLUR_ALIASES)  # accepted names
BOX_PARTS = frozenset(("face", "tight"))  # plain boxes (no landmarks needed)


def parse_region_names(raw: str) -> tuple[list[str], list[str]]:
    """'eyebar, nose+mouth' -> (['eyebar','nose','mouth'], unknown names). Aliases are expanded."""
    parts: list[str] = []
    unknown: list[str] = []
    raw = (raw or "").split("#", 1)[0]  # KEY=a,b   # comment (systemd keeps the comment text)
    for tok in re.split(r"[,+\s]+", raw.strip().lower()):
        if not tok:
            continue
        if tok in FACEBLUR_ALIASES:
            names = list(FACEBLUR_ALIASES[tok])
        elif tok in FACEBLUR_PARTS:
            names = [tok]
        else:
            unknown.append(tok)
            continue
        for n in names:
            if n not in parts:
                parts.append(n)
    return parts, unknown


FACEBLUR_FILLS = ("none", "bridge", "hull")


def faceblur_fill() -> str:
    """none = separate bars; bridge = close the gaps between neighbouring parts; hull = one convex shape."""
    raw = os.getenv("PHOTOTAG_FACEBLUR_FILL", "none").split("#", 1)[0].strip().lower()
    return raw if raw in FACEBLUR_FILLS else "none"


def faceblur_fill_gap() -> float:
    """bridge: largest gap that gets closed, in eye distances."""
    return _env_float("PHOTOTAG_FACEBLUR_FILL_GAP", 1.5, 0.2, 4.0)


def faceblur_part_wh(part: str) -> tuple[float, float]:
    """Optional width/height factors per part: PHOTOTAG_FACEBLUR_SCALE_<PART>_W / _H."""
    return (
        _env_float(f"PHOTOTAG_FACEBLUR_SCALE_{part.upper()}_W", 1.0, 0.3, 3.0),
        _env_float(f"PHOTOTAG_FACEBLUR_SCALE_{part.upper()}_H", 1.0, 0.3, 3.0),
    )


def faceblur_track_min_score() -> float:
    """Video: a track needs at least this MEAN score (with >= 2 hits) or it is dropped as a false positive."""
    return _env_float("PHOTOTAG_FACEBLUR_TRACK_MIN_SCORE", 0.55, 0.2, 0.95)


def faceblur_track_sure_score() -> float:
    """Video: a track with a single detection is kept only when that detection is this sure."""
    return _env_float("PHOTOTAG_FACEBLUR_TRACK_SURE_SCORE", 0.90, 0.3, 0.99)


def faceblur_corroborate() -> bool:
    """Video: a real face is also found (at a lower score) when the picture is turned; a body pattern or a
    hand is not. A track found in ONE orientation only is dropped when it is not very sure (score < 0.9)."""
    return _env_bool("PHOTOTAG_FACEBLUR_CORROBORATE", True)


def faceblur_track_trust_score() -> float:
    """Video: above this score a track needs no second orientation to be believed."""
    return _env_float("PHOTOTAG_FACEBLUR_TRACK_TRUST_SCORE", 0.85, 0.5, 0.99)


def faceblur_max_faces() -> int:
    """Video: keep only the N best tracks (0 = no limit). 1 is a quick cure for single-person scenes."""
    return int(_env_float("PHOTOTAG_FACEBLUR_MAX_FACES", 0, 0, 20))


def faceblur_gap_sec() -> float:
    """Video: a face that vanishes for less than this is assumed to be still there (interpolated)."""
    return _env_float("PHOTOTAG_FACEBLUR_GAP_SEC", 1.0, 0.0, 10.0)


def faceblur_hold_sec() -> float:
    """Video: keep blurring this long before the first and after the last detection of a track."""
    return _env_float("PHOTOTAG_FACEBLUR_HOLD_SEC", 0.3, 0.0, 5.0)


def faceblur_video_max_side() -> int:
    """Video output is scaled down to this long side (0 = keep the original size)."""
    return int(_env_float("PHOTOTAG_FACEBLUR_VIDEO_MAX_SIDE", 1920, 0, 8000))


def faceblur_video_preset() -> str:
    raw = os.getenv("PHOTOTAG_FACEBLUR_VIDEO_PRESET", "veryfast").split("#", 1)[0].strip().lower()
    ok = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow")
    return raw if raw in ok else "veryfast"


def faceblur_stage_local() -> bool:
    """Copy a video from the mount to local disk once, process it there, copy the result back once."""
    return _env_bool("PHOTOTAG_FACEBLUR_STAGE", True)


def faceblur_debug_dir() -> str:
    return os.getenv("PHOTOTAG_FACEBLUR_DEBUG_DIR", "").strip()


def faceblur_rotate() -> bool:
    """Detect in 4 orientations (0/90/180/270). YuNet sideways hits need an upright pass for landmarks."""
    return _env_bool("PHOTOTAG_FACEBLUR_ROTATE", True)


def faceblur_scale(part: str) -> float:
    """Per-part size factor PHOTOTAG_FACEBLUR_SCALE_<PART> (nose, mouth, ears, chin, forehead)."""
    return _env_float(f"PHOTOTAG_FACEBLUR_SCALE_{part.upper()}", 1.0, 0.3, 3.0)


def faceblur_bar_up() -> float:
    """eyebar: height above the eye line in eye distances (eyebrows)."""
    return _env_float("PHOTOTAG_FACEBLUR_BAR_UP", 0.45, 0.1, 2.0)


def faceblur_bar_down() -> float:
    """eyebar: height below the eye line in eye distances (eye bags)."""
    return _env_float("PHOTOTAG_FACEBLUR_BAR_DOWN", 0.35, 0.1, 2.0)


def faceblur_bar_side() -> float:
    """eyebar: length factor; 1.0 = from face edge to face edge (about ear to ear)."""
    return _env_float("PHOTOTAG_FACEBLUR_BAR_SIDE", 1.0, 0.5, 2.0)


def faceblur_regions() -> list[str]:
    """Parts to blur, e.g. PHOTOTAG_FACEBLUR_REGION=eyebar,nose,mouth (unknown names are ignored)."""
    parts, _ = parse_region_names(os.getenv("PHOTOTAG_FACEBLUR_REGION", "face"))
    return parts or ["face"]


def faceblur_region() -> str:
    """Effective region as text (parts joined by '+')."""
    return "+".join(faceblur_regions())


def faceblur_strength() -> float:
    """Blur intensity (0.2–4). Higher = stärkerer Unkenntlichmachungs-Effekt."""
    return _env_float("PHOTOTAG_FACEBLUR_STRENGTH", 1.0, 0.2, 4.0)


def faceblur_mode() -> str:
    mode = os.getenv("PHOTOTAG_FACEBLUR_MODE", "blur").strip().lower()
    return mode if mode in ("blur", "pixel") else "blur"


def faceblur_max_detect_side() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_MAX_SIDE", 2560, 640, 8000))


def faceblur_video_stride() -> int:
    return int(_env_float("PHOTOTAG_FACEBLUR_VIDEO_STRIDE", 3, 1, 15))


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
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()  # KEY=value   # comment
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


def faceblur_settings_warnings() -> list[str]:
    """Env values that are set but ignored (typo, comment text, out of range) — otherwise silent."""
    warnings: list[str] = []
    checks: tuple[tuple[str, str, float, float], ...] = (
        ("PHOTOTAG_FACEBLUR_SCORE", "score", 0.2, 0.95),
        ("PHOTOTAG_FACEBLUR_MARGIN", "margin", 0.0, 1.5),
        ("PHOTOTAG_FACEBLUR_STRENGTH", "strength", 0.2, 4.0),
        ("PHOTOTAG_FACEBLUR_BAR_UP", "bar_up", 0.1, 2.0),
        ("PHOTOTAG_FACEBLUR_BAR_DOWN", "bar_down", 0.1, 2.0),
        ("PHOTOTAG_FACEBLUR_BAR_SIDE", "bar_side", 0.5, 2.0),
        ("PHOTOTAG_FACEBLUR_TRACK_MIN_SCORE", "track_min_score", 0.2, 0.95),
        ("PHOTOTAG_FACEBLUR_TRACK_SURE_SCORE", "track_sure_score", 0.3, 0.99),
        ("PHOTOTAG_FACEBLUR_GAP_SEC", "gap_sec", 0.0, 10.0),
        ("PHOTOTAG_FACEBLUR_HOLD_SEC", "hold_sec", 0.0, 5.0),
        ("PHOTOTAG_FACEBLUR_MAX_FACES", "max_faces", 0.0, 20.0),
        ("PHOTOTAG_FACEBLUR_VIDEO_MAX_SIDE", "video_max_side", 0.0, 8000.0),
    )
    for key, label, lo, hi in checks:
        raw = os.getenv(key, "").strip()
        if not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            warnings.append(f"{key}={raw!r} ist keine Zahl -> Standardwert ({label})")
            continue
        if not lo <= val <= hi:
            warnings.append(f"{key}={raw} ausserhalb {lo:g}..{hi:g} -> begrenzt")
    raw_region = os.getenv("PHOTOTAG_FACEBLUR_REGION", "").strip()
    if raw_region:
        parts, unknown = parse_region_names(raw_region)
        allowed = ", ".join(sorted(FACEBLUR_REGIONS))
        if unknown and not parts:
            warnings.append(f"PHOTOTAG_FACEBLUR_REGION={raw_region!r} unbekannt -> face (erlaubt: {allowed}; mehrere mit Komma)")
        elif unknown:
            warnings.append(f"PHOTOTAG_FACEBLUR_REGION: {', '.join(unknown)} unbekannt und ignoriert (erlaubt: {allowed})")
    raw_fill = os.getenv("PHOTOTAG_FACEBLUR_FILL", "").split("#", 1)[0].strip().lower()
    if raw_fill and raw_fill not in FACEBLUR_FILLS:
        warnings.append(f"PHOTOTAG_FACEBLUR_FILL={raw_fill!r} unbekannt -> none (erlaubt: {', '.join(FACEBLUR_FILLS)})")
    raw_gap = os.getenv("PHOTOTAG_FACEBLUR_FILL_GAP", "").split("#", 1)[0].strip()
    if raw_gap:
        try:
            float(raw_gap)
        except ValueError:
            warnings.append(f"PHOTOTAG_FACEBLUR_FILL_GAP={raw_gap!r} ist keine Zahl -> 1.5")
    for part in ("nose", "mouth", "ears", "chin", "forehead"):
        for suffix in ("", "_W", "_H"):
            key = f"PHOTOTAG_FACEBLUR_SCALE_{part.upper()}{suffix}"
            raw = os.getenv(key, "").split("#", 1)[0].strip()
            if not raw:
                continue
            try:
                val = float(raw)
            except ValueError:
                warnings.append(f"{key}={raw!r} ist keine Zahl -> 1.0")
                continue
            if not 0.3 <= val <= 3.0:
                warnings.append(f"{key}={raw} ausserhalb 0.3..3 -> begrenzt")
    mode = os.getenv("PHOTOTAG_FACEBLUR_MODE", "").strip().lower()
    if mode and mode not in ("blur", "pixel"):
        warnings.append(f"PHOTOTAG_FACEBLUR_MODE={mode!r} unbekannt -> blur")
    return warnings


def faceblur_settings_summary() -> str:
    """Effective Face-Blur tuning (for logs / job result)."""
    base = (
        f"region={faceblur_region()} margin={faceblur_margin():g} strength={faceblur_strength():g} "
        f"score={faceblur_score():g} mode={faceblur_mode()}"
    )
    scales = [f"{p}={faceblur_scale(p):g}" for p in ("nose", "mouth", "ears", "chin", "forehead") if p in faceblur_regions() and faceblur_scale(p) != 1.0]
    if scales:
        base += " scale[" + " ".join(scales) + "]"
    if not faceblur_rotate():
        base += " rotate=off"
    if faceblur_max_faces():
        base += f" max_faces={faceblur_max_faces()}"
    if faceblur_fill() != "none":
        base += f" fill={faceblur_fill()}" + (f"(gap={faceblur_fill_gap():g})" if faceblur_fill() == "bridge" else "")
    if "eyebar" in faceblur_regions():
        base += f" bar_up={faceblur_bar_up():g} bar_down={faceblur_bar_down():g} bar_side={faceblur_bar_side():g}"
    warn = faceblur_settings_warnings()
    return base + (" | WARNUNG: " + "; ".join(warn) if warn else "")
