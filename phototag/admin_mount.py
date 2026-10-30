"""Mount-first admin browse: filesystem listing + DB overlay on demand."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from phototag.admin_browse import (
    BROWSE_FILTERS,
    list_child_folders_fs,
    mount_accessible,
    normalize_folder,
    physical_path_for_logical,
)
from phototag.cli import IMAGE_EXTS, VIDEO_EXTS, encode_image, index_image, sha1_of
from phototag.config import caption_max, scan_root
from phototag.nextcloud_sync import nc_dav_relative_path
from phototag.review_queue import normalize_chosen_tags, proposed_tags_from_row

MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Browse overlay: never load thumb BLOBs when listing (was multi‑MB per request).
_IMAGES_BROWSE_COLS = (
    "id, root, path, folder, kind, status, age_status, caption, error, model_raw, "
    "derived_from, blur_faces, sha1, size, mtime"
)


def _path_filter_sql(logical_folder: str, recursive: bool) -> tuple[str, list[Any]]:
    """SQLite path prefix for NC-relative ``images.path`` (browse folder scope)."""
    bf = normalize_folder(logical_folder)
    if not bf:
        if recursive:
            return "", []
        return " AND instr(path, '/') = 0", []
    esc = bf.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    prefix_like = esc + "/%"
    if recursive:
        return " AND path LIKE ? ESCAPE '\\'", [prefix_like]
    return (
        " AND path LIKE ? ESCAPE '\\' AND instr(substr(path, length(?) + 2), '/') = 0",
        [prefix_like, bf],
    )


def nc_rel_from_dir_file(logical_dir: str, filename: str) -> str:
    d = normalize_folder(logical_dir)
    name = filename.strip()
    return f"{d}/{name}" if d else name


def physical_file_path(nc_rel: str) -> Path:
    nc_rel = nc_rel.replace("\\", "/").lstrip("/")
    return physical_path_for_logical(str(Path(nc_rel).parent.as_posix() if "/" in nc_rel else "")) / Path(
        nc_rel
    ).name


def build_nc_rel_index(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """One pass over images — O(n) lookup by NC path for browse overlays."""
    idx: dict[str, sqlite3.Row] = {}
    for row in con.execute(f"SELECT {_IMAGES_BROWSE_COLS} FROM images"):
        key = nc_dav_relative_path(row["root"], row["path"])
        idx[key] = row
    return idx


def find_image_by_nc_rel(
    con: sqlite3.Connection,
    nc_rel: str,
    *,
    nc_index: dict[str, sqlite3.Row] | None = None,
) -> sqlite3.Row | None:
    nc_rel = nc_rel.replace("\\", "/").lstrip("/")
    if nc_index is None:
        nc_index = build_nc_rel_index(con)
    return nc_index.get(nc_rel)


def list_media_files_on_mount(logical_folder: str) -> list[str]:
    """NC-relative paths of media files directly in this folder (from mount)."""
    try:
        physical = physical_path_for_logical(logical_folder)
    except (ValueError, OSError):
        return []
    if not physical.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(physical.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in MEDIA_EXTS:
            continue
        out.append(nc_rel_from_dir_file(logical_folder, entry.name))
    return out


def list_child_folders_mount_only(con: sqlite3.Connection, _root: str, parent: str) -> list[str]:
    del con, _root
    return list_child_folders_fs(parent)


def _row_matches_filter(row: sqlite3.Row | None, filter_key: str) -> bool:
    if filter_key == "all":
        return True
    if row is None:
        return filter_key == "untagged"
    st = row["status"]
    if filter_key == "untagged":
        return st in ("new", "error")
    if filter_key == "review":
        return st == "review"
    if filter_key == "tagged":
        return st == "tagged"
    if filter_key == "blurred":
        return st in ("blurred_review", "blurred_ok")
    return True


def _tags_for_row(con: sqlite3.Connection, row: sqlite3.Row | None, allowed: set[str]) -> list[str]:
    if not row:
        return []
    if row["status"] in ("tagged", "review"):
        db_tags = [
            r["tag"]
            for r in con.execute(
                "SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (row["id"],)
            ).fetchall()
        ]
        if db_tags:
            return db_tags
    if row["status"] == "review":
        tags, _, _ = proposed_tags_from_row(row, allowed)
        return tags
    return []


def _mount_entry_from_row(
    con: sqlite3.Connection,
    nc_rel: str,
    row: sqlite3.Row | None,
    allowed: set[str],
    blur_map: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    try:
        physical = physical_file_path(nc_rel)
        kind = "video" if physical.suffix.lower() in VIDEO_EXTS else "image"
    except (ValueError, OSError):
        kind = row["kind"] if row else "image"
    tags = _tags_for_row(con, row, allowed)
    cap = (row["caption"] or "") if row else ""
    flags: list[str] = []
    if row and row["status"] == "review":
        if not cap:
            _, cap_from_raw, _ = proposed_tags_from_row(row, allowed)
            cap = cap_from_raw
        err = row["error"] or ""
        if err.startswith("sanitize:"):
            flags = [f for f in err.split(":", 1)[1].split(",") if f]
        if not flags:
            _, _, flags = proposed_tags_from_row(row, allowed)
    status = row["status"] if row else "unindexed"
    return {
        "nc_path": nc_rel,
        "id": row["id"] if row else None,
        "indexed": row is not None,
        "path": row["path"] if row else nc_rel,
        "kind": kind,
        "status": status,
        "age_status": row["age_status"] if row else None,
        "caption": cap,
        "tags": tags,
        "proposed_tags": tags,
        "sanitize_flags": flags,
        "in_review": bool(row and row["status"] == "review"),
        "source": "mount",
        "is_blur_copy": bool(row and row["derived_from"] is not None),
        "blur_faces": row["blur_faces"] if row else None,
        "thumb_v": (row["sha1"] or "")[:12] if row else "",
        "blur_info": (row["error"] or "") if row and row["derived_from"] is not None else "",
        "blur_copies": (blur_map or {}).get(row["id"], []) if row else [],
    }


def mount_entry_to_api(
    con: sqlite3.Connection,
    nc_rel: str,
    allowed: set[str],
    filter_key: str,
    *,
    nc_index: dict[str, sqlite3.Row] | None = None,
) -> dict[str, Any] | None:
    row = find_image_by_nc_rel(con, nc_rel, nc_index=nc_index)
    if not _row_matches_filter(row, filter_key):
        return None
    return _mount_entry_from_row(con, nc_rel, row, allowed)


def _db_nc_paths_in_folder(
    con: sqlite3.Connection,
    logical_folder: str,
    filter_key: str,
    *,
    recursive: bool = False,
    limit: int = 80,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """Indexed rows in this folder matching status filter — SQL scope + pagination."""
    from phototag.admin_browse import _filter_clause

    logical_folder = normalize_folder(logical_folder)
    extra, params = _filter_clause(filter_key)
    path_sql, path_params = _path_filter_sql(logical_folder, recursive)
    where = f"({extra}){path_sql}"
    all_params = params + path_params
    total = int(con.execute(f"SELECT COUNT(*) FROM images WHERE {where}", all_params).fetchone()[0])
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    rows = con.execute(
        f"SELECT {_IMAGES_BROWSE_COLS} FROM images WHERE {where} ORDER BY path LIMIT ? OFFSET ?",
        [*all_params, lim, off],
    ).fetchall()
    return list(rows), total


def list_mount_browse_items(
    con: sqlite3.Connection,
    logical_folder: str,
    filter_key: str,
    allowed: set[str],
    *,
    limit: int = 80,
    offset: int = 0,
    recursive: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    filter_key = filter_key if filter_key in BROWSE_FILTERS else "all"
    logical_folder = normalize_folder(logical_folder)
    if filter_key == "blurred":
        recursive = True  # blur copies live in the same tree as originals — always include subfolders
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    nc_index = build_nc_rel_index(con)
    items: list[dict[str, Any]] = []
    from phototag.blur_jobs import blur_copies_by_source

    blur_map = blur_copies_by_source(con)

    if filter_key in ("tagged", "review", "blurred"):
        rows, total = _db_nc_paths_in_folder(
            con,
            logical_folder,
            filter_key,
            recursive=recursive,
            limit=lim,
            offset=off,
        )
        for row in rows:
            nc_rel = nc_dav_relative_path(row["root"], row["path"])
            items.append(_mount_entry_from_row(con, nc_rel, row, allowed, blur_map))
        return items, total
    else:
        if recursive:
            from phototag.admin_browse import collect_mount_media_nc_paths

            nc_list = collect_mount_media_nc_paths([logical_folder], True)
        else:
            nc_list = list_media_files_on_mount(logical_folder)
        filtered: list[str] = []
        for nc_rel in nc_list:
            if _row_matches_filter(nc_index.get(nc_rel), filter_key):
                filtered.append(nc_rel)
        total = len(filtered)
        for nc_rel in filtered[off : off + lim]:
            row = nc_index.get(nc_rel)
            items.append(_mount_entry_from_row(con, nc_rel, row, allowed, blur_map))
        return items, total


def ensure_indexed_from_mount(con: sqlite3.Connection, nc_rel: str) -> sqlite3.Row:
    row = find_image_by_nc_rel(con, nc_rel)
    if row:
        return row
    physical = physical_file_path(nc_rel)
    if not physical.is_file():
        raise FileNotFoundError(f"Datei fehlt auf Mount: {nc_rel}")
    root = str(Path(scan_root()).resolve())
    rel = nc_rel.replace("\\", "/").lstrip("/")
    folder = Path(rel).parent.as_posix()
    folder = "" if folder == "." else folder
    ext = physical.suffix.lower()
    kind = "video" if ext in VIDEO_EXTS else "image"
    st = physical.stat()
    digest = sha1_of(physical)
    thumb: bytes | None = None
    if kind == "image":
        try:
            thumb = encode_image(physical)["thumb"]
        except OSError:
            thumb = None
    con.execute(
        "INSERT INTO images(root,path,folder,kind,size,mtime,sha1,thumb,status) "
        "VALUES(?,?,?,?,?,?,?,?,'new')",
        (root, rel, folder, kind, st.st_size, st.st_mtime, digest, thumb),
    )
    con.commit()
    row = find_image_by_nc_rel(con, nc_rel)
    if not row:
        raise RuntimeError(f"Index fehlgeschlagen: {nc_rel}")
    return row


def save_mount_tags(
    con: sqlite3.Connection,
    nc_rel: str,
    *,
    tags: list[str],
    caption: str,
    push_nc: bool = False,
    allowed: set[str],
) -> dict[str, Any]:
    from phototag.cli import index_image, prepare_stored_tags, sanitize_review_note

    row = ensure_indexed_from_mount(con, nc_rel)
    image_id = row["id"]
    final_tags, sanitize_flags = prepare_stored_tags(tags, allowed)
    final_caption = (caption or "")[: caption_max()]
    status = "review" if sanitize_flags else "tagged"
    review_note = sanitize_review_note(sanitize_flags)
    from phototag.nextcloud_sync import ensure_nc_pushed_table

    ensure_nc_pushed_table(con)
    con.execute("DELETE FROM nc_pushed WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM tags WHERE image_id=?", (image_id,))
    con.executemany("INSERT INTO tags VALUES(?,?)", [(image_id, t) for t in final_tags])
    con.execute(
        "UPDATE images SET status=?, caption=?, error=? WHERE id=?",
        (status, final_caption, review_note, image_id),
    )
    index_image(con, image_id)
    con.commit()
    pushed = 0
    if push_nc and status == "tagged":
        from phototag.nextcloud_sync import load_nc_config, push_image_tags

        cfg = load_nc_config()
        tag_cache: dict[str, int] = {}
        pushed = push_image_tags(
            con,
            cfg,
            image_id=image_id,
            path=nc_dav_relative_path(row["root"], row["path"]),
            tags=final_tags,
            tag_cache=tag_cache,
            dry_run=False,
        )
        con.commit()
    return {
        "ok": True,
        "id": image_id,
        "nc_path": nc_rel,
        "status": status,
        "tags": final_tags,
        "caption": final_caption,
        "sanitize_flags": sanitize_flags,
        "pushed_tags": pushed,
    }


def forward_mount_media_to_telegram(
    nc_rel: str,
    *,
    caption: str = "",
) -> dict[str, Any]:
    from phototag.telegram_compress import cleanup_phototag_temp, cleanup_temps, prepare_media_for_telegram
    from phototag.telegram_forward import send_media_to_telegram

    if not mount_accessible():
        raise RuntimeError("PHOTOTAG_SCAN_ROOT nicht lesbar")
    physical = physical_file_path(nc_rel)
    if not physical.is_file():
        raise FileNotFoundError(f"Datei nicht auf Mount: {nc_rel}")
    cap = (caption or "").strip() or nc_rel
    send_path, temps, meta = prepare_media_for_telegram(physical)
    upload_name = Path(nc_rel).name
    if meta.get("compressed"):
        upload_name = f"{Path(nc_rel).stem}.mp4"
    try:
        sent = send_media_to_telegram(send_path, caption=cap, upload_name=upload_name)
    finally:
        cleanup_temps(temps)
        cleanup_phototag_temp(send_path)
    return {"ok": True, "nc_path": nc_rel, **sent, **meta}


def thumb_bytes_for_nc(con: sqlite3.Connection, nc_rel: str) -> bytes | None:
    row = find_image_by_nc_rel(con, nc_rel)
    if row and row["thumb"]:
        return row["thumb"]
    physical = physical_file_path(nc_rel)
    if not physical.is_file():
        return None
    ext = physical.suffix.lower()
    if ext in IMAGE_EXTS:
        try:
            return encode_image(physical)["thumb"]
        except OSError:
            return None
    if ext in VIDEO_EXTS:
        try:
            from phototag.cli import encode_video

            return encode_video(physical)["thumb"]
        except (OSError, ValueError):
            return None
    return None
