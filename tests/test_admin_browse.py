"""Tests for admin folder browse."""
from __future__ import annotations

import pytest

from phototag.admin_browse import list_browse_rows, list_child_folders, normalize_folder
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
