"""Admin DB search API and query builder."""
from __future__ import annotations

import sqlite3

import pytest

from phototag.admin_search import build_search_where, search_image_rows
from phototag.cli import connect, index_image


def _seed_tagged(con: sqlite3.Connection, *, path: str, caption: str, tags: list[str]) -> int:
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,caption,mtime,size) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("/mnt", path, path.rsplit("/", 1)[0], "image", "tagged", caption, 1.0, 100),
    )
    iid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    for t in tags:
        con.execute("INSERT INTO tags(image_id, tag) VALUES(?,?)", (iid, t))
    index_image(con, iid)
    con.commit()
    return iid


def test_search_by_tag_and_query(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    con = connect()
    _seed_tagged(con, path="album/beach.jpg", caption="Am Strand.", tags=["outdoor", "portrait"])
    _seed_tagged(con, path="album/ski.jpg", caption="Im Schnee.", tags=["outdoor", "sport"])

    where, params = build_search_where(con, query="strand", tags=None, any_term=False, fuzzy=False, folder="", kind=None)
    assert "MATCH" in where
    rows, total = search_image_rows(
        con,
        query="strand",
        limit=10,
        offset=0,
        columns="i.id, i.path",
    )
    assert total == 1
    assert rows[0]["path"] == "album/beach.jpg"

    rows2, total2 = search_image_rows(
        con,
        tags=["sport"],
        limit=10,
        offset=0,
        columns="i.id, i.path",
    )
    assert total2 == 1
    assert rows2[0]["path"] == "album/ski.jpg"


def test_api_search_requires_criteria(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_ADMIN_USER", "u")
    monkeypatch.setenv("PHOTOTAG_ADMIN_PASSWORD", "p")
    monkeypatch.setenv("PHOTOTAG_ADMIN_SESSION_SECRET", "secret")
    pytest.importorskip("flask")
    from phototag.admin_app import create_app

    client = create_app().test_client()
    assert client.get("/api/search?q=x").status_code == 401
    assert client.post("/login", data={"username": "u", "password": "p"}).status_code == 302
    r = client.get("/api/search")
    assert r.status_code == 400
    connect()
