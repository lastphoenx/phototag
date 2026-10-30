"""Tests for tag filters and row scope."""
from __future__ import annotations

import pytest

from phototag.cli import connect
from phototag.tagging import nc_path_of_row, row_in_scope, select_tag_candidates


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    mount = tmp_path / "mount"
    mount.mkdir()
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    return mount


def _insert(con, mount, path, folder, status="new"):
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(mount), path, folder, "image", status),
    )
    con.commit()
    return con.execute("SELECT * FROM images WHERE path=?", (path,)).fetchone()


def test_row_in_scope_nc_and_folder(env_db):
    mount = env_db
    con = connect()
    r1 = _insert(con, mount, "a.jpg", "", "new")
    r2 = _insert(con, mount, "Photos/b.jpg", "Photos", "new")
    nc1 = nc_path_of_row(r1)
    nc2 = nc_path_of_row(r2)
    assert row_in_scope(r1, nc_paths={nc1}, folders=[], recursive=True)
    assert not row_in_scope(r2, nc_paths={nc1}, folders=[], recursive=True)
    assert row_in_scope(r2, nc_paths=set(), folders=["Photos"], recursive=True)
    assert row_in_scope(r2, nc_paths=set(), folders=["Photos"], recursive=False)  # direktes Kind
    r3 = _insert(con, mount, "Photos/sub/c.jpg", "Photos/sub", "new")
    assert row_in_scope(r3, nc_paths=set(), folders=["Photos"], recursive=True)
    assert not row_in_scope(r3, nc_paths=set(), folders=["Photos"], recursive=False)


def test_select_tag_candidates_review_not_default(env_db):
    mount = env_db
    con = connect()
    _insert(con, mount, "d/x.jpg", "d", "review")
    rows = select_tag_candidates(con, folders=["d"], recursive=True)
    assert len(rows) == 0
    rows2 = select_tag_candidates(con, retag=True, folders=["d"], recursive=True)
    assert len(rows2) == 1


def test_select_tag_candidates_by_nc_path(env_db):
    mount = env_db
    con = connect()
    r = _insert(con, mount, "only/me.jpg", "only", "new")
    nc = nc_path_of_row(r)
    rows = select_tag_candidates(con, nc_paths=[nc], recursive=True)
    assert len(rows) == 1
    assert rows[0]["path"] == "only/me.jpg"
