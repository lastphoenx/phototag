"""Tests for admin folder browse."""
from __future__ import annotations

import pytest

from phototag.admin_browse import (
    is_direct_child_of_folder,
    list_browse_rows,
    list_child_folders,
    normalize_folder,
    nc_parent_dir,
)
from phototag.cli import connect


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/of")
    return db


def _seed(con):
    root = "/mnt/of"
    rows = [
        (root, "a.jpg", "", "image", "tagged"),
        (root, "Zum Löschen/x.jpg", "Zum Löschen", "image", "new"),
        (root, "Zum Löschen/Test/y.jpg", "Zum Löschen/Test", "image", "review"),
    ]
    for r, path, folder, kind, status in rows:
        con.execute(
            "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
            (r, path, folder, kind, status),
        )
    con.commit()


def test_list_child_folders(env_db):
    con = connect()
    _seed(con)
    assert list_child_folders(con, "/mnt/of", "") == ["Zum Löschen"]
    assert list_child_folders(con, "/mnt/of", "Zum Löschen") == ["Zum Löschen/Test"]
    assert normalize_folder(" foo/ ") == "foo"


def test_list_browse_filter_review(env_db):
    con = connect()
    _seed(con)
    rows, total = list_browse_rows(con, "/mnt/of", "Zum Löschen/Test", "review")
    assert total == 1
    assert rows[0]["path"] == "Zum Löschen/Test/y.jpg"
    rows2, total2 = list_browse_rows(con, "/mnt/of", "Zum Löschen", "untagged")
    assert total2 == 1
    assert rows2[0]["path"] == "Zum Löschen/x.jpg"
    root_only, n = list_browse_rows(con, "/mnt/of", "", "all")
    assert n == 1
    assert root_only[0]["path"] == "a.jpg"


def test_subfolder_scan_root(env_db, monkeypatch):
    """Scan only .../Zum Löschen/Test: DB folder empty, browse via NC path."""
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/of")
    con = connect()
    scan_root = "/mnt/of/Zum Löschen/Test"
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (scan_root, "a.mov", "", "video", "tagged"),
    )
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (scan_root, "b.jpg", "", "image", "review"),
    )
    con.commit()
    assert nc_parent_dir(scan_root, "a.mov") == "Zum Löschen/Test"
    assert list_child_folders(con, scan_root, "") == ["Zum Löschen"]
    assert list_child_folders(con, scan_root, "Zum Löschen") == ["Zum Löschen/Test"]
    rows, total = list_browse_rows(con, scan_root, "", "all")
    assert total == 0
    rows2, total2 = list_browse_rows(con, scan_root, "Zum Löschen/Test", "all")
    assert total2 == 2
    assert is_direct_child_of_folder(scan_root, "a.mov", "Zum Löschen/Test")
