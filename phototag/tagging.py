"""Vision-LMM tagging: single image rows, filters, batch with progress."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from phototag.admin_browse import normalize_folder
from phototag.nextcloud_sync import nc_dav_relative_path
from phototag.tagging_custom import (
    strip_invalid_detail_under_censor,
    vision_prompt_anonymization_block,
)

__all__ = [
    "strip_invalid_detail_under_censor",
    "vision_prompt_anonymization_block",
    "nc_path_of_row",
    "row_in_scope",
    "select_tag_candidates",
    "tag_one_row",
    "run_tag_batch",
]



def _too_many_tags_limit() -> int:
    from phototag.config import max_tags

    return max(25, 2 * max_tags())


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
        sanitize_review_note,
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
        raw_tags = out.get("tags")
        if isinstance(raw_tags, list) and len(raw_tags) > _too_many_tags_limit():
            # Modell zaehlt die Kategorienliste auf statt zu taggen -> nicht still abschneiden
            sanitize_flags.append("too_many_tags")
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
    review_note = sanitize_review_note(sanitize_flags)
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
