"""Admin UI: folder tree and filtered image lists from SQLite + scan mount."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from phototag.cli import SKIP_DIRS
from phototag.config import scan_root
from phototag.nextcloud_sync import nc_dav_relative_path
from phototag.review_queue import proposed_tags_from_row

BROWSE_FILTERS = frozenset({"all", "review", "tagged", "untagged"})


def normalize_folder(folder: str) -> str:
    return folder.strip().strip("/")


def resolve_images_root(con: sqlite3.Connection) -> str:
    """Primary DB scan root (legacy API field)."""
    want = str(Path(scan_root()).resolve())
    if con.execute("SELECT 1 FROM images WHERE root=? LIMIT 1", (want,)).fetchone():
        return want
    row = con.execute("SELECT root FROM images LIMIT 1").fetchone()
    if row:
        return str(row["root"])
    return want


def physical_path_for_logical(logical_parent: str) -> Path:
    """Map OF-relative browse path to PHOTOTAG_SCAN_ROOT on disk."""
    base = Path(scan_root()).resolve()
    parent = normalize_folder(logical_parent)
    if not parent:
        return base
    target = (base / parent).resolve()
    base_s = base.as_posix()
    target_s = target.as_posix()
    if target_s != base_s and not target_s.startswith(base_s + "/"):
        raise ValueError("invalid browse path")
    return target


def mount_accessible() -> bool:
    try:
        p = Path(scan_root()).resolve()
        return p.is_dir()
    except OSError:
        return False


def list_child_folders_fs(logical_parent: str) -> list[str]:
    """Subfolders from rclone mount / PHOTOTAG_SCAN_ROOT (full OF tree)."""
    parent = normalize_folder(logical_parent)
    try:
        physical = physical_path_for_logical(parent)
    except (ValueError, OSError):
        return []
    if not physical.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(physical.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name in SKIP_DIRS or entry.name.startswith("."):
            continue
        out.append(f"{parent}/{entry.name}" if parent else entry.name)
    return out


def list_child_folders_from_db(con: sqlite3.Connection, logical_parent: str) -> list[str]:
    """Subfolders implied by indexed files (all DB roots, NC paths)."""
    parent = normalize_folder(logical_parent)
    rows = con.execute("SELECT root, path FROM images").fetchall()
    children: set[str] = set()
    for r in rows:
        pdir = nc_parent_dir(r["root"], r["path"])
        if not pdir:
            continue
        if parent:
            if pdir == parent:
                continue
            prefix = parent + "/"
            if not pdir.startswith(prefix):
                continue
            rest = pdir[len(prefix) :]
            seg = rest.split("/")[0]
            if seg:
                children.add(f"{parent}/{seg}")
        else:
            seg = pdir.split("/")[0]
            if seg:
                children.add(seg)
    return sorted(children)


def list_child_folders(con: sqlite3.Connection, _root: str, parent: str) -> list[str]:
    """Union: mount directories + DB-derived paths."""
    del _root  # all roots; mount is authoritative for empty folders
    fs = list_child_folders_fs(parent)
    db = list_child_folders_from_db(con, parent)
    return sorted(set(fs) | set(db))


def nc_parent_dir(images_root: str, rel_path: str) -> str:
    nc = nc_dav_relative_path(images_root, rel_path)
    parent = Path(nc).parent.as_posix()
    return "" if parent == "." else parent


def is_direct_child_of_folder(images_root: str, rel_path: str, browse_folder: str) -> bool:
    return nc_parent_dir(images_root, rel_path) == normalize_folder(browse_folder)


def is_under_browse_folder(images_root: str, rel_path: str, browse_folder: str) -> bool:
    """True if file's NC parent is browse_folder or a descendant path."""
    parent = nc_parent_dir(images_root, rel_path)
    bf = normalize_folder(browse_folder)
    if not bf:
        return True
    return parent == bf or parent.startswith(bf + "/")


def collect_mount_media_nc_paths(folders: list[str], recursive: bool) -> list[str]:
    """NC-relative media paths under folder(s) from PHOTOTAG_SCAN_ROOT."""
    from phototag.admin_mount import MEDIA_EXTS, list_media_files_on_mount

    base = Path(scan_root()).resolve()
    out: list[str] = []
    for folder in folders:
        f = normalize_folder(folder)
        if recursive:
            try:
                physical = physical_path_for_logical(f)
            except (ValueError, OSError):
                continue
            if not physical.is_dir():
                continue
            for p in sorted(physical.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in MEDIA_EXTS:
                    continue
                try:
                    rel = p.relative_to(base).as_posix()
                except ValueError:
                    continue
                out.append(rel)
        else:
            out.extend(list_media_files_on_mount(f))
    return sorted(set(out))


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
    folder: str,
    filter_key: str,
    *,
    limit: int = 80,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """Files directly in logical browse folder — all scan roots in DB."""
    folder = normalize_folder(folder)
    filter_key = filter_key if filter_key in BROWSE_FILTERS else "all"
    extra, extra_params = _filter_clause(filter_key)
    rows = con.execute(
        "SELECT id, root, path, folder, kind, status, age_status, caption, error, model_raw "
        f"FROM images WHERE ({extra}) ORDER BY path",
        extra_params,
    ).fetchall()
    matched = [r for r in rows if is_direct_child_of_folder(r["root"], r["path"], folder)]
    total = len(matched)
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    return matched[off : off + lim], total


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
    from phototag.review_queue import nc_display_path

    tags = tags_for_image(con, row, allowed)
    cap = row["caption"] or ""
    if row["status"] == "review" and not cap:
        _, cap_from_raw, _ = proposed_tags_from_row(row, allowed)
        cap = cap_from_raw
    flags: list[str] = []
    if row["status"] == "review":
        _, _, flags = proposed_tags_from_row(row, allowed)
    nc_dir = nc_parent_dir(row["root"], row["path"])
    return {
        "id": row["id"],
        "path": row["path"],
        "nc_path": nc_display_path(row),
        "folder": nc_dir,
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
