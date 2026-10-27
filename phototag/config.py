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
