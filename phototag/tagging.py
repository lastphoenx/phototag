"""Vision-LMM tagging: single image rows, filters, batch with progress."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from phototag.admin_browse import normalize_folder
from phototag.nextcloud_sync import nc_dav_relative_path

# Tag-IDs für absichtliche Anonymisierung/Zensur (nur in lokaler categories.txt).
# Der Prompt-Block erscheint, wenn mindestens einer in der geladenen Tag-Liste vorkommt.
ANONYMIZATION_CENSOR_TAG_IDS: tuple[str, ...] = (
    "privacy_blur",
    "face_censored",
    "breasts_censored",
    "nipples_censored",
    "genitals_censored",
    "penis_censored",
    "vulva_censored",
    "buttocks_censored",
    "anus_censored",
    "testicles_censored",
    "pixelation",
    "bar_censor",
)

# Feinere Zensur-Regionen — Prompt-Hinweise nur wenn in categories.txt vorhanden.
GRANULAR_CENSOR_REGION_TAG_IDS: tuple[str, ...] = (
    "nipples_censored",
    "breasts_censored",
    "penis_censored",
    "vulva_censored",
    "genitals_censored",
    "buttocks_censored",
    "anus_censored",
    "testicles_censored",
)

# Zensur über Genital/After/Hoden — kein «rasiert/behaart» aus Blur/Balken ableiten.
GENITAL_AREA_CENSOR_TAG_IDS: frozenset[str] = frozenset(
    {
        "penis_censored",
        "vulva_censored",
        "genitals_censored",
        "anus_censored",
        "testicles_censored",
    }
)

ANUS_DETAIL_TAG_IDS: frozenset[str] = frozenset(
    {"anus_visible", "anus_closeup", "anus_spread"},
)
REAR_VULVA_DETAIL_TAG_IDS: frozenset[str] = frozenset(
    {"rear_view_genitals", "rear_pussy", "genitals_visible"},
)
TESTICLE_DETAIL_TAG_IDS: frozenset[str] = frozenset(
    {"testicles_visible", "testicles_large", "testicles_small"},
)
PENIS_DETAIL_TAG_IDS: frozenset[str] = frozenset(
    {
        "penis_visible",
        "penis_erect",
        "penis_flaccid",
        "penis_closeup",
        "penis_large",
        "penis_small",
        "penis_thick",
        "penis_thin",
        "penis_long",
        "penis_short",
    },
)
GROOMING_DETAIL_TAG_IDS: frozenset[str] = frozenset({"genitals_shaved", "genitals_hairy"})


def strip_invalid_detail_under_censor(t: set[str]) -> list[str]:
    """Detail-Tags entfernen, die unter *_censored nicht belegbar sind."""
    flags: list[str] = []
    genital_censor = t & GENITAL_AREA_CENSOR_TAG_IDS
    if genital_censor and t & GROOMING_DETAIL_TAG_IDS:
        t -= GROOMING_DETAIL_TAG_IDS
        flags.append("grooming_under_censor")
    if "anus_censored" in t and t & ANUS_DETAIL_TAG_IDS:
        t -= ANUS_DETAIL_TAG_IDS
        flags.append("anus_detail_under_censor")
    if "vulva_censored" in t and t & REAR_VULVA_DETAIL_TAG_IDS:
        t -= REAR_VULVA_DETAIL_TAG_IDS
        flags.append("vulva_detail_under_censor")
    if "testicles_censored" in t and t & TESTICLE_DETAIL_TAG_IDS:
        t -= TESTICLE_DETAIL_TAG_IDS
        flags.append("testicles_detail_under_censor")
    if "penis_censored" in t and t & PENIS_DETAIL_TAG_IDS:
        t -= PENIS_DETAIL_TAG_IDS
        flags.append("penis_detail_under_censor")
    return flags


def _vision_prompt_censor_no_detail_inference(lang: str, allowed: set[str]) -> str:
    """Blur/Balken/Emoji ≠ sichtbare Hautdetails."""
    if not (allowed & GENITAL_AREA_CENSOR_TAG_IDS or allowed & GROOMING_DETAIL_TAG_IDS):
        return ""
    if lang == "en":
        return (
            "- Under censorship (blur, bar, emoji, circle, mosaic): do not tag skin details that are "
            "hidden — smooth censor patches are not genitals_shaved/genitals_hairy; no anus_visible, "
            "rear_view_genitals, penis_visible, testicles_visible, etc. on covered areas. "
            "rear_view_naked / rear_view still OK when the pose is clearly a naked rear, with *_censored "
            "on the hidden patches only."
        )
    return (
        "- Unter Zensur (Blur, Balken, Emoji, Kreis, Mosaik): keine Detail-Tags für verdeckte Stellen — "
        "glatte Zensurfläche ist nicht genitals_shaved/genitals_hairy; kein anus_visible, rear_view_genitals, "
        "rear_pussy, penis_visible, testicles_visible usw. auf abgedeckten Bereichen. "
        "rear_view_naked / rear_view weiter setzen, wenn klar nacktes Gesäß von hinten, plus passende "
        "*_censored nur auf den verdeckten Flecken."
    )


def _vision_prompt_censor_granularity(lang: str, allowed: set[str]) -> str:
    present = [t for t in GRANULAR_CENSOR_REGION_TAG_IDS if t in allowed]
    if not present:
        return ""
    lines_de = [
        "- Zensur-Region (was verdeckt ist, nicht was darunter erkennbar wäre):",
    ]
    if "nipples_censored" in allowed:
        lines_de.append(
            "  nipples_censored: nur Brustwarze/Hof per Blur/Balken/Mosaik — Brustform daneben noch sichtbar."
        )
    if "breasts_censored" in allowed:
        lines_de.append(
            "  breasts_censored: ganze Brust bzw. große Brustfläche verdeckt (inkl. Warzen); "
            "nicht bei kleiner Warzen-Abdeckung allein."
        )
    if "penis_censored" in allowed and "vulva_censored" in allowed:
        lines_de.append(
            "  penis_censored vs vulva_censored: an Körperkontext wählen (Schaft/Glans vs Schamlippen/Vulva); "
            "genitals_censored nur wenn Geschlecht/Teil unklar."
        )
    elif "penis_censored" in allowed:
        lines_de.append("  penis_censored: Penis/Schaft/Glans-Bereich absichtlich verdeckt.")
    elif "vulva_censored" in allowed:
        lines_de.append(
            "  vulva_censored: Vulva/Schambereich verdeckt (vorn oder von hinten zwischen den Beinen; "
            "entspricht rear_view_genitals/rear_pussy wenn dort zensiert)."
        )
    elif "genitals_censored" in allowed:
        lines_de.append("  genitals_censored: Genitalbereich verdeckt, wenn penis/vulva nicht unterscheidbar.")
    if "buttocks_censored" in allowed:
        lines_de.append(
            "  buttocks_censored: Gesäßfläche/Po verdeckt (Blur/Balken); rear_view_naked trotzdem möglich, "
            "wenn Pose klar nacktes Gesäß ist und nur Teile verdeckt sind."
        )
    if "anus_censored" in allowed:
        lines_de.append("  anus_censored: After/Anus-Bereich verdeckt (nicht anus_visible/spread setzen).")
    if "testicles_censored" in allowed:
        lines_de.append("  testicles_censored: Hoden/Eier verdeckt (nicht testicles_visible).")
    lines_de.append(
        "- Keine widersprüchlichen Paare: bei *_censored nicht das passende Sichtbar-Tag "
        "(nipples, breasts, penis_visible, genitals_visible, …)."
    )
    if lang == "en":
        return (
            "- Censor region (what is hidden, not what might be visible under blur):\n"
            + "\n".join(
                line.replace("  ", "  ", 1) if line.startswith("  ") else line
                for line in [
                    *(
                        [
                            "  nipples_censored: only areola/nipple patch censored; breast shape still visible."
                        ]
                        if "nipples_censored" in allowed
                        else []
                    ),
                    *(
                        [
                            "  breasts_censored: whole breast or large breast area censored; not tiny nipple-only patches."
                        ]
                        if "breasts_censored" in allowed
                        else []
                    ),
                    *(
                        [
                            "  penis_censored vs vulva_censored: pick by anatomy/context; "
                            "genitals_censored only if unclear."
                        ]
                        if "penis_censored" in allowed and "vulva_censored" in allowed
                        else []
                    ),
                    *(
                        ["  penis_censored: penis/shaft/glans area deliberately hidden."]
                        if "penis_censored" in allowed
                        and "vulva_censored" not in allowed
                        else []
                    ),
                    *(
                        ["  vulva_censored: vulva/labia censored front or rear between legs (rear_pussy patch)."]
                        if "vulva_censored" in allowed
                        and "penis_censored" not in allowed
                        else []
                    ),
                    *(
                        ["  buttocks_censored: butt cheeks censored; rear_view_naked may still apply."]
                        if "buttocks_censored" in allowed
                        else []
                    ),
                    *(
                        ["  anus_censored: anus area censored — not anus_visible/spread."]
                        if "anus_censored" in allowed
                        else []
                    ),
                    *(
                        ["  testicles_censored: testicles censored — not testicles_visible."]
                        if "testicles_censored" in allowed
                        else []
                    ),
                    *(
                        ["  genitals_censored: crotch censored when penis vs vulva not distinguishable."]
                        if "genitals_censored" in allowed
                        and "penis_censored" not in allowed
                        and "vulva_censored" not in allowed
                        else []
                    ),
                    "- Do not combine *_censored with matching visible tags (nipples, breasts, penis_visible, …).",
                ]
            )
        )
    return "\n".join(lines_de)


def vision_prompt_anonymization_block(lang: str, allowed: set[str]) -> str:
    """Zusatz-Anweisungen für VL: Zensur-Blur vs. natürliche Unschärfe."""
    censor_present = [t for t in ANONYMIZATION_CENSOR_TAG_IDS if t in allowed]
    if not censor_present:
        return ""
    examples = ", ".join(censor_present[:6])
    if len(censor_present) > 6:
        examples += ", …"
    face_rules: list[str] = []
    if "face_visible" in allowed and "face_censored" in allowed:
        if lang == "en":
            face_rules.append(
                "face_visible only if the face is recognizable; if censored (blur/bar/pixel), "
                "use face_censored instead of face_visible."
            )
        else:
            face_rules.append(
                "face_visible nur wenn das Gesicht erkennbar ist; bei absichtlicher Gesichts-Zensur "
                "face_censored statt face_visible."
            )
    elif "face_visible" in allowed:
        if lang == "en":
            face_rules.append("Do not set face_visible when a face is deliberately censored.")
        else:
            face_rules.append("face_visible nicht setzen, wenn ein Gesicht absichtlich zensiert ist.")
    face_block = ""
    if face_rules:
        face_block = "\n- " + face_rules[0]
    gran_block = _vision_prompt_censor_granularity(lang, allowed)
    gran_suffix = f"\n{gran_block}" if gran_block else ""
    detail_block = _vision_prompt_censor_no_detail_inference(lang, allowed)
    detail_suffix = f"\n{detail_block}" if detail_block else ""

    if lang == "en":
        return (
            "- Anonymization / censorship in the image: If blur, mosaic/pixelation, bars, or stickers "
            "deliberately hide body parts or faces (not artistic depth-of-field), set the matching tags "
            f"from the list (e.g. {examples}).\n"
            "- Do not use these tags for motion blur, bokeh, soft focus, or an out-of-focus background "
            "without censorship intent."
            + face_block
            + gran_suffix
            + detail_suffix
        )
    return (
        "- Anonymisierung / Zensur im Bild: Wenn absichtliche Unschärfe, Mosaik/Pixelierung, Balken oder "
        "Sticker Körperteile oder Gesichter verbergen (nicht künstliche Tiefenschärfe), vergib die "
        f"passenden Tags aus der Liste (z. B. {examples}).\n"
        "- Diese Tags nicht bei Bewegungsunschärfe, Bokeh, weichem Zeichenstil oder unscharfem Hintergrund "
        "ohne Zensur-Absicht."
        + face_block
        + gran_suffix
        + detail_suffix
    )


def nc_path_of_row(row: sqlite3.Row) -> str:
    return nc_dav_relative_path(row["root"], row["path"])


def row_in_scope(
    row: sqlite3.Row,
    *,
    nc_paths: set[str],
    folders: list[str],
    recursive: bool,
) -> bool:
    nc = nc_path_of_row(row)
    if nc_paths:
        if nc in nc_paths:
            return True
        if not folders:
            return False
    for folder in folders:
        f = normalize_folder(folder)
        if not f:
            continue
        if recursive:
            if nc == f or nc.startswith(f + "/"):
                return True
        else:
            parent = Path(nc).parent.as_posix()
            if parent == ".":
                parent = ""
            if parent == f:
                return True
    return False


def select_tag_candidates(
    con: sqlite3.Connection,
    *,
    retag: bool = False,
    kind: str | None = None,
    nc_paths: list[str] | None = None,
    folders: list[str] | None = None,
    recursive: bool = True,
    limit: int = 0,
) -> list[sqlite3.Row]:
    nc_set = {p.replace("\\", "/").lstrip("/") for p in (nc_paths or []) if p.strip()}
    folder_list = [normalize_folder(f) for f in (folders or []) if normalize_folder(f)]
    if not nc_set and not folder_list:
        return []
    where = ["1=1"] if retag else ["status IN ('new','error')"]
    params: list[Any] = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    rows = con.execute(
        f"SELECT * FROM images WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()
    matched = [
        r
        for r in rows
        if row_in_scope(r, nc_paths=nc_set, folders=folder_list, recursive=recursive)
    ]
    if limit and limit > 0:
        matched = matched[:limit]
    return matched


def tag_one_row(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run vision model on one DB row; commit. Returns result summary."""
    import json
    import subprocess
    import urllib.error

    from phototag.cli import (
        _apply_model_output,
        _print_tag_extras,
        ask_model,
        cli_verbose,
        encode_image,
        encode_video,
        index_image,
        load_categories,
        model_name,
        sanitize_tags,
    )

    from phototag.config import caption_max

    categories = load_categories()
    allowed = {t for t, _ in categories}
    path = Path(row["root"]) / row["path"]
    is_video = row["kind"] == "video"
    t0 = time.time()
    try:
        media = encode_video(path) if is_video else encode_image(path)
        out = ask_model(media["frames"], categories, is_video)
        age_status = str(out.get("age_status", "unsure")).lower()
        tags, dropped, caption, model_raw = _apply_model_output(out, allowed)
        tags, sanitize_flags = sanitize_tags(tags, allowed)
    except (
        OSError,
        urllib.error.URLError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        con.execute("UPDATE images SET status='error', error=? WHERE id=?", (str(exc)[:300], row["id"]))
        con.commit()
        return {
            "ok": False,
            "id": row["id"],
            "nc_path": nc_path_of_row(row),
            "error": str(exc),
        }
    status = "tagged" if age_status == "yes" and not sanitize_flags else "review"
    review_note = None
    if sanitize_flags:
        review_note = "sanitize:" + ",".join(sanitize_flags)
    con.execute("DELETE FROM tags WHERE image_id=?", (row["id"],))
    if status == "tagged":
        con.executemany("INSERT INTO tags VALUES(?,?)", [(row["id"], t) for t in tags])
    con.execute(
        "UPDATE images SET status=?,age_status=?,caption=?,model_raw=?,width=?,height=?,duration=?,"
        "frames=?,thumb=?,model=?,tagged_at=?,error=? WHERE id=?",
        (
            status,
            age_status,
            caption,
            model_raw,
            media["width"],
            media["height"],
            media["duration"],
            len(media["frames"]),
            media["thumb"],
            model_name(),
            time.time(),
            review_note,
            row["id"],
        ),
    )
    index_image(con, row["id"])
    con.commit()
    v = verbose or cli_verbose()
    if v:
        _print_tag_extras(
            out,
            caption if status == "tagged" else str(out.get("caption", ""))[: caption_max()],
            dropped,
            v,
            sanitize_flags,
        )
    return {
        "ok": True,
        "id": row["id"],
        "nc_path": nc_path_of_row(row),
        "status": status,
        "tags": tags if status == "tagged" else [],
        "proposed_tags": tags,
        "caption": caption,
        "seconds": round(time.time() - t0, 1),
    }


def run_tag_batch(
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    verbose: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    ok = failed = 0
    for i, row in enumerate(rows, 1):
        if on_progress:
            on_progress(
                {
                    "phase": "tagging",
                    "index": i,
                    "total": len(rows),
                    "nc_path": nc_path_of_row(row),
                }
            )
        result = tag_one_row(con, row, verbose=verbose)
        if result.get("ok"):
            ok += 1
        else:
            failed += 1
        if on_progress:
            on_progress({"phase": "done_one", "result": result, "index": i, "total": len(rows)})
    return {"ok": ok, "failed": failed, "total": len(rows)}
