"""Tests for admin folder browse."""
from __future__ import annotations

import pytest

from phototag.admin_browse import (
    is_direct_child_of_folder,
    list_browse_rows,
    list_child_folders,
    list_child_folders_fs,
    normalize_folder,
    nc_parent_dir,
    physical_path_for_logical,
)
from phototag.cli import connect


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(tmp_path / "mount"))
    return db


def _seed(con, root="/mnt/of"):
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


def test_list_child_folders(env_db, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "Photos").mkdir(parents=True)
    (mount / "Zum Löschen" / "Test").mkdir(parents=True)
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    con = connect()
    _seed(con, root=str(mount))
    top = list_child_folders(con, str(mount), "")
    assert "Photos" in top
    assert "Zum Löschen" in top
    assert list_child_folders(con, str(mount), "Zum Löschen") == ["Zum Löschen/Test"]
    assert normalize_folder(" foo/ ") == "foo"


def test_list_child_folders_fs(env_db, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "A").mkdir(parents=True)
    (mount / "B").mkdir()
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    assert list_child_folders_fs("") == ["A", "B"]
    assert physical_path_for_logical("A") == mount / "A"


def test_list_browse_filter_review(env_db, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    con = connect()
    _seed(con, root=str(mount))
    rows, total = list_browse_rows(con, "Zum Löschen/Test", "review")
    assert total == 1
    assert rows[0]["path"] == "Zum Löschen/Test/y.jpg"
    rows2, total2 = list_browse_rows(con, "Zum Löschen", "untagged")
    assert total2 == 1
    assert rows2[0]["path"] == "Zum Löschen/x.jpg"
    root_only, n = list_browse_rows(con, "", "all")
    assert n == 1
    assert root_only[0]["path"] == "a.jpg"


def test_subfolder_scan_root(env_db, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    con = connect()
    scan_root_path = mount / "Zum Löschen" / "Test"
    scan_root_path.mkdir(parents=True)
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(scan_root_path), "a.mov", "", "video", "tagged"),
    )
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(scan_root_path), "b.jpg", "", "image", "review"),
    )
    con.commit()
    assert nc_parent_dir(str(scan_root_path), "a.mov") == "Zum Löschen/Test"
    assert list_child_folders(con, str(scan_root_path), "") == ["Zum Löschen"]
    rows, total = list_browse_rows(con, "", "all")
    assert total == 0
    rows2, total2 = list_browse_rows(con, "Zum Löschen/Test", "all")
    assert total2 == 2
    assert is_direct_child_of_folder(str(scan_root_path), "a.mov", "Zum Löschen/Test")
