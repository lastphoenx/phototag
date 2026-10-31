"""Review of a video blur: stored analysis, timeline, corrections, re-render, API."""
from __future__ import annotations

import pytest

from test_faceblur import client, mount_env  # noqa: F401  (shared fixtures)
from test_faceblur_video import Scripted, face, make_video, needs_ffmpeg

from phototag import blur_review as br
from phototag import faceblur_video as fv
from phototag.cli import connect


def analysis(total=300, fps=30.0) -> fv.Analysis:
    """One sure track (frames 30-90), a weak stretch (120-150, only interpolated between weak hits),
    a dropped candidate at 200, nothing elsewhere."""
    sure = fv.Track(1, 0, [(i, face(100 + i, 80, 100, 130, 0.92)) for i in range(30, 93, 3)])
    weak = fv.Track(2, 0, [(i, face(300, 90, 100, 130, 0.55)) for i in range(120, 153, 6)])
    ghost = fv.Track(3, 0, [(i, face(500, 200, 90, 120, 0.5)) for i in (198, 201)])
    return fv.Analysis(640, 360, fps, total / fps, total, (640, 360), [sure, weak], [(ghost, "von MediaPipe nicht bestätigt")], 40, 100, 0, [])


def test_analysis_survives_json_and_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    an = analysis()
    again = br.analysis_from_dict(br.analysis_to_dict(an))
    assert again.total_frames == 300 and len(again.tracks) == 2 and again.dropped[0][1].startswith("von MediaPipe")
    for a, b in zip(again.tracks[0].items[0][1].landmarks, an.tracks[0].items[0][1].landmarks):
        assert a == pytest.approx(b, abs=0.06)                    # coordinates are stored with one decimal
    con = connect()
    br.save_analysis(con, 7, an, {"preset": "eyebar"})
    got, opts, rendered = br.load_analysis(con, 7)
    assert opts == {"preset": "eyebar"} and rendered == 0 and got.fps == 30.0
    with pytest.raises(LookupError):
        br.load_analysis(con, 8)


def test_timeline_marks_sure_unsure_dropped_and_nothing(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_VIDEO_STRIDE", "3")
    kinds = br.classify_frames(analysis())
    assert kinds[60] == "sure" and kinds[135] == "unsure" and kinds[200] == "dropped" and kinds[270] == "none"
    segs = br.build_segments(analysis(), [])
    by_kind = {s["kind"] for s in segs}
    assert by_kind == {"sure", "unsure", "dropped", "none"}
    assert segs[0]["start"] == 0 and segs[-1]["end"] == 299
    assert all(a["end"] + 1 == b["start"] for a, b in zip(segs, segs[1:]))        # no gaps, no overlaps
    assert all(2 <= len(s["thumbs"]) or s["frames"] < 3 for s in segs)
    assert all(s["start"] <= f <= s["end"] for s in segs for f in s["thumbs"])


def test_segments_do_not_cross_a_cut():
    an = analysis()
    an.cuts = [250]
    segs = br.build_segments(an, [])
    assert any(s["start"] == 250 for s in segs)


def test_none_edit_removes_blur_face_edit_blurs_the_whole_face_rect_edit_follows_the_drawn_box():
    an = analysis()
    base = br.plan_frames(an, [])
    assert 60 in base.faces and 135 in base.faces
    e_none = br.Edit(1, 50, 70, "none")
    plan = br.plan_frames(an, [e_none])
    assert 60 not in plan.faces and 49 in plan.faces
    e_face = br.Edit(2, 198, 202, "face")                                 # only a dropped candidate is known there
    plan = br.plan_frames(an, [e_face])
    assert 200 in plan.full and plan.full[200][0].x == pytest.approx(500)
    e_rect = br.Edit(3, 10, 20, "rect", (0.1, 0.1, 0.2, 0.2), (0.3, 0.1, 0.4, 0.2))
    plan = br.plan_frames(an, [e_rect])
    assert plan.rects[10][0] == pytest.approx((0.1, 0.1, 0.2, 0.2)) and plan.rects[20][0] == pytest.approx((0.3, 0.1, 0.4, 0.2))
    assert plan.rects[15][0][0] == pytest.approx(0.2)                      # halfway
    keep = br.plan_frames(an, [br.Edit(4, 40, 45, "rect", (0.5, 0.5, 0.6, 0.6), None, replace=False)])
    assert 42 in keep.faces and 42 in keep.rects                         # drawn box ADDS to the automatic blur
    repl = br.plan_frames(an, [br.Edit(4, 40, 45, "rect", (0.5, 0.5, 0.6, 0.6))])
    assert 42 not in repl.faces and 42 in repl.rects                       # default: the drawn box REPLACES it
    later_wins = br.plan_frames(an, [br.Edit(1, 40, 45, "rect", (0.5, 0.5, 0.6, 0.6)), br.Edit(2, 40, 45, "none")])
    assert 42 not in later_wins.faces and 42 not in later_wins.rects


def test_edit_validation_and_pending_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    an = analysis()
    con = connect()
    br.save_analysis(con, 7, an, None)
    with pytest.raises(ValueError):
        br.add_edit(con, 7, an, frame_from=0, frame_to=10, mode="blur")
    with pytest.raises(ValueError):
        br.add_edit(con, 7, an, frame_from=0, frame_to=10, mode="rect")                       # no box
    with pytest.raises(ValueError):
        br.add_edit(con, 7, an, frame_from=0, frame_to=10, mode="rect", rect_start=[0.5, 0.5, 0.5, 0.5])
    with pytest.raises(ValueError):
        br.add_edit(con, 7, an, frame_from=295, frame_to=299, mode="face")                     # no face anywhere near
    with pytest.raises(ValueError):
        br.add_edit(con, 7, an, frame_from=900, frame_to=950, mode="none")                     # outside the video
    e = br.add_edit(con, 7, an, frame_from=280, frame_to=500, mode="none")
    assert (e.frame_from, e.frame_to) == (280, 299)                                           # clamped to the video
    _, _, rendered = br.load_analysis(con, 7)
    assert br.pending_edits(con, 7, rendered)
    br.mark_rendered(con, 7)
    _, _, rendered = br.load_analysis(con, 7)
    assert not br.pending_edits(con, 7, rendered)
    assert br.delete_edit(con, 7, e.id) and not br.delete_edit(con, 7, e.id)
    assert br.pending_edits(con, 7, rendered)                                                 # removing one is a change too


def test_shapes_for_the_overlay_are_polygons_in_frame_shares(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face")
    an = analysis()
    plan = br.plan_frames(an, [br.Edit(1, 40, 40, "rect", (0.1, 0.2, 0.3, 0.4))])
    polys = br.shapes_for_frame(an, plan, 40)
    assert polys == [[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]]]
    auto = br.shapes_for_frame(an, br.plan_frames(an, []), 60)
    assert auto and all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for poly in auto for x, y in poly)


# ---------------------------------------------------------------- end to end (needs ffmpeg)

@pytest.fixture
def video_copy(mount_env, monkeypatch):
    from phototag.blur_jobs import blur_one_file
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face")
    make_video(mount_env / "Album" / "v.mp4", seconds=3)
    con = connect()
    det = Scripted(lambda n, angles: {0: [face(200, 90, 60, 80)]})
    res = blur_one_file(con, "Album/v.mp4", detector=det)
    return mount_env, con, res


@needs_ffmpeg
def test_blur_job_keeps_the_analysis_for_review_and_rerender_applies_corrections(video_copy):
    from phototag.blur_jobs import rerender_one
    mount, con, res = video_copy
    an, _opts, _r = br.load_analysis(con, res["id"])
    assert an.total_frames > 50 and an.tracks
    dest = mount / "Album" / "v.ai-blurred.mp4"
    before = dest.read_bytes()
    br.add_edit(con, res["id"], an, frame_from=0, frame_to=an.total_frames - 1, mode="none")   # "there is no face here at all"
    out = rerender_one(con, res["id"])
    assert out["status"] == "blurred_review" and out["id"] == res["id"]
    after = dest.read_bytes()
    assert after != before
    row = con.execute("SELECT status, sha1 FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["status"] == "blurred_review"
    _, _, rendered = br.load_analysis(con, res["id"])
    assert not br.pending_edits(con, res["id"], rendered)
    assert (mount / "Album" / "v.mp4").is_file()                                   # the original is never touched


@needs_ffmpeg
def test_review_api_roundtrip(video_copy, client):
    mount, con, res = video_copy
    cid = res["id"]
    rv = client.get(f"/api/blur/{cid}/review")
    assert rv.status_code == 200 and rv.json["segments"] and rv.json["pending"] is False
    assert rv.headers["Cache-Control"] == "private, no-store"
    thumb = rv.json["segments"][0]["thumbs"][0]
    img = client.get(f"/api/blur/{cid}/frame/{thumb['frame']}?w=320")
    assert img.status_code == 200 and img.data[:2] == b"\xff\xd8"
    add = client.post(f"/api/blur/{cid}/edits", json={"frame_from": 5, "frame_to": 20, "mode": "rect",
                                                      "rect_start": [0.1, 0.1, 0.3, 0.3], "rect_end": [0.4, 0.1, 0.6, 0.3]})
    assert add.status_code == 200 and add.json["edit"]["mode"] == "rect"
    assert client.get(f"/api/blur/{cid}/review").json["pending"] is True
    assert client.post(f"/api/blur/{cid}/edits", json={"frame_from": 1, "frame_to": 2, "mode": "kaputt"}).status_code == 400
    rr = client.post(f"/api/blur/{cid}/rerender")
    assert rr.status_code == 200 and rr.json["job"]["status"] == "pending" and client.spawned
    assert client.post(f"/api/blur/{cid}/rerender").status_code == 409            # one job at a time
    assert client.delete(f"/api/blur/{cid}/edits/{add.json['edit']['id']}").json["ok"] is True
    assert client.delete(f"/api/blur/{cid}/edits/9999").status_code == 404
    assert client.get("/api/blur/99999/review").status_code in (400, 404)


@needs_ffmpeg
def test_rerender_runs_through_the_job_runner(video_copy):
    from phototag import blur_jobs
    mount, con, res = video_copy
    job_id = blur_jobs.create_rerender_job(con, res["id"])
    blur_jobs.run_blur_job(con, job_id)
    job = blur_jobs.get_blur_job(con, job_id)
    assert job["status"] == "done" and job["result"]["rerender"] is True and job["result"]["id"] == res["id"]


def test_old_copies_without_analysis_have_no_review(mount_env, client):
    from phototag.blur_jobs import blur_one_file
    from test_faceblur import FakeDetector
    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    con.execute("DELETE FROM blur_photo WHERE image_id=?", (res["id"],))        # a copy made before the review feature
    con.commit()
    r = client.get(f"/api/blur/{res['id']}/review")
    assert r.status_code == 404 and "Analyse" in r.json["error"]
    assert client.get(f"/api/blur/{res['id']}/photo").status_code == 404


def test_rejecting_a_copy_removes_its_review_data(video_copy):
    from phototag.blur_jobs import reject_blur
    mount, con, res = video_copy
    reject_blur(con, res["id"])
    with pytest.raises(LookupError):
        br.load_analysis(con, res["id"])


def test_admin_ui_has_the_review_overlay_and_uses_only_existing_endpoints():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "phototag/static/admin"
    js, css = (root / "admin.js").read_text(encoding="utf-8"), (root / "admin.css").read_text(encoding="utf-8")
    for needle in ("openBlurReview", "/review", "/edits", "/rerender", "/frame/", "Prüfen / korrigieren",
                   "Kein Gesicht — Blur entfernen", "Ganzes Gesicht blurren", "Rechteck zeichnen", "pointerdown"):
        assert needle in js, needle
    assert ".br-overlay" in css and ".br-seg" in css and "touch-action: none" in css
    assert "innerHTML" not in js[js.index("async function openBlurReview"):js.index("After a page reload the running blur job")]   # file names etc. only via textContent
