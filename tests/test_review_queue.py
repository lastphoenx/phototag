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
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/of")
    cat = tmp_path / "categories.txt"
    cat.write_text("outdoor\nindoor\nbreasts\n", encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(cat))
    return db


def test_nc_dav_relative_path_subfolder_scan(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", "/mnt/nextcloud-of")
    from phototag.review_queue import nc_display_path

    class R:
        root = "/mnt/nextcloud-of/Zum Löschen/Test"
        path = "IMG_1599.MOV"

    assert nc_display_path(R()) == "Zum Löschen/Test/IMG_1599.MOV"


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
            "/mnt/of/Test",
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
    result = approve_image(con, iid, tags=["breasts", "indoor"])
    assert result["tags"] == ["breasts", "indoor"]
    row = con.execute("SELECT status, caption FROM images WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "tagged"
    assert row["caption"] == "Test caption"
    tags = [r[0] for r in con.execute("SELECT tag FROM tags WHERE image_id=?", (iid,))]
    assert tags == ["breasts", "indoor"]


def test_row_to_api_dict_marks_review(env_db):
    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status,age_status,model_raw) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            "/mnt/of",
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
    allowed = {"outdoor", "indoor", "breasts"}
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
            "/mnt/of",
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
