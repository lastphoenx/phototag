"""Face-Blur UI options: presets, validation, env overlay for blur jobs (Option A)."""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from phototag.config import (
    FACEBLUR_FILLS,
    FACEBLUR_PARTS,
    faceblur_fill,
    faceblur_margin,
    faceblur_mode,
    faceblur_region,
    faceblur_regions,
    faceblur_settings_summary,
    faceblur_settings_warnings,
    faceblur_strength,
    parse_region_names,
)

PRESET_ENV = "env"
PRESET_CUSTOM = "custom"

BLUR_PRESETS: dict[str, dict[str, Any] | None] = {
    PRESET_ENV: None,
    "eyebar": {"region": "eyebar", "fill": "none", "margin": 0.15, "strength": 0.9},
    "mitte": {"region": "eyebar,nose,mouth", "fill": "bridge", "margin": 0.12, "strength": 0.85},
    "eyes": {"region": "eyes", "fill": "none", "margin": 0.2, "strength": 1.0},
    "tight": {"region": "tight", "fill": "none", "margin": 0.15, "strength": 1.0},
}

PRESET_LABELS: dict[str, str] = {
    PRESET_ENV: "Env verwenden",
    "eyebar": "Eyebar",
    "mitte": "Mitte Gesicht",
    "eyes": "Nur Augen",
    "tight": "Teilen (eng)",
    PRESET_CUSTOM: "Custom",
}


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def effective_env_snapshot() -> dict[str, Any]:
    return {
        "region": faceblur_region(),
        "regions": faceblur_regions(),
        "fill": faceblur_fill(),
        "margin": faceblur_margin(),
        "strength": faceblur_strength(),
        "mode": faceblur_mode(),
        "summary": re.sub(r"\s*score=\S+", "", faceblur_settings_summary()),
        "warnings": faceblur_settings_warnings(),
    }


def blur_defaults_payload() -> dict[str, Any]:
    env = effective_env_snapshot()
    presets = []
    for pid, label in PRESET_LABELS.items():
        if pid == PRESET_CUSTOM:
            continue
        row: dict[str, Any] = {"id": pid, "label": label}
        if pid == PRESET_ENV:
            row["uses_env"] = True
        else:
            spec = BLUR_PRESETS[pid] or {}
            row.update(spec)
        presets.append(row)
    presets.append({"id": PRESET_CUSTOM, "label": PRESET_LABELS[PRESET_CUSTOM], "custom": True})
    return {
        "parts": sorted(FACEBLUR_PARTS),
        "fills": list(FACEBLUR_FILLS),
        "modes": ("blur", "pixel"),
        "margin": {"min": 0.0, "max": 1.5, "default": faceblur_margin()},
        "strength": {"min": 0.2, "max": 4.0, "default": faceblur_strength()},
        "presets": presets,
        "env": env,
    }


def _resolved_tuning(options: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """Return tuning dict (region, fill, margin, strength, mode) and source env|ui."""
    if not options:
        return {}, "env"
    preset = (options.get("preset") or PRESET_ENV).strip().lower()
    if preset in ("", PRESET_ENV, "server"):
        return {}, "env"
    if preset == PRESET_CUSTOM:
        region_raw = (options.get("region") or "").strip()
        parts, unknown = parse_region_names(region_raw)
        if unknown and not parts:
            raise ValueError(f"Unbekannte Teile: {', '.join(unknown)}")
        fill = (options.get("fill") or "none").split("#", 1)[0].strip().lower()
        if fill not in FACEBLUR_FILLS:
            fill = "none"
        margin = _clamp(float(options.get("margin", faceblur_margin())), 0.0, 1.5)
        strength = _clamp(float(options.get("strength", faceblur_strength())), 0.2, 4.0)
        mode = (options.get("mode") or faceblur_mode()).strip().lower()
        if mode not in ("blur", "pixel"):
            mode = "blur"
        region = "+".join(parts) if parts else faceblur_region()
        return {
            "region": region,
            "fill": fill,
            "margin": margin,
            "strength": strength,
            "mode": mode,
        }, "ui"
    if preset not in BLUR_PRESETS or BLUR_PRESETS[preset] is None:
        raise ValueError(f"Unbekanntes Preset: {preset}")
    spec = BLUR_PRESETS[preset]
    mode = (options.get("mode") or faceblur_mode()).strip().lower()
    if mode not in ("blur", "pixel"):
        mode = faceblur_mode()
    return {**spec, "mode": mode}, "ui"


def normalize_blur_options(raw: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """Parse API/ job JSON. Returns (stored options or None for env-only, source)."""
    if not raw:
        return None, "env"
    tuning, source = _resolved_tuning(raw)
    if source == "env":
        return None, "env"
    preset = (raw.get("preset") or PRESET_CUSTOM).strip().lower()
    out: dict[str, Any] = {"preset": preset, "source": "ui", **tuning}
    return out, "ui"


def options_from_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    opts, _ = normalize_blur_options(data if isinstance(data, dict) else None)
    return opts


def apply_blur_options_overlay(options: dict[str, Any] | None) -> str:
    """Apply UI tuning to os.environ for this process. Returns source env|ui."""
    opts, source = normalize_blur_options(options)
    if source == "env" or not opts:
        return "env"
    os.environ["PHOTOTAG_FACEBLUR_REGION"] = opts.get("region", faceblur_region()).replace("+", ",")
    os.environ["PHOTOTAG_FACEBLUR_FILL"] = str(opts.get("fill", "none"))
    os.environ["PHOTOTAG_FACEBLUR_MARGIN"] = str(opts.get("margin", faceblur_margin()))
    os.environ["PHOTOTAG_FACEBLUR_STRENGTH"] = str(opts.get("strength", faceblur_strength()))
    os.environ["PHOTOTAG_FACEBLUR_MODE"] = str(opts.get("mode", faceblur_mode()))
    return "ui"


_OVERLAY_KEYS = (
    "PHOTOTAG_FACEBLUR_REGION",
    "PHOTOTAG_FACEBLUR_FILL",
    "PHOTOTAG_FACEBLUR_MARGIN",
    "PHOTOTAG_FACEBLUR_STRENGTH",
    "PHOTOTAG_FACEBLUR_MODE",
)
_overlay_lock = threading.Lock()


@contextmanager
def temporary_blur_overlay(options: dict[str, Any] | None) -> Iterator[str]:
    """UI tuning for one call in the admin process; env is restored afterward."""
    with _overlay_lock:
        saved = {k: os.environ.get(k) for k in _OVERLAY_KEYS}
        try:
            yield apply_blur_options_overlay(options)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def blur_info_prefix(source: str) -> str:
    return f"source={source}"


def append_source_to_notes(notes: list[str], source: str) -> None:
    summary = faceblur_settings_summary()
    if notes and notes[0] == summary:
        notes[0] = f"{blur_info_prefix(source)}; {summary}"
    else:
        notes.insert(0, blur_info_prefix(source))


def validate_blur_options_body(raw: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        opts, source = normalize_blur_options(raw)
    except (ValueError, TypeError) as exc:
        return None, [str(exc)]
    if source == "ui" and opts:
        parts, unknown = parse_region_names(str(opts.get("region", "")).replace("+", ","))
        if unknown:
            errors.append(f"Unbekannte Teile ignoriert: {', '.join(unknown)}")
        if not parts:
            errors.append("Mindestens ein gültiges Teil (region) nötig")
    return opts, errors


def options_for_storage(raw: dict[str, Any] | None) -> str | None:
    opts, _ = normalize_blur_options(raw)
    return json.dumps(opts, ensure_ascii=False) if opts else None
