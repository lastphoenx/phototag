"""Review queue approve and paths."""
from __future__ import annotations

import json

import pytest

from phototag.cli import connect
from phototag.review_queue import approve_image, backfill_review_captions, nc_display_path, row_to_api_dict


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/media")
    cat = tmp_path / "categories.txt"
    cat.write_text("outdoor\nindoor\nportrait\n", encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(cat))
    return db


def test_nc_dav_relative_path_subfolder_scan(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/media")
    from phototag.review_queue import nc_display_path

    row = {"root": "/mnt/media/Album/Test", "path": "IMG_0001.MOV"}

    assert nc_display_path(row) == "Album/Test/IMG_0001.MOV"


def test_approve_review_with_custom_tags(env_db):
    con = connect()
    raw = json.dumps(
        {
            "age_status": "unsure",
            "tags": ["outdoor", "indoor"],
            "caption": "Test caption",
        }
    )
    con.execute(
        "INSERT INTO images(root,path,folder,kind,size,mtime,sha1,status,age_status,model_raw,caption) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "/mnt/media/Test",
            "a.jpg",
            "",
            "image",
            1,
            1.0,
            "x",
            "review",
            "unsure",
            raw,
            "Test caption",
        ),
    )
    con.commit()
    iid = con.execute("SELECT id FROM images").fetchone()[0]
    result = approve_image(con, iid, tags=["portrait", "indoor"])
    assert result["tags"] == ["indoor", "portrait"]
    row = con.execute("SELECT status, caption FROM images WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "tagged"
    assert row["caption"] == "Test caption"
    tags = [r[0] for r in con.execute("SELECT tag FROM tags WHERE image_id=?", (iid,))]
    assert tags == ["indoor", "portrait"]


def test_row_to_api_dict_marks_review(env_db):
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,age_status,model_raw) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            "/mnt/media",
            "c.jpg",
            "",
            "image",
            "review",
            "unsure",
            '{"age_status":"unsure","tags":["outdoor"],"caption":"x"}',
        ),
    )
    con.commit()
    row = con.execute("SELECT * FROM images").fetchone()
    allowed = {"outdoor", "indoor", "portrait"}
    d = row_to_api_dict(row, allowed)
    assert d["status"] == "review"
    assert d["in_review"] is True
    assert "outdoor" in d["proposed_tags"]


def test_backfill_captions(env_db):
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,size,mtime,sha1,status,model_raw) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "/mnt/media",
            "b.jpg",
            "",
            "image",
            1,
            1.0,
            "y",
            "review",
            '{"age_status":"unsure","tags":[],"caption":"Hallo Welt"}',
        ),
    )
    con.commit()
    assert backfill_review_captions(con) == 1
    cap = con.execute("SELECT caption FROM images").fetchone()[0]
    assert cap == "Hallo Welt"
