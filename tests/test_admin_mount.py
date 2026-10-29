"""Mount-first admin browse tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from phototag.admin_mount import (
    list_media_files_on_mount,
    list_mount_browse_items,
    nc_rel_from_dir_file,
    save_mount_tags,
)
from phototag.cli import connect


@pytest.fixture
def mount_env(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "Zum Löschen" / "Test").mkdir(parents=True)
    (mount / "Photos").mkdir()
    (mount / "Zum Löschen" / "Test" / "a.jpg").write_bytes(b"x")
    (mount / "Zum Löschen" / "Test" / "b.mov").write_bytes(b"y")
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    cat = tmp_path / "categories.txt"
    cat.write_text("outdoor\nindoor\n", encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(cat))
    return mount


def test_list_media_on_mount(mount_env):
    assert list_media_files_on_mount("Zum Löschen/Test") == [
        "Zum Löschen/Test/a.jpg",
        "Zum Löschen/Test/b.mov",
    ]
    assert nc_rel_from_dir_file("Zum Löschen/Test", "x.jpg") == "Zum Löschen/Test/x.jpg"


def test_mount_browse_merge_db(mount_env, monkeypatch):
    con = connect()
    root = str(Path(mount_env).resolve())
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,caption) VALUES(?,?,?,?,?,?)",
        (root, "Zum Löschen/Test/a.jpg", "Zum Löschen/Test", "image", "tagged", "cap"),
    )
    con.execute("INSERT INTO tags VALUES(1,'outdoor')")
    con.commit()
    allowed = {"outdoor", "indoor"}
    items, total = list_mount_browse_items(con, "Zum Löschen/Test", "all", allowed)
    assert total == 2
    tagged = [i for i in items if i["nc_path"].endswith("a.jpg")][0]
    assert tagged["caption"] == "cap"
    assert "outdoor" in tagged["tags"]


def test_tagged_filter_recursive_under_folder(mount_env):
    con = connect()
    root = str(Path(mount_env).resolve())
    sub = mount_env / "Zum Löschen" / "Test" / "sub"
    sub.mkdir()
    (sub / "deep.jpg").write_bytes(b"x")
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (root, "Zum Löschen/Test/sub/deep.jpg", "Zum Löschen/Test/sub", "image", "tagged"),
    )
    con.commit()
    _, flat_total = list_mount_browse_items(con, "Zum Löschen/Test", "tagged", {"outdoor"})
    assert flat_total == 0
    _, rec_total = list_mount_browse_items(
        con, "Zum Löschen/Test", "tagged", {"outdoor"}, recursive=True
    )
    assert rec_total == 1


def test_tagged_filter_uses_db_not_mount_walk(mount_env):
    con = connect()
    root = str(Path(mount_env).resolve())
    folder = Path(mount_env) / "Zum Löschen" / "Test"
    for i in range(120):
        (folder / f"noise_{i:03d}.jpg").write_bytes(b"x")
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (root, "Zum Löschen/Test/a.jpg", "Zum Löschen/Test", "image", "tagged"),
    )
    con.commit()
    items, total = list_mount_browse_items(con, "Zum Löschen/Test", "tagged", {"outdoor"})
    assert total == 1
    assert items[0]["nc_path"].endswith("a.jpg")


def test_save_mount_tags(mount_env, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_NC_DAV_PREFIX", "OF")
    con = connect()
    allowed = {"outdoor", "indoor"}
    result = save_mount_tags(
        con,
        "Zum Löschen/Test/a.jpg",
        tags=["outdoor"],
        caption="Manuell",
        push_nc=False,
        allowed=allowed,
    )
    assert result["tags"] == ["outdoor"]
    row = con.execute("SELECT status FROM images WHERE id=?", (result["id"],)).fetchone()
    assert row["status"] == "tagged"
