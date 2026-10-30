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
    (mount / "Album" / "Test").mkdir(parents=True)
    (mount / "Photos").mkdir()
    (mount / "Album" / "Test" / "a.jpg").write_bytes(b"x")
    (mount / "Album" / "Test" / "b.mov").write_bytes(b"y")
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    cat = tmp_path / "categories.txt"
    cat.write_text("outdoor\nindoor\n", encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(cat))
    return mount


def test_list_media_on_mount(mount_env):
    assert list_media_files_on_mount("Album/Test") == [
        "Album/Test/a.jpg",
        "Album/Test/b.mov",
    ]
    assert nc_rel_from_dir_file("Album/Test", "x.jpg") == "Album/Test/x.jpg"


def test_mount_browse_merge_db(mount_env, monkeypatch):
    con = connect()
    root = str(Path(mount_env).resolve())
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,caption) VALUES(?,?,?,?,?,?)",
        (root, "Album/Test/a.jpg", "Album/Test", "image", "tagged", "cap"),
    )
    con.execute("INSERT INTO tags VALUES(1,'outdoor')")
    con.commit()
    allowed = {"outdoor", "indoor"}
    items, total = list_mount_browse_items(con, "Album/Test", "all", allowed)
    assert total == 2
    tagged = [i for i in items if i["nc_path"].endswith("a.jpg")][0]
    assert tagged["caption"] == "cap"
    assert "outdoor" in tagged["tags"]


def test_tagged_filter_recursive_under_folder(mount_env):
    con = connect()
    root = str(Path(mount_env).resolve())
    sub = mount_env / "Album" / "Test" / "sub"
    sub.mkdir()
    (sub / "deep.jpg").write_bytes(b"x")
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (root, "Album/Test/sub/deep.jpg", "Album/Test/sub", "image", "tagged"),
    )
    con.commit()
    _, flat_total = list_mount_browse_items(con, "Album/Test", "tagged", {"outdoor"})
    assert flat_total == 0
    _, rec_total = list_mount_browse_items(
        con, "Album/Test", "tagged", {"outdoor"}, recursive=True
    )
    assert rec_total == 1


def test_blurred_filter_always_includes_subfolders(mount_env):
    con = connect()
    root = str(Path(mount_env).resolve())
    sub = mount_env / "Album" / "Test" / "sub"
    sub.mkdir()
    (sub / "x.ai-blurred.jpg").write_bytes(b"blur")
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,derived_from,blur_faces) VALUES(?,?,?,?,?,?,?)",
        (root, "Album/Test/sub/x.ai-blurred.jpg", "Album/Test/sub", "image", "blurred_review", 1, 1),
    )
    con.commit()
    items, total = list_mount_browse_items(con, "Album/Test", "blurred", set(), recursive=False)
    assert total == 1
    assert items[0]["nc_path"] == "Album/Test/sub/x.ai-blurred.jpg"


def test_tagged_filter_uses_db_not_mount_walk(mount_env):
    con = connect()
    root = str(Path(mount_env).resolve())
    folder = Path(mount_env) / "Album" / "Test"
    for i in range(120):
        (folder / f"noise_{i:03d}.jpg").write_bytes(b"x")
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (root, "Album/Test/a.jpg", "Album/Test", "image", "tagged"),
    )
    con.commit()
    items, total = list_mount_browse_items(con, "Album/Test", "tagged", {"outdoor"})
    assert total == 1
    assert items[0]["nc_path"].endswith("a.jpg")


def test_save_mount_tags(mount_env, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_NC_DAV_PREFIX", "Media")
    con = connect()
    allowed = {"outdoor", "indoor"}
    result = save_mount_tags(
        con,
        "Album/Test/a.jpg",
        tags=["outdoor"],
        caption="Manuell",
        push_nc=False,
        allowed=allowed,
    )
    assert result["tags"] == ["outdoor"]
    row = con.execute("SELECT status FROM images WHERE id=?", (result["id"],)).fetchone()
    assert row["status"] == "tagged"
