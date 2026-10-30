"""Optional tagging customization from JSON (server-only path, not in public repo).

Schema (version 1): JSON with prompt/strip rules, kept on the server only.
Env: PHOTOTAG_TAGGING_CUSTOM=/path/to/tagging-custom.json
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

_GENERIC_INTRO: dict[str, list[str]] = {
    "de": [
        "- Absichtliche Zensur (Blur, Mosaik, Balken, Sticker): passende Tags aus der categories-Liste "
        "(z. B. *_censored, privacy_blur); nicht Bokeh oder Bewegungsunschärfe.",
        "- Keine Sichtbar-/Detail-Tags für absichtlich verdeckte Bereiche.",
    ],
    "en": [
        "- Deliberate censorship (blur, mosaic, bars, stickers): use matching tags from the category list "
        "(e.g. *_censored, privacy_blur); not bokeh or motion blur.",
        "- Do not tag visible/detail tags for deliberately covered areas.",
    ],
}


def tagging_custom_path() -> str:
    return os.getenv("PHOTOTAG_TAGGING_CUSTOM", "").strip()


@lru_cache(maxsize=1)
def load_tagging_custom() -> dict[str, Any]:
    path = tagging_custom_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _censor_triggers(custom: dict[str, Any]) -> list[str]:
    raw = custom.get("censor_trigger_tags")
    if isinstance(raw, list):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    return []


def _prompt_section(custom: dict[str, Any], lang: str, key: str) -> str:
    block = custom.get("prompt", {})
    if not isinstance(block, dict):
        return ""
    lang_block = block.get(lang) or block.get("de") or {}
    if not isinstance(lang_block, dict):
        return ""
    val = lang_block.get(key)
    if isinstance(val, list):
        return "\n".join(str(x) for x in val if str(x).strip())
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def _lines_for_allowed_tags(tag_lines: dict[str, str], allowed: set[str], header: str, footer: str) -> str:
    if not isinstance(tag_lines, dict):
        return ""
    parts = [header] if header else []
    for tag, line in tag_lines.items():
        tid = str(tag).strip().lower()
        if tid in allowed and str(line).strip():
            parts.append(f"  {tid}: {str(line).strip()}")
    if len(parts) <= (1 if header else 0):
        return ""
    if footer:
        parts.append(footer)
    return "\n".join(parts)


def vision_prompt_anonymization_block(lang: str, allowed: set[str]) -> str:
    """Zusatz-Prompt: aus PHOTOTAG_TAGGING_CUSTOM oder generischer Fallback."""
    custom = load_tagging_custom()
    triggers = _censor_triggers(custom)
    face_extra = custom.get("face_visibility_exclusive")
    activators: set[str] = set(triggers)
    if isinstance(face_extra, list):
        activators.update(str(x).strip().lower() for x in face_extra if str(x).strip())
    activators.update({"face_hidden", str(custom.get("face_censored_tag") or "face_censored")})

    if triggers:
        present = sorted(t for t in triggers if t in allowed)
    else:
        present = sorted(
            t
            for t in allowed
            if t in activators or t.endswith("_censored") or t in ("privacy_blur", "pixelation", "bar_censor")
        )
    if not present and not (allowed & activators):
        return ""

    lang_key = lang if lang in ("de", "en") else "de"
    if custom:
        intro = _prompt_section(custom, lang_key, "intro")
        if not intro:
            intro = "\n".join(_GENERIC_INTRO[lang_key])
        examples = ", ".join(present[:8])
        if len(present) > 8:
            examples += ", …"
        intro = intro.replace("{examples}", examples)

        chunks = [intro]
        lang_cfg = (custom.get("prompt") or {}).get(lang_key) or {}
        extra_keys = sorted(k for k in lang_cfg if isinstance(k, str) and k.endswith("_hint")) if isinstance(lang_cfg, dict) else []
        for key in ("face_censor_rule", "face_visibility", *extra_keys):
            block = _prompt_section(custom, lang_key, key)
            if block:
                if not block.startswith("-"):
                    block = "- " + block
                chunks.append(block)

        lang_block = (custom.get("prompt") or {}).get(lang_key) or {}
        gran_header = ""
        gran_footer = ""
        gran_by_tag: dict[str, str] = {}
        if isinstance(lang_block, dict):
            gran_header = str(lang_block.get("granularity_header") or "").strip()
            gran_footer = str(lang_block.get("granularity_footer") or "").strip()
            raw_map = lang_block.get("granularity_by_tag")
            if isinstance(raw_map, dict):
                gran_by_tag = {str(k).lower(): str(v) for k, v in raw_map.items()}

        gran = _lines_for_allowed_tags(gran_by_tag, allowed, gran_header, gran_footer)
        if gran:
            chunks.append(gran)

        detail = _prompt_section(custom, lang_key, "no_detail_inference")
        if detail:
            if not detail.startswith("-"):
                detail = "- " + detail
            chunks.append(detail)

        return "\n".join(chunks)

    return "\n".join(_GENERIC_INTRO[lang_key])


def strip_invalid_detail_under_censor(t: set[str]) -> list[str]:
    """Regeln aus PHOTOTAG_TAGGING_CUSTOM; sonst keine body-spezifische Bereinigung."""
    custom = load_tagging_custom()
    rules = custom.get("strip_under_censor")
    if not isinstance(rules, list):
        return []

    flags: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when_tag = str(rule.get("when", "")).strip().lower()
        when_any = rule.get("when_any_in")
        drop = rule.get("drop_tags") or rule.get("drop") or []
        if not isinstance(drop, list):
            drop = [drop]
        drop_set = {str(x).strip().lower() for x in drop if str(x).strip()}
        flag = str(rule.get("flag") or "under_censor").strip()

        fire = False
        when_all = rule.get("when_all_in")
        if isinstance(when_all, list):
            all_set = {str(x).strip().lower() for x in when_all if str(x).strip()}
            if all_set and all_set <= t:
                fire = True
        if when_tag and when_tag in t:
            if isinstance(when_any, list):
                any_set = {str(x).strip().lower() for x in when_any}
                fire = bool(any_set & t)
            elif not isinstance(when_all, list):
                fire = True
        elif isinstance(when_any, list) and not when_tag and not isinstance(when_all, list):
            any_set = {str(x).strip().lower() for x in when_any}
            if any_set & t:
                fire = True
        if not fire or not drop_set:
            continue
        if t & drop_set:
            t -= drop_set
            flags.append(flag)

    face_exclusive = custom.get("face_visibility_exclusive")
    if isinstance(face_exclusive, list):
        order = [str(x).strip().lower() for x in face_exclusive if str(x).strip()]
        present = [x for x in order if x in t]
        if len(present) > 1:
            keep = present[-1]
            for x in present[:-1]:
                t.discard(x)
            flags.append("face_visibility_exclusive")

    censored = str(custom.get("face_censored_tag") or "face_censored").strip().lower()
    if censored in t and isinstance(face_exclusive, list):
        for x in face_exclusive:
            if x != censored and x in t:
                t.discard(x)
        if "face_visible" in t:
            t.discard("face_visible")
            flags.append("face_censored_vs_visible")

    return flags


def clear_tagging_custom_cache() -> None:
    load_tagging_custom.cache_clear()
