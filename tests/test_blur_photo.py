"""Brush corrections of photo blur copies."""
from __future__ import annotations

import numpy as np
import pytest

from test_faceblur import FakeDetector, client, mount_env  # noqa: F401  (shared fixtures)

from phototag import blur_photo as bp
from phototag.blur_jobs import blur_one_file
from phototag.cli import connect


def _original(mount):
    from PIL import Image
    return np.asarray(Image.open(mount / "Album" / "a.png").convert("RGB"))[:, :, ::-1]


@pytest.fixture
def photo_copy(mount_env):
    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    return mount_env, con, res


def test_the_photo_job_keeps_the_faces_for_the_brush(photo_copy):
    mount, con, res = photo_copy
    p = bp.load_photo(con, res["id"])
    assert p["faces"] and p["strokes"] == [] and p["rev"] == 0
    assert p["size"] == (_original(mount).shape[1], _original(mount).shape[0])
    payload = bp.photo_payload(con, res["id"])
    assert payload["kind"] == "image" and payload["shapes"] and payload["pending"] is False
    assert all(0 <= x <= 1 and 0 <= y <= 1 for poly in payload["shapes"] for x, y in poly)


def test_stroke_validation(photo_copy):
    _, con, res = photo_copy
    ok = bp.set_strokes(con, res["id"], [{"op": "add", "size": 0.05, "pts": [[0.1, 0.2], [0.3, 0.4]]}])
    assert ok["strokes"] == 1 and ok["pending"] is True and ok["rev"] == 1
    for bad in ("x", [{"op": "paint", "size": 0.1, "pts": [[0, 0]]}], [{"op": "add", "size": 0.1, "pts": []}],
                [{"op": "add", "size": "a", "pts": [[0, 0]]}], [{"op": "add", "size": 0.1, "pts": [[0]]}],
                [{"op": "add", "size": 0.1, "pts": [[0, 0]]}] * (bp.MAX_STROKES + 1)):
        with pytest.raises(ValueError):
            bp.set_strokes(con, res["id"], bad)
    s = bp.clean_strokes([{"op": "erase", "size": 5, "pts": [[-3, 9]]}])[0]
    assert s["size"] == bp.MAX_SIZE and s["pts"] == [[0.0, 1.0]]                         # clamped, never trusted


def test_add_stroke_blurs_more_and_erase_stroke_gives_the_original_back(photo_copy):
    mount, con, res = photo_copy
    orig = _original(mount)
    h, w = orig.shape[:2]
    p = bp.load_photo(con, res["id"])
    auto = bp.render_array(orig, p["faces"], [])
    fx, fy = p["faces"][0].x + p["faces"][0].w / 2, p["faces"][0].y + p["faces"][0].h / 2
    far = (0.9, 0.9) if (fx / w) < 0.6 and (fy / h) < 0.6 else (0.1, 0.1)               # somewhere the face blur is not
    add = bp.render_array(orig, p["faces"], [{"op": "add", "size": 0.12, "pts": [list(far)]}])
    cx, cy = int(far[0] * (w - 1)), int(far[1] * (h - 1))
    win = (slice(max(0, cy - 8), cy + 8), slice(max(0, cx - 8), cx + 8))
    assert auto[win].std() > 5 and add[win].std() < auto[win].std() * 0.7                # noise image: blur flattens it
    centre = (int(fx), int(fy))
    erase = bp.render_array(orig, p["faces"], [{"op": "erase", "size": 0.2, "pts": [[fx / w, fy / h]]}])
    assert np.array_equal(erase[centre[1], centre[0]], orig[centre[1], centre[0]])       # the blur is gone in the middle
    assert not np.array_equal(auto[centre[1], centre[0]], orig[centre[1], centre[0]])
    both = bp.render_array(orig, p["faces"], [{"op": "erase", "size": 0.2, "pts": [[fx / w, fy / h]]},
                                              {"op": "add", "size": 0.05, "pts": [[fx / w, fy / h]]}])
    assert not np.array_equal(both[centre[1], centre[0]], orig[centre[1], centre[0]])    # later strokes win


def test_rerender_overwrites_the_copy_and_never_touches_the_original(photo_copy):
    import hashlib
    mount, con, res = photo_copy
    src, dest = mount / "Album" / "a.png", mount / "Album" / "a.ai-blurred.png"
    before_src, before_copy = hashlib.sha1(src.read_bytes()).hexdigest(), hashlib.sha1(dest.read_bytes()).hexdigest()
    bp.set_strokes(con, res["id"], [{"op": "add", "size": 0.2, "pts": [[0.5, 0.5], [0.8, 0.8]]}])
    out = bp.rerender_photo(con, res["id"])
    assert out["status"] == "blurred_review" and out["id"] == res["id"]
    assert hashlib.sha1(src.read_bytes()).hexdigest() == before_src
    assert hashlib.sha1(dest.read_bytes()).hexdigest() != before_copy
    row = con.execute("SELECT sha1, status FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["sha1"] == hashlib.sha1(dest.read_bytes()).hexdigest() and row["status"] == "blurred_review"
    assert bp.photo_payload(con, res["id"])["pending"] is False


def test_photo_api_roundtrip(photo_copy, client):
    mount, con, res = photo_copy
    cid = res["id"]
    rv = client.get(f"/api/blur/{cid}/review")
    assert rv.status_code == 200 and rv.json["kind"] == "image" and rv.json["shapes"]
    for view in ("original", "copy"):
        img = client.get(f"/api/blur/{cid}/photo?view={view}&w=400")
        assert img.status_code == 200 and img.data[:2] == b"\xff\xd8" and img.headers["Cache-Control"] == "private, no-store"
    put = client.put(f"/api/blur/{cid}/strokes", json={"strokes": [{"op": "erase", "size": 0.1, "pts": [[0.4, 0.4], [0.5, 0.5]]}]})
    assert put.status_code == 200 and put.json["pending"] is True
    assert client.put(f"/api/blur/{cid}/strokes", json={"strokes": "kaputt"}).status_code == 400
    assert client.get(f"/api/blur/{cid}/review").json["pending"] is True
    done = client.post(f"/api/blur/{cid}/photo-render")
    assert done.status_code == 200 and done.json["ok"] and client.get(f"/api/blur/{cid}/review").json["pending"] is False


def test_rejecting_a_photo_copy_removes_its_brush_data(photo_copy):
    from phototag.blur_jobs import reject_blur
    _, con, res = photo_copy
    reject_blur(con, res["id"])
    with pytest.raises(LookupError):
        bp.load_photo(con, res["id"])


def test_changed_original_is_refused(photo_copy):
    from PIL import Image
    mount, con, res = photo_copy
    Image.new("RGB", (40, 30), (9, 9, 9)).save(mount / "Album" / "a.png")
    with pytest.raises(Exception, match="geändert"):
        bp.rerender_photo(con, res["id"])


def test_admin_ui_has_the_photo_brush():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "phototag/static/admin"
    js, css = (root / "admin.js").read_text(encoding="utf-8"), (root / "admin.css").read_text(encoding="utf-8")
    for needle in ("renderPhoto", "/strokes", "/photo-render", "/photo?view=", "Pinsel: Blur ergänzen",
                   "Radierer: Blur entfernen", "Speichern & Kopie neu erzeugen", "Ungespeicherte Pinselstriche", "setPointerCapture"):
        assert needle in js, needle
    assert ".br-canvas" in css and "touch-action: none" in css
