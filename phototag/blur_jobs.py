"""Face-blur jobs for the admin UI: background subprocess, SQLite status, review of copies.

Flow per file: original (untouched) -> temp file -> copy next to the original as
``<stem>.ai-blurred.<ext>`` (rclone mount uploads it to Nextcloud) -> DB row with status
``blurred_review``. A human then approves (``blurred_ok``) or rejects (file + row deleted).
Neither status is picked up by the night loop; KI tagging stays a manual action.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from phototag.admin_mount import (
    ensure_indexed_from_mount,
    nc_rel_from_dir_file,
    physical_file_path,
)
from phototag.config import faceblur_keep_empty, faceblur_suffix, read_phototag_env, load_phototag_env
from phototag.faceblur import (
    BLUR_OK,
    BLUR_REVIEW,
    BLUR_STATUSES,
    FaceBlurError,
    FaceDetector,
    NoFacesFound,
    blur_file,
    blurred_filename,
    is_blurred_name,
    sweep_stale_blur_temps,
    temp_output_path,
    unique_destination,
)
from phototag.nextcloud_sync import nc_dav_relative_path

STALE_AFTER_SEC = 120

BLUR_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS blur_jobs(
  id TEXT PRIMARY KEY,
  nc_path TEXT NOT NULL,
  status TEXT NOT NULL,           -- pending | running | done | no_faces | error
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  heartbeat_at REAL,
  progress REAL NOT NULL DEFAULT 0,
  result TEXT,                    -- JSON
  error TEXT
);
"""


class BlurBusy(RuntimeError):
    """Another blur job is active (one at a time: CPU/IO heavy)."""


def ensure_blur_jobs_table(con: sqlite3.Connection) -> None:
    con.executescript(BLUR_JOBS_DDL)
    con.commit()


def _update(con: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE blur_jobs SET {cols} WHERE id=?", [*fields.values(), job_id])
    con.commit()


def active_job(con: sqlite3.Connection) -> sqlite3.Row | None:
    ensure_blur_jobs_table(con)
    cutoff = time.time() - STALE_AFTER_SEC
    return con.execute(
        "SELECT * FROM blur_jobs WHERE status IN ('pending','running') "
        "AND COALESCE(heartbeat_at, created_at) > ? ORDER BY created_at LIMIT 1",
        (cutoff,),
    ).fetchone()


def validate_source(nc_rel: str) -> Path:
    nc_rel = nc_rel.replace("\\", "/").lstrip("/")
    if not nc_rel:
        raise FaceBlurError("nc_path fehlt")
    physical = physical_file_path(nc_rel)
    if not physical.is_file():
        raise FileNotFoundError(f"Datei fehlt auf Mount: {nc_rel}")
    if is_blurred_name(physical.name):
        raise FaceBlurError("Das ist bereits eine Blur-Kopie")
    blurred_filename(physical.name)  # raises for unsupported types
    return physical


def create_blur_job(con: sqlite3.Connection, nc_rel: str) -> str:
    nc_rel = nc_rel.replace("\\", "/").lstrip("/")
    validate_source(nc_rel)
    busy = active_job(con)
    if busy:
        raise BlurBusy(f"Es läuft bereits ein Blur-Job für {busy['nc_path']}")
    job_id = uuid.uuid4().hex[:16]
    con.execute(
        "INSERT INTO blur_jobs(id,nc_path,status,created_at,heartbeat_at) VALUES(?,?,?,?,?)",
        (job_id, nc_rel, "pending", time.time(), time.time()),
    )
    con.commit()
    return job_id


def get_blur_job(con: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    ensure_blur_jobs_table(con)
    row = con.execute("SELECT * FROM blur_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    status = row["status"]
    beat = row["heartbeat_at"] or row["created_at"]
    if status in ("pending", "running") and time.time() - beat > STALE_AFTER_SEC:
        status = "stale"
    result = json.loads(row["result"]) if row["result"] else None
    return {
        "id": row["id"],
        "nc_path": row["nc_path"],
        "status": status,
        "progress": row["progress"],
        "result": result,
        "error": row["error"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def spawn_blur_job_subprocess(job_id: str) -> None:
    install = os.getenv("PHOTOTAG_INSTALL_DIR", "/opt/phototag")
    log_dir = Path(install) / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(read_phototag_env())
    with open(log_dir / f"blur-job-{job_id}.log", "ab") as logfh:
        subprocess.Popen(
            [sys.executable, "-m", "phototag", "blur-job-run", job_id],
            cwd=install, env=env, start_new_session=True,
            stdout=logfh, stderr=subprocess.STDOUT,
        )


# ---------------------------------------------------------------- DB rows

def register_blurred_file(
    con: sqlite3.Connection,
    dest_nc_rel: str,
    *,
    source_id: int,
    source_name: str,
    faces: int,
    info: str = "",
) -> int:
    """Insert (or reset) the row of a freshly written blur copy with status ``blurred_review``."""
    from phototag.cli import IMAGE_EXTS, VIDEO_EXTS, encode_image, encode_video, sha1_of
    from phototag.config import scan_root

    physical = physical_file_path(dest_nc_rel)
    root = str(Path(scan_root()).resolve())
    rel = dest_nc_rel.replace("\\", "/").lstrip("/")
    folder = Path(rel).parent.as_posix()
    folder = "" if folder == "." else folder
    kind = "video" if physical.suffix.lower() in VIDEO_EXTS else "image"
    st = physical.stat()
    thumb = None
    try:
        if kind == "video":
            thumb = encode_video(physical)["thumb"]
        elif physical.suffix.lower() in IMAGE_EXTS:
            thumb = encode_image(physical)["thumb"]
    except (OSError, ValueError):
        thumb = None
    caption = f"AI-Blur: {faces} Gesicht(er) in {source_name} verdeckt (Original unverändert)"
    note = f"faceblur:faces={faces}" + (f"; {info}" if info else "")
    note = note[:600]
    existing = con.execute("SELECT id FROM images WHERE root=? AND path=?", (root, rel)).fetchone()
    values = (st.st_size, st.st_mtime, sha1_of(physical), folder, kind, thumb, BLUR_REVIEW, caption, note, source_id, faces)
    if existing:
        con.execute(
            "UPDATE images SET size=?,mtime=?,sha1=?,folder=?,kind=?,thumb=?,status=?,caption=?,error=?,"
            "derived_from=?,blur_faces=? WHERE id=?",
            (*values, existing["id"]),
        )
        con.execute("DELETE FROM tags WHERE image_id=?", (existing["id"],))
        con.commit()
        return int(existing["id"])
    cur = con.execute(
        "INSERT INTO images(root,path,folder,kind,size,mtime,sha1,thumb,status,caption,error,derived_from,blur_faces) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (root, rel, folder, kind, st.st_size, st.st_mtime, values[2], thumb, BLUR_REVIEW, caption, note, source_id, faces),
    )
    con.commit()
    return int(cur.lastrowid)


def blur_one_file(
    con: sqlite3.Connection,
    nc_rel: str,
    *,
    detector: FaceDetector | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Create the blur copy for ``nc_rel`` and register it. Raises NoFacesFound / FaceBlurError."""
    nc_rel = nc_rel.replace("\\", "/").lstrip("/")
    src = validate_source(nc_rel)
    source_row = ensure_indexed_from_mount(con, nc_rel)
    out_name = blurred_filename(src.name)
    sweep_stale_blur_temps()
    tmp = temp_output_path(Path(out_name).suffix)
    try:
        res = blur_file(
            src, tmp,
            detector=detector or FaceDetector(),
            progress=progress,
            allow_empty=faceblur_keep_empty(),
        )
        dest = unique_destination(src.parent, out_name)
        shutil.copyfile(tmp, dest)
        if dest.stat().st_size != tmp.stat().st_size:
            dest.unlink(missing_ok=True)
            raise FaceBlurError("Kopie auf den Mount unvollständig (Grösse weicht ab)")
    finally:
        tmp.unlink(missing_ok=True)
    parent_logical = Path(nc_rel).parent.as_posix()
    dest_nc = nc_rel_from_dir_file("" if parent_logical == "." else parent_logical, dest.name)
    new_id = register_blurred_file(
        con, dest_nc, source_id=source_row["id"], source_name=src.name, faces=res.faces,
        info="; ".join(res.notes),
    )
    return {
        "ok": True,
        "id": new_id,
        "source_id": source_row["id"],
        "source": nc_rel,
        "nc_path": dest_nc,
        "faces": res.faces,
        "kind": res.kind,
        "frames": res.frames,
        "notes": res.notes,
        "status": BLUR_REVIEW,
    }


def run_blur_job(con: sqlite3.Connection, job_id: str) -> None:
    load_phototag_env(override=True)
    ensure_blur_jobs_table(con)
    row = con.execute("SELECT * FROM blur_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise LookupError(f"blur_job {job_id} fehlt")
    if row["status"] != "pending":
        return
    _update(con, job_id, status="running", started_at=time.time(), heartbeat_at=time.time())
    last_beat = 0.0

    def on_progress(frac: float) -> None:
        nonlocal last_beat
        now = time.time()
        if now - last_beat >= 2.0 or frac >= 1.0:
            last_beat = now
            _update(con, job_id, progress=round(float(frac), 3), heartbeat_at=now)

    try:
        result = blur_one_file(con, row["nc_path"], progress=on_progress)
        _update(con, job_id, status="done", finished_at=time.time(), progress=1.0,
                result=json.dumps(result, ensure_ascii=False), heartbeat_at=time.time())
    except NoFacesFound as exc:
        _update(con, job_id, status="no_faces", finished_at=time.time(), error=str(exc))
    except (FaceBlurError, OSError, ValueError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _update(con, job_id, status="error", finished_at=time.time(), error=str(exc)[:400])
    except Exception as exc:  # never leave a job in 'running'
        _update(con, job_id, status="error", finished_at=time.time(), error=f"{type(exc).__name__}: {exc}"[:400])
        raise


# ---------------------------------------------------------------- review

def _blur_row(con: sqlite3.Connection, image_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise LookupError(f"Kein Eintrag id={image_id}")
    if row["derived_from"] is None:
        raise ValueError(f"id={image_id} ist keine Blur-Kopie")
    return row


def approve_blur(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    row = _blur_row(con, image_id)
    if row["status"] != BLUR_REVIEW:
        raise ValueError(f"id={image_id} status={row['status']}, erwartet {BLUR_REVIEW}")
    con.execute("UPDATE images SET status=? WHERE id=?", (BLUR_OK, image_id))
    con.commit()
    return {"ok": True, "id": image_id, "status": BLUR_OK}


def reject_blur(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    """Delete the generated copy (mount + Nextcloud) and its row. Only for our own copies."""
    row = _blur_row(con, image_id)
    if row["status"] not in (BLUR_REVIEW, BLUR_OK):
        raise ValueError(f"id={image_id} status={row['status']}: nur Blur-Kopien in Prüfung löschbar")
    nc_rel = nc_dav_relative_path(row["root"], row["path"])
    physical = physical_file_path(nc_rel)
    if not is_blurred_name(physical.name, faceblur_suffix()):
        raise ValueError("Dateiname enthält nicht das Blur-Suffix — nicht gelöscht")
    if physical.is_file():
        physical.unlink()
    con.execute("DELETE FROM images WHERE id=?", (image_id,))
    con.commit()
    return {"ok": True, "id": image_id, "deleted": nc_rel}


def blur_copies_by_source(con: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    """source image id -> blur copies (for the 'Blur vorhanden' hint on original cards)."""
    out: dict[int, list[dict[str, Any]]] = {}
    for r in con.execute(
        "SELECT id, root, path, status, derived_from FROM images WHERE derived_from IS NOT NULL"
    ):
        out.setdefault(int(r["derived_from"]), []).append(
            {"id": r["id"], "nc_path": nc_dav_relative_path(r["root"], r["path"]), "status": r["status"]}
        )
    return out


__all__ = [
    "BLUR_OK",
    "BLUR_REVIEW",
    "BLUR_STATUSES",
    "BlurBusy",
    "approve_blur",
    "blur_copies_by_source",
    "blur_one_file",
    "create_blur_job",
    "get_blur_job",
    "reject_blur",
    "run_blur_job",
    "spawn_blur_job_subprocess",
]
