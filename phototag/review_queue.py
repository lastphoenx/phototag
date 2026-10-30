"""Review queue: approve model suggestions for push-nc."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from phototag.config import caption_max, max_tags
from phototag.nextcloud_sync import nc_dav_relative_path


def nc_display_path(row: sqlite3.Row) -> str:
    """Path under PHOTOTAG_NC_DAV_PREFIX (e.g. Album/Test/IMG_0002.JPG)."""
    return nc_dav_relative_path(row["root"], row["path"])


def proposed_tags_from_row(row: sqlite3.Row, allowed: set[str]) -> tuple[list[str], str, list[str]]:
    """Parse model_raw like tag run; does not re-check age_status."""
    from phototag.cli import _apply_model_output, sanitize_tags

    raw = row["model_raw"] or ""
    if not raw.strip():
        return [], "", ["no_model_raw"]
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        return [], "", ["bad_model_raw"]
    if not isinstance(out, dict):
        return [], "", ["bad_model_raw"]
    tags, _dropped, caption, _ = _apply_model_output(out, allowed)
    tags, flags = sanitize_tags(tags, allowed)
    return tags, caption, flags


def normalize_chosen_tags(tags: list[str], allowed: set[str]) -> list[str]:
    cleaned = sorted({t.strip().lower() for t in tags if t.strip().lower() in allowed})
    return cleaned[: max_tags()]


def list_review_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id, root, path, folder, kind, age_status, caption, error, model_raw "
        "FROM images WHERE status='review' ORDER BY path"
    ).fetchall()


def row_to_api_dict(row: sqlite3.Row, allowed: set[str]) -> dict[str, Any]:
    tags, caption, flags = proposed_tags_from_row(row, allowed)
    cap = row["caption"] or caption
    return {
        "id": row["id"],
        "path": row["path"],
        "nc_path": nc_display_path(row),
        "root": row["root"],
        "kind": row["kind"],
        "status": "review",
        "in_review": True,
        "age_status": row["age_status"],
        "caption": cap,
        "error": row["error"],
        "proposed_tags": tags,
        "sanitize_flags": flags,
        "allowed_tags": sorted(allowed),
        "can_approve": bool(cap or tags),
    }


def approve_image(
    con: sqlite3.Connection,
    image_id: int,
    *,
    push_nc: bool = False,
    dry_run_push: bool = False,
    tags: list[str] | None = None,
    caption: str | None = None,
) -> dict[str, Any]:
    from phototag.cli import index_image, load_categories, prepare_stored_tags, sanitize_review_note

    row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise LookupError(f"Kein Eintrag id={image_id}")
    if row["status"] != "review":
        raise ValueError(f"id={image_id} status={row['status']}, erwartet review")

    allowed = {t for t, _ in load_categories()}
    proposed, cap_from_raw, flags = proposed_tags_from_row(row, allowed)

    if tags is not None:
        final_tags, sanitize_flags = prepare_stored_tags(tags, allowed)
        if sanitize_flags:
            raise ValueError(
                f"id={image_id} sanitize: {','.join(sanitize_flags)} — Tags anpassen, dann erneut freigeben"
            )
        final_caption = (caption or row["caption"] or cap_from_raw or "")[: caption_max()]
    else:
        if flags:
            raise ValueError(f"id={image_id} sanitize: {','.join(flags)} — Tags im UI anpassen oder --tag setzen")
        final_tags = proposed
        if not final_tags:
            raise ValueError(f"id={image_id} keine Tags in model_raw")
        final_caption = (caption or row["caption"] or cap_from_raw or "")[: caption_max()]

    from phototag.nextcloud_sync import ensure_nc_pushed_table

    ensure_nc_pushed_table(con)
    con.execute("DELETE FROM nc_pushed WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM tags WHERE image_id=?", (image_id,))
    con.executemany("INSERT INTO tags VALUES(?,?)", [(image_id, t) for t in final_tags])
    con.execute(
        "UPDATE images SET status='tagged', caption=?, error=NULL WHERE id=?",
        (final_caption, image_id),
    )
    index_image(con, image_id)
    con.commit()

    pushed = 0
    if push_nc:
        from phototag.nextcloud_sync import load_nc_config, push_image_tags

        cfg = load_nc_config()
        tag_cache: dict[str, int] = {}
        dav_path = nc_display_path(row)
        pushed = push_image_tags(
            con,
            cfg,
            image_id=image_id,
            path=dav_path,
            tags=final_tags,
            tag_cache=tag_cache,
            dry_run=dry_run_push,
        )
        if not dry_run_push:
            con.commit()

    return {
        "id": image_id,
        "nc_path": nc_display_path(row),
        "tags": final_tags,
        "caption": final_caption,
        "pushed_tags": pushed,
    }


def skip_review(con: sqlite3.Connection, image_id: int) -> None:
    row = con.execute("SELECT status FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise LookupError(f"Kein Eintrag id={image_id}")
    if row["status"] != "review":
        raise ValueError(f"id={image_id} status={row['status']}, erwartet review")
    con.execute(
        "UPDATE images SET status='new', error='review-skipped' WHERE id=?",
        (image_id,),
    )
    con.execute("DELETE FROM tags WHERE image_id=?", (image_id,))
    con.commit()


def backfill_review_captions(con: sqlite3.Connection) -> int:
    """Set caption from model_raw where review rows still have NULL caption."""
    n = 0
    for row in list_review_rows(con):
        if row["caption"]:
            continue
        _, cap, _ = proposed_tags_from_row(row, set())
        if not cap:
            continue
        con.execute("UPDATE images SET caption=? WHERE id=?", (cap[: caption_max()], row["id"]))
        n += 1
    if n:
        con.commit()
    return n
