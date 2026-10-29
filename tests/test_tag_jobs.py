"""Tests for tag job resolution and SQLite job rows."""
from __future__ import annotations

import pytest

from phototag.cli import connect
from phototag.tag_jobs import create_tag_job, get_tag_job, preview_tag_job, resolve_rows_for_spec


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "sub").mkdir()
    (mount / "sub" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (mount / "skip.txt").write_text("x")
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    return mount


def test_resolve_rows_from_mount_folder(env_db):
    mount = env_db
    con = connect()
    spec = {
        "folders": ["sub"],
        "nc_paths": [],
        "retag": False,
        "recursive": True,
        "kind": None,
        "limit": 0,
        "include_db_folder_rows": False,
    }
    rows = resolve_rows_for_spec(con, spec)
    assert len(rows) == 1
    assert rows[0]["path"] == "sub/a.jpg"


def test_create_tag_job_skips_tagged(env_db):
    mount = env_db
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(mount), "sub/a.jpg", "sub", "image", "tagged"),
    )
    con.commit()
    job_id = create_tag_job(con, folders=["sub"], retag=False, recursive=True)
    job = get_tag_job(con, job_id)
    assert job is not None
    assert job["total"] == 0


def test_create_tag_job_counts_new(env_db):
    mount = env_db
    con = connect()
    job_id = create_tag_job(con, folders=["sub"], retag=False, recursive=True)
    job = get_tag_job(con, job_id)
    assert job["total"] == 1
    assert job["status"] == "pending"


def test_resolve_nc_path_retag_includes_tagged(env_db):
    mount = env_db
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(mount), "sub/a.jpg", "sub", "image", "tagged"),
    )
    con.commit()
    spec = {
        "folders": [],
        "nc_paths": ["sub/a.jpg"],
        "retag": False,
        "recursive": False,
    }
    assert len(resolve_rows_for_spec(con, spec)) == 0
    spec["retag"] = True
    rows = resolve_rows_for_spec(con, spec)
    assert len(rows) == 1
    assert rows[0]["status"] == "tagged"


def test_preview_skips_tagged(env_db):
    mount = env_db
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(mount), "sub/a.jpg", "sub", "image", "tagged"),
    )
    con.commit()
    stats = preview_tag_job(con, folders=["sub"], retag=False, recursive=True)
    assert stats["media_files"] == 1
    assert stats["will_tag"] == 0
    assert stats["skipped_tagged"] == 1
