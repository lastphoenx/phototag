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


def thumb_side() -> int:
    return int(os.getenv("PHOTOTAG_THUMB_SIDE", "320"))


def thumb_quality() -> int:
    return int(os.getenv("PHOTOTAG_THUMB_QUALITY", "70"))


def nc_url() -> str:
    return os.getenv("PHOTOTAG_NC_URL", "").strip()


def nc_user() -> str:
    return os.getenv("PHOTOTAG_NC_USER", "").strip()


def nc_dav_prefix() -> str:
    """Path under .../remote.php/dav/files/USER/ matching phototag scan root (e.g. Photos/OF)."""
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
