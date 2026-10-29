"""Admin UI: folder tree and filtered image lists from SQLite."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from phototag.config import scan_root
from phototag.review_queue import nc_display_path, proposed_tags_from_row

BROWSE_FILTERS = frozenset({"all", "review", "tagged", "untagged"})


def normalize_folder(folder: str) -> str:
    return folder.strip().strip("/")


def resolve_images_root(con: sqlite3.Connection) -> str:
    """Root path as stored in images.root (matches scan)."""
    want = str(Path(scan_root()).resolve())
    if con.execute("SELECT 1 FROM images WHERE root=? LIMIT 1", (want,)).fetchone():
        return want
    row = con.execute("SELECT root FROM images LIMIT 1").fetchone()
    if row:
        return str(row["root"])
    return want


def list_child_folders(con: sqlite3.Connection, root: str, parent: str) -> list[str]:
    parent = normalize_folder(parent)
    if parent:
        like = parent + "/%"
        rows = con.execute(
            "SELECT DISTINCT folder FROM images WHERE root=? AND (folder=? OR folder LIKE ?)",
            (root, parent, like),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT DISTINCT folder FROM images WHERE root=? AND folder != ''",
            (root,),
        ).fetchall()
    children: set[str] = set()
    for r in rows:
        f = normalize_folder(r["folder"] or "")
        if not f:
            continue
        if parent:
            if f == parent:
                continue
            prefix = parent + "/"
            if not f.startswith(prefix):
                continue
            rest = f[len(prefix) :]
        else:
            rest = f
        seg = rest.split("/")[0]
        if not seg:
            continue
        children.add(f"{parent}/{seg}" if parent else seg)
    return sorted(children)


def _filter_clause(filter_key: str) -> tuple[str, list[Any]]:
    if filter_key == "review":
        return "status='review'", []
    if filter_key == "tagged":
        return "status='tagged'", []
    if filter_key == "untagged":
        return "status IN ('new','error')", []
    return "1=1", []


def list_browse_rows(
    con: sqlite3.Connection,
    root: str,
    folder: str,
    filter_key: str,
    *,
    limit: int = 80,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    folder = normalize_folder(folder)
    filter_key = filter_key if filter_key in BROWSE_FILTERS else "all"
    extra, extra_params = _filter_clause(filter_key)
    where = f"root=? AND folder=? AND ({extra})"
    params: list[Any] = [root, folder, *extra_params]
    total = con.execute(
        f"SELECT COUNT(*) FROM images WHERE {where}",
        params,
    ).fetchone()[0]
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    rows = con.execute(
        "SELECT id, root, path, folder, kind, status, age_status, caption, error, model_raw "
        f"FROM images WHERE {where} ORDER BY path LIMIT ? OFFSET ?",
        [*params, lim, off],
    ).fetchall()
    return rows, total


def tags_for_image(con: sqlite3.Connection, row: sqlite3.Row, allowed: set[str]) -> list[str]:
    if row["status"] == "tagged":
        return [
            r["tag"]
            for r in con.execute(
                "SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (row["id"],)
            ).fetchall()
        ]
    if row["status"] == "review":
        tags, _, _ = proposed_tags_from_row(row, allowed)
        return tags
    return []


def browse_row_to_api_dict(
    con: sqlite3.Connection, row: sqlite3.Row, allowed: set[str]
) -> dict[str, Any]:
    tags = tags_for_image(con, row, allowed)
    cap = row["caption"] or ""
    if row["status"] == "review" and not cap:
        _, cap_from_raw, _ = proposed_tags_from_row(row, allowed)
        cap = cap_from_raw
    flags: list[str] = []
    if row["status"] == "review":
        _, _, flags = proposed_tags_from_row(row, allowed)
    return {
        "id": row["id"],
        "path": row["path"],
        "nc_path": nc_display_path(row),
        "folder": row["folder"] or "",
        "kind": row["kind"],
        "status": row["status"],
        "age_status": row["age_status"],
        "caption": cap,
        "error": row["error"],
        "tags": tags,
        "proposed_tags": tags if row["status"] == "review" else tags,
        "sanitize_flags": flags,
        "in_review": row["status"] == "review",
    }
