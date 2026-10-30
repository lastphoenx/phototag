"""Background tag jobs for admin UI (subprocess + SQLite status)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from phototag.admin_browse import collect_mount_media_nc_paths, normalize_folder
from phototag.admin_mount import (
    ensure_indexed_from_mount,
    find_image_by_nc_rel,
)
from phototag.faceblur import BLUR_STATUSES
from phototag.nextcloud_sync import nc_dav_relative_path
from phototag.tagging import run_tag_batch, select_tag_candidates, tag_one_row

TAG_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS tag_jobs(
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  ok_count INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  current_nc_path TEXT,
  last_error TEXT,
  spec TEXT NOT NULL
);
"""


def ensure_tag_jobs_table(con: sqlite3.Connection) -> None:
    con.executescript(TAG_JOBS_DDL)
    con.commit()


def _collect_nc_paths_from_folders(folders: list[str], recursive: bool) -> list[str]:
    return collect_mount_media_nc_paths(folders, recursive)


def preview_tag_job(
    con: sqlite3.Connection,
    *,
    folders: list[str] | None = None,
    nc_paths: list[str] | None = None,
    retag: bool = False,
    recursive: bool = True,
    kind: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    """Counts for confirm dialog before starting a folder job."""
    folders = folders or []
    nc_paths = list(nc_paths or [])
    spec = {
        "folders": folders,
        "nc_paths": nc_paths,
        "retag": retag,
        "recursive": recursive,
        "kind": kind,
        "limit": limit,
        "include_db_folder_rows": False,
    }
    media_files = 0
    if folders and not nc_paths:
        media_files = len(_collect_nc_paths_from_folders(folders, recursive))
    rows = resolve_rows_for_spec(con, spec)
    skipped_tagged = 0
    if folders and not nc_paths and not retag and media_files:
        for nc in _collect_nc_paths_from_folders(folders, recursive):
            row = find_image_by_nc_rel(con, nc)
            if row and row["status"] == "tagged":
                skipped_tagged += 1
    return {
        "will_tag": len(rows),
        "skipped_tagged": skipped_tagged,
        "media_files": media_files,
        "retag": retag,
        "recursive": recursive,
    }


def resolve_rows_for_spec(con: sqlite3.Connection, spec: dict[str, Any]) -> list[sqlite3.Row]:
    retag = bool(spec.get("retag"))
    kind = spec.get("kind")
    recursive = spec.get("recursive", True)
    limit = int(spec.get("limit") or 0)
    nc_paths = list(spec.get("nc_paths") or [])
    folders = list(spec.get("folders") or [])
    # Blur copies are never tagged by folder/night batches, only when picked explicitly.
    explicit_paths = bool(nc_paths)

    if folders and not nc_paths:
        nc_paths = _collect_nc_paths_from_folders(folders, recursive)

    rows_by_id: dict[int, sqlite3.Row] = {}
    for nc in nc_paths:
        nc = nc.replace("\\", "/").lstrip("/")
        row = find_image_by_nc_rel(con, nc)
        if not row:
            try:
                row = ensure_indexed_from_mount(con, nc)
            except (FileNotFoundError, RuntimeError, OSError):
                continue
        allowed_status = ("new", "error", "review", *(BLUR_STATUSES if explicit_paths else ()))
        if not retag and row["status"] not in allowed_status:
            continue
        rows_by_id[row["id"]] = row

    if folders and spec.get("include_db_folder_rows"):
        for r in select_tag_candidates(
            con,
            retag=retag,
            kind=kind,
            folders=folders,
            recursive=recursive,
            limit=0,
        ):
            rows_by_id[r["id"]] = r

    rows = list(rows_by_id.values())
    rows.sort(key=lambda r: r["id"])
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if limit > 0:
        rows = rows[:limit]
    return rows


def create_tag_job(
    con: sqlite3.Connection,
    *,
    folders: list[str] | None = None,
    nc_paths: list[str] | None = None,
    retag: bool = False,
    recursive: bool = True,
    kind: str | None = None,
    limit: int = 0,
) -> str:
    ensure_tag_jobs_table(con)
    job_id = uuid.uuid4().hex[:16]
    spec = {
        "folders": folders or [],
        "nc_paths": nc_paths or [],
        "retag": retag,
        "recursive": recursive,
        "kind": kind,
        "limit": limit,
        "include_db_folder_rows": False,
    }
    rows = resolve_rows_for_spec(con, spec)
    paths = [nc_dav_relative_path(r["root"], r["path"]) for r in rows]
    spec["paths"] = paths
    con.execute(
        "INSERT INTO tag_jobs(id,status,created_at,total,spec) VALUES(?,?,?,?,?)",
        (job_id, "pending", time.time(), len(rows), json.dumps(spec)),
    )
    con.commit()
    return job_id


def _update_job(con: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE tag_jobs SET {cols} WHERE id=?", [*fields.values(), job_id])
    con.commit()


def run_tag_job(con: sqlite3.Connection, job_id: str, *, verbose: bool = False) -> None:
    ensure_tag_jobs_table(con)
    row = con.execute("SELECT * FROM tag_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise LookupError(f"tag_job {job_id} fehlt")
    if row["status"] == "running":
        return
    spec = json.loads(row["spec"])
    rows = resolve_rows_for_spec(con, spec)
    _update_job(
        con,
        job_id,
        status="running",
        started_at=time.time(),
        total=len(rows),
        done=0,
        ok_count=0,
        fail_count=0,
        current_nc_path="",
        last_error="",
    )
    done = ok_c = fail_c = 0

    def on_progress(ev: dict[str, Any]) -> None:
        nonlocal done, ok_c, fail_c
        if ev.get("phase") == "done_one":
            done += 1
            res = ev.get("result") or {}
            if res.get("ok"):
                ok_c += 1
            else:
                fail_c += 1
            _update_job(
                con,
                job_id,
                done=done,
                ok_count=ok_c,
                fail_count=fail_c,
                current_nc_path=res.get("nc_path") or "",
                last_error=(res.get("error") or "")[:300],
            )
        elif ev.get("phase") == "tagging":
            _update_job(con, job_id, current_nc_path=ev.get("nc_path") or "")

    try:
        stats = run_tag_batch(con, rows, verbose=verbose, on_progress=on_progress)
        _update_job(
            con,
            job_id,
            status="done",
            finished_at=time.time(),
            done=stats["total"],
            ok_count=stats["ok"],
            fail_count=stats["failed"],
            current_nc_path="",
        )
    except Exception as exc:
        _update_job(
            con,
            job_id,
            status="error",
            finished_at=time.time(),
            last_error=str(exc)[:300],
        )
        raise


def get_tag_job(con: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    ensure_tag_jobs_table(con)
    row = con.execute("SELECT * FROM tag_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    spec = json.loads(row["spec"])
    paths = list(spec.get("paths") or [])
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "total": row["total"],
        "done": row["done"],
        "ok_count": row["ok_count"],
        "fail_count": row["fail_count"],
        "current_nc_path": row["current_nc_path"],
        "last_error": row["last_error"],
        "spec": spec,
        "paths": paths,
    }


def spawn_tag_job_subprocess(job_id: str) -> None:
    install = os.getenv("PHOTOTAG_INSTALL_DIR", "/opt/phototag")
    py = sys.executable
    log_dir = Path(install) / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"tag-job-{job_id}.log"
    with open(log_file, "ab") as logfh:
        subprocess.Popen(
            [py, "-m", "phototag", "tag-job-run", job_id],
            cwd=install,
            env=os.environ.copy(),
            start_new_session=True,
            stdout=logfh,
            stderr=subprocess.STDOUT,
        )


def tag_nc_path_sync(
    con: sqlite3.Connection,
    nc_path: str,
    *,
    retag: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    nc_path = nc_path.replace("\\", "/").lstrip("/")
    row = find_image_by_nc_rel(con, nc_path)
    if not row:
        row = ensure_indexed_from_mount(con, nc_path)
    if not retag and row["status"] == "tagged":
        return {
            "ok": True,
            "skipped": True,
            "nc_path": nc_path,
            "status": "tagged",
            "message": "bereits tagged — retag=1 nutzen",
        }
    return tag_one_row(con, row, verbose=verbose)
