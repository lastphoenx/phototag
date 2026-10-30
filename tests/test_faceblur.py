"""Face-blur: naming, image/video pipeline, jobs, review, scan safety, admin API."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

from phototag import faceblur as fb  # noqa: E402
from phototag.cli import cmd_scan, connect  # noqa: E402


class FakeDetector:
    """Always finds one face at a fixed place (coordinates of the passed image)."""

    def __init__(self, boxes=None):
        self.boxes = boxes if boxes is not None else [fb.FaceBox(40, 30, 40, 50, 0.99)]

    def detect(self, bgr, *, max_side=None):
        return list(self.boxes)


def _noise_image(path: Path, size=(160, 120)) -> None:
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(arr).save(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- naming

def test_blurred_filename_variants():
    assert fb.blurred_filename("IMG_1.jpg") == "IMG_1.ai-blurred.jpg"
    assert fb.blurred_filename("a.PNG") == "a.ai-blurred.png"
    assert fb.blurred_filename("clip.MOV") == "clip.ai-blurred.mp4"  # re-encoded -> mp4
    assert fb.blurred_filename("clip.mkv") == "clip.ai-blurred.mp4"


def test_blurred_filename_rejects_copies_and_unknown_types():
    with pytest.raises(fb.FaceBlurError):
        fb.blurred_filename("IMG_1.ai-blurred.jpg")
    with pytest.raises(fb.FaceBlurError):
        fb.blurred_filename("notes.txt")
    assert fb.is_blurred_name("x.ai-blurred-2.jpg")


def test_unique_destination_never_overwrites(tmp_path):
    (tmp_path / "a.ai-blurred.jpg").write_bytes(b"1")
    (tmp_path / "a.ai-blurred-2.jpg").write_bytes(b"2")
    assert fb.unique_destination(tmp_path, "a.ai-blurred.jpg").name == "a.ai-blurred-3.jpg"
    assert fb.unique_destination(tmp_path, "b.ai-blurred.jpg").name == "b.ai-blurred.jpg"


def test_suffix_is_configurable(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SUFFIX", "anon")
    assert fb.blurred_filename("x.jpg") == "x.anon.jpg"


# ---------------------------------------------------------------- blur math

def test_expand_box_clips_to_image():
    x0, y0, x1, y1 = fb.expand_box(fb.FaceBox(5, 5, 40, 40), 0.5, 100, 100)
    assert (x0, y0) == (0, 0) and x1 <= 100 and y1 <= 100 and x1 > 45


def _face_with_landmarks():
    lm = ((60, 45), (50, 45), (55, 55), (58, 62), (48, 62))
    return fb.FaceBox(40, 30, 40, 50, 0.99, landmarks=lm)


def test_boxes_for_face_tight_smaller_than_face():
    b = _face_with_landmarks()
    face = fb.boxes_for_face(b, "face", 0.4, 200, 200)[0]
    tight = fb.boxes_for_face(b, "tight", 0.4, 200, 200)[0]
    assert (tight[2] - tight[0]) * (tight[3] - tight[1]) < (face[2] - face[0]) * (face[3] - face[1])


def test_boxes_for_face_eyes_returns_two_regions():
    boxes = fb.boxes_for_face(_face_with_landmarks(), "eyes", 0.4, 200, 200)
    assert len(boxes) == 2


def test_boxes_for_face_eyes_mouth_ears_more_boxes_than_eyes():
    b = _face_with_landmarks()
    eyes = fb.boxes_for_face(b, "eyes", 0.4, 200, 200)
    all_parts = fb.boxes_for_face(b, "eyes_mouth_ears", 0.4, 200, 200)
    assert len(all_parts) > len(eyes)


def test_margin_scales_landmark_eye_boxes():
    b = _face_with_landmarks()
    small = fb.boxes_for_face(b, "eyes", 0.05, 200, 200)
    large = fb.boxes_for_face(b, "eyes", 0.8, 200, 200)
    area = lambda boxes: sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes)
    assert area(large) > area(small)


def test_strength_increases_obfuscation():
    rng = np.random.default_rng(3)
    weak = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    strong = weak.copy()
    fb.apply_blur(weak, [(20, 20, 60, 60)], "blur", strength=0.35)
    fb.apply_blur(strong, [(20, 20, 60, 60)], "blur", strength=3.5)
    assert strong[20:60, 20:60].std() < weak[20:60, 20:60].std()


@pytest.mark.parametrize("mode", ["blur", "pixel"])
def test_apply_blur_changes_only_the_box(mode):
    rng = np.random.default_rng(2)
    img = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
    orig = img.copy()
    fb.apply_blur(img, [(20, 20, 60, 60)], mode)
    assert not np.array_equal(img[20:60, 20:60], orig[20:60, 20:60])
    mask = np.ones((100, 100), bool)
    mask[20:60, 20:60] = False
    assert np.array_equal(img[mask], orig[mask])
    # detail must be mostly gone inside the box
    assert img[20:60, 20:60].std() < orig[20:60, 20:60].std() * 0.5


# ---------------------------------------------------------------- image pipeline

def test_blur_image_png_blurs_box_and_strips_metadata(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src)
    before = _sha(src)
    res = fb.blur_image_file(src, dst, detector=FakeDetector())
    assert res.faces == 1 and dst.is_file()
    assert _sha(src) == before  # original untouched
    a, b = np.asarray(Image.open(src).convert("RGB")), np.asarray(Image.open(dst).convert("RGB"))
    assert a.shape == b.shape
    assert not np.array_equal(a[30:80, 40:80], b[30:80, 40:80])
    assert np.array_equal(a[:4], b[:4])  # outside the expanded box: identical


def test_blur_image_jpeg_has_no_exif(tmp_path):
    src, dst = tmp_path / "a.jpg", tmp_path / "a.ai-blurred.jpg"
    im = Image.fromarray(np.full((120, 160, 3), 90, np.uint8))
    exif = Image.Exif()
    exif[0x010F] = "SomeCamera"
    exif[0x8825] = {1: "N"}  # GPS IFD
    im.save(src, exif=exif)
    assert len(Image.open(src).getexif()) > 0
    fb.blur_image_file(src, dst, detector=FakeDetector())
    assert len(Image.open(dst).getexif()) == 0


def test_no_face_writes_no_file(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src)
    with pytest.raises(fb.NoFacesFound):
        fb.blur_image_file(src, dst, detector=FakeDetector(boxes=[]))
    assert not dst.exists()
    fb.blur_image_file(src, dst, detector=FakeDetector(boxes=[]), allow_empty=True)
    assert dst.exists()


def test_rejects_animated_and_corrupt_images(tmp_path):
    corrupt = tmp_path / "x.jpg"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(fb.FaceBlurError):
        fb.blur_image_file(corrupt, tmp_path / "o.jpg", detector=FakeDetector())
    anim = tmp_path / "anim.webp"
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "blue")]
    frames[0].save(anim, save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(fb.FaceBlurError):
        fb.blur_image_file(anim, tmp_path / "o.webp", detector=FakeDetector())


def test_alpha_channel_is_kept(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    arr = np.full((120, 160, 4), 200, np.uint8)
    arr[:, :20, 3] = 0
    Image.fromarray(arr, "RGBA").save(src)
    fb.blur_image_file(src, dst, detector=FakeDetector())
    out = Image.open(dst)
    assert out.mode == "RGBA" and np.asarray(out)[0, 0, 3] == 0


# ---------------------------------------------------------------- video pipeline

pytestmark_ffmpeg = pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0, reason="ffmpeg fehlt"
)


@pytestmark_ffmpeg
def test_blur_video_keeps_audio_duration_and_blurs_every_frame(tmp_path):
    src, dst = tmp_path / "c.mp4", tmp_path / "c.ai-blurred.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(src)], check=True)
    res = fb.blur_video_file(src, dst, detector=FakeDetector())
    assert res.kind == "video" and res.frames == 20 and res.faces == 1
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(dst)],
        capture_output=True, text=True).stdout.split()
    assert "video" in streams and "audio" in streams
    cap_o, cap_b = cv2.VideoCapture(str(src)), cv2.VideoCapture(str(dst))
    n = 0
    while True:
        ok1, f1 = cap_o.read()
        ok2, f2 = cap_b.read()
        if not (ok1 and ok2):
            break
        n += 1
        # inside the (expanded) face box the testsrc detail must be gone
        assert f2[30:80, 40:80].std() < f1[30:80, 40:80].std() * 0.8
    assert n == 20


@pytestmark_ffmpeg
def test_video_without_faces_writes_nothing(tmp_path):
    src, dst = tmp_path / "c.mp4", tmp_path / "c.ai-blurred.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    with pytest.raises(fb.NoFacesFound):
        fb.blur_video_file(src, dst, detector=FakeDetector(boxes=[]))
    assert not dst.exists()


# ---------------------------------------------------------------- jobs, DB, review

@pytest.fixture
def mount_env(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "Album").mkdir(parents=True)
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MODEL", str(tmp_path / "model.onnx"))
    monkeypatch.setenv("PHOTOTAG_TELEGRAM_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("PHOTOTAG_INSTALL_DIR", str(tmp_path))
    _noise_image(mount / "Album" / "a.png")
    return mount


def test_blur_one_file_creates_copy_row_and_keeps_original(mount_env):
    from phototag.blur_jobs import blur_one_file

    src = mount_env / "Album" / "a.png"
    before = _sha(src)
    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    dest = mount_env / "Album" / "a.ai-blurred.png"
    assert dest.is_file() and _sha(src) == before
    assert res["nc_path"] == "Album/a.ai-blurred.png" and res["status"] == "blurred_review"
    row = con.execute("SELECT * FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["status"] == "blurred_review" and row["folder"] == "Album" and row["kind"] == "image"
    assert row["derived_from"] == res["source_id"] and row["blur_faces"] == 1 and row["thumb"]
    orig = con.execute("SELECT * FROM images WHERE id=?", (res["source_id"],)).fetchone()
    assert orig["status"] == "new" and orig["derived_from"] is None
    # a second run never overwrites the first copy
    res2 = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    assert res2["nc_path"] == "Album/a.ai-blurred-2.png"


def test_blur_copy_refused_for_copies_and_missing_files(mount_env):
    from phototag.blur_jobs import blur_one_file

    con = connect()
    blur_one_file(con, "Album/a.png", detector=FakeDetector())
    with pytest.raises(fb.FaceBlurError):
        blur_one_file(con, "Album/a.ai-blurred.png", detector=FakeDetector())
    with pytest.raises(FileNotFoundError):
        blur_one_file(con, "Album/missing.png", detector=FakeDetector())
    with pytest.raises(ValueError):  # path traversal stays inside the mount
        blur_one_file(con, "../outside.png", detector=FakeDetector())


def test_approve_and_reject(mount_env):
    from phototag.blur_jobs import approve_blur, blur_one_file, reject_blur

    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    assert approve_blur(con, res["id"])["status"] == "blurred_ok"
    with pytest.raises(ValueError):
        approve_blur(con, res["id"])  # only from blurred_review
    with pytest.raises(ValueError):
        reject_blur(con, res["source_id"])  # originals can never be deleted via this path
    out = reject_blur(con, res["id"])
    assert out["deleted"] == "Album/a.ai-blurred.png"
    assert not (mount_env / "Album" / "a.ai-blurred.png").exists()
    assert (mount_env / "Album" / "a.png").exists()
    assert con.execute("SELECT 1 FROM images WHERE id=?", (res["id"],)).fetchone() is None


def test_reject_refuses_rows_without_blur_suffix(mount_env):
    from phototag.blur_jobs import blur_one_file, reject_blur

    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    _noise_image(mount_env / "Album" / "precious.png")
    con.execute("UPDATE images SET path='Album/precious.png' WHERE id=?", (res["id"],))  # tampered row
    con.commit()
    with pytest.raises(ValueError):
        reject_blur(con, res["id"])
    assert (mount_env / "Album" / "precious.png").exists()


def test_scan_keeps_blur_status_when_file_changes(mount_env):
    from phototag.blur_jobs import blur_one_file

    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    cmd_scan(argparse.Namespace(root=str(mount_env)))
    st = con.execute("SELECT status FROM images WHERE id=?", (res["id"],)).fetchone()[0]
    assert st == "blurred_review"
    copy = mount_env / "Album" / "a.ai-blurred.png"
    os.utime(copy, (1_000_000, 1_000_000))
    copy.write_bytes(copy.read_bytes() + b"\0")
    cmd_scan(argparse.Namespace(root=str(mount_env)))
    row = con.execute("SELECT status, error FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["status"] == "blurred_review" and row["error"].startswith("faceblur:")


def test_batches_skip_blur_copies_but_explicit_pick_includes_them(mount_env):
    from phototag.blur_jobs import approve_blur, blur_one_file
    from phototag.tag_jobs import resolve_rows_for_spec

    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    approve_blur(con, res["id"])
    folder_job = resolve_rows_for_spec(con, {"folders": ["Album"], "recursive": True})
    assert [r["path"] for r in folder_job] == ["Album/a.png"]  # original only
    picked = resolve_rows_for_spec(con, {"nc_paths": ["Album/a.ai-blurred.png"]})
    assert [r["path"] for r in picked] == ["Album/a.ai-blurred.png"]
    night = con.execute("SELECT path FROM images WHERE status IN ('new','error')").fetchall()
    assert [r[0] for r in night] == ["Album/a.png"]


def test_job_lifecycle_and_single_job_rule(mount_env, monkeypatch):
    from phototag import blur_jobs

    con = connect()
    monkeypatch.setattr(blur_jobs, "FaceDetector", lambda *a, **k: FakeDetector())
    jid = blur_jobs.create_blur_job(con, "Album/a.png")
    assert blur_jobs.get_blur_job(con, jid)["status"] == "pending"
    with pytest.raises(blur_jobs.BlurBusy):
        blur_jobs.create_blur_job(con, "Album/a.png")
    blur_jobs.run_blur_job(con, jid)
    job = blur_jobs.get_blur_job(con, jid)
    assert job["status"] == "done" and job["result"]["nc_path"] == "Album/a.ai-blurred.png"
    # finished -> next job allowed
    assert blur_jobs.create_blur_job(con, "Album/a.png")


def test_job_without_faces_and_stale_detection(mount_env, monkeypatch):
    import time

    from phototag import blur_jobs

    con = connect()
    monkeypatch.setattr(blur_jobs, "FaceDetector", lambda *a, **k: FakeDetector(boxes=[]))
    jid = blur_jobs.create_blur_job(con, "Album/a.png")
    blur_jobs.run_blur_job(con, jid)
    job = blur_jobs.get_blur_job(con, jid)
    assert job["status"] == "no_faces" and "Keine Gesichter" in job["error"]
    assert not (mount_env / "Album" / "a.ai-blurred.png").exists()
    # a crashed worker must not block forever
    jid2 = blur_jobs.create_blur_job(con, "Album/a.png")
    con.execute("UPDATE blur_jobs SET status='running', heartbeat_at=? WHERE id=?", (time.time() - 600, jid2))
    con.commit()
    assert blur_jobs.get_blur_job(con, jid2)["status"] == "stale"
    assert blur_jobs.create_blur_job(con, "Album/a.png")


# ---------------------------------------------------------------- admin API

@pytest.fixture
def client(mount_env, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_ADMIN_USER", "u")
    monkeypatch.setenv("PHOTOTAG_ADMIN_PASSWORD", "p")
    monkeypatch.setenv("PHOTOTAG_ADMIN_SESSION_SECRET", "s")
    pytest.importorskip("flask")
    from phototag.admin_app import create_app
    from phototag import blur_jobs

    spawned: list[str] = []
    monkeypatch.setattr(blur_jobs, "spawn_blur_job_subprocess", lambda jid: spawned.append(jid))
    monkeypatch.setattr(blur_jobs, "FaceDetector", lambda *a, **k: FakeDetector())
    app = create_app()
    c = app.test_client()
    assert c.post("/login", data={"username": "u", "password": "p"}).status_code == 302
    c.spawned = spawned
    return c


def test_api_requires_login_and_feature_flag(mount_env, monkeypatch, client):
    anon = client.application.test_client()
    assert anon.post("/api/blur/jobs", json={"nc_path": "Album/a.png"}).status_code == 401
    monkeypatch.delenv("PHOTOTAG_FACEBLUR_MODEL")
    r = client.post("/api/blur/jobs", json={"nc_path": "Album/a.png"})
    assert r.status_code == 503


def test_api_full_flow(client, mount_env):
    from phototag import blur_jobs

    r = client.post("/api/blur/jobs", json={"nc_path": "Album/a.png"})
    assert r.status_code == 200 and r.json["job"]["status"] == "pending"
    jid = r.json["job"]["id"]
    assert client.spawned == [jid]
    assert client.post("/api/blur/jobs", json={"nc_path": "Album/a.png"}).status_code == 409
    blur_jobs.run_blur_job(connect(), jid)
    job = client.get(f"/api/blur/jobs/{jid}").json["job"]
    assert job["status"] == "done"
    new_id = job["result"]["id"]

    listing = client.get("/api/browse?folder=Album&filter=blurred").json
    assert [i["nc_path"] for i in listing["items"]] == ["Album/a.ai-blurred.png"]
    assert listing["items"][0]["is_blur_copy"] and listing["items"][0]["status"] == "blurred_review"
    everything = {i["nc_path"]: i for i in client.get("/api/browse?folder=Album&filter=all").json["items"]}
    assert everything["Album/a.png"]["blur_copies"][0]["nc_path"] == "Album/a.ai-blurred.png"

    assert client.post(f"/api/blur/{new_id}/approve").json["status"] == "blurred_ok"
    assert client.post(f"/api/blur/{new_id}/approve").status_code == 400
    assert client.post(f"/api/blur/{new_id}/reject").json["ok"] is True
    assert not (mount_env / "Album" / "a.ai-blurred.png").exists()


def test_api_input_errors(client):
    assert client.post("/api/blur/jobs", json={}).status_code == 400
    assert client.post("/api/blur/jobs", json={"nc_path": "Album/nope.png"}).status_code == 404
    assert client.post("/api/blur/jobs", json={"nc_path": "Album/x.ai-blurred.png"}).status_code in (400, 404)
    assert client.get("/api/blur/jobs/doesnotexist").status_code == 404


# ---------------------------------------------------------------- optional: real detector

MODEL = os.getenv("PHOTOTAG_FACEBLUR_MODEL", "")


@pytest.mark.skipif(not MODEL or not Path(MODEL).is_file(), reason="PHOTOTAG_FACEBLUR_MODEL (YuNet) nicht gesetzt")
def test_real_detector_removes_the_face(tmp_path):
    skimage_data = pytest.importorskip("skimage.data")
    src, dst = tmp_path / "f.png", tmp_path / "f.ai-blurred.png"
    Image.fromarray(skimage_data.astronaut()).save(src)
    det = fb.FaceDetector()
    res = fb.blur_image_file(src, dst, detector=det)
    assert res.faces >= 1
    after = cv2.imread(str(dst))
    assert det.detect(after) == []


# ---------------------------------------------------------------- 0.6.2: visibility and checks

def test_env_inline_comments_are_stripped(tmp_path, monkeypatch):
    from phototag.config import read_phototag_env

    f = tmp_path / "phototag.env"
    f.write_text('PHOTOTAG_FACEBLUR_REGION=eyes   # nur Augen\nPHOTOTAG_FACEBLUR_MARGIN="0.3"\n#PHOTOTAG_FACEBLUR_MODE=pixel\n', encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_ENV", str(f))
    env = read_phototag_env()
    assert env == {"PHOTOTAG_FACEBLUR_REGION": "eyes", "PHOTOTAG_FACEBLUR_MARGIN": "0.3"}


def test_invalid_settings_are_reported_not_silent(monkeypatch):
    from phototag.config import faceblur_region, faceblur_settings_summary

    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "eyes # kommentar")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MARGIN", "0.4 # box")
    assert faceblur_region() == "eyes"  # the comment text is cut off, the value still works
    summary = faceblur_settings_summary()
    assert "WARNUNG" in summary and "MARGIN" in summary and "REGION" not in summary
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "augen")  # a real typo is reported
    assert faceblur_region() == "face" and "REGION" in faceblur_settings_summary()


def test_big_face_warns_about_large_blur_area():
    boxes = [fb.FaceBox(300, 100, 600, 700, 0.9)]  # close-up face in a 1280x720 frame
    share = fb.blur_area_share(boxes, 1280, 720, margin=0.4, region="face")
    assert share > 0.25
    notes = fb.quality_notes(share, 0, region="face")
    assert any("WARNUNG" in n and "Bildes" in n for n in notes)
    small = fb.blur_area_share([fb.FaceBox(100, 100, 60, 70, 0.9)], 1280, 720, margin=0.4, region="face")
    assert not any("WARNUNG" in n for n in fb.quality_notes(small, 0, region="face"))


def test_leak_check_flags_faces_left_in_the_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face")
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src)

    class Stubborn(FakeDetector):  # still "finds" a face in the blurred output
        pass

    res = fb.blur_image_file(src, dst, detector=Stubborn())
    assert any("noch 1 Gesicht" in n for n in res.notes)
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "eyes")
    res = fb.blur_image_file(src, dst, detector=Stubborn())
    assert not any("noch" in n and "Gesicht" in n for n in res.notes)  # eyes mode: no leak claim


def test_blur_info_is_stored_and_listed(mount_env):
    from phototag.admin_mount import list_mount_browse_items
    from phototag.blur_jobs import blur_one_file

    con = connect()
    res = blur_one_file(con, "Album/a.png", detector=FakeDetector())
    row = con.execute("SELECT error FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["error"].startswith("faceblur:faces=1;") and "region=" in row["error"]
    items, _ = list_mount_browse_items(con, "Album", "blurred", set())
    assert "region=" in items[0]["blur_info"]


# ---------------------------------------------------------------- 0.6.3: eye bar

def _landmarks(cx=100.0, cy=80.0, eye_dist=40.0, tilt_deg=0.0):
    import math

    a = math.radians(tilt_deg)
    ux, uy = math.cos(a), math.sin(a)
    re = (cx - ux * eye_dist / 2, cy - uy * eye_dist / 2)
    le = (cx + ux * eye_dist / 2, cy + uy * eye_dist / 2)
    return fb.FaceBox(cx - 45, cy - 45, 90, 120, 0.99, landmarks=(re, le, (cx, cy + 25), (cx - 15, cy + 50), (cx + 15, cy + 50)))


def _inside(poly, p):
    pts = np.array(poly, dtype=np.int32)
    return cv2.pointPolygonTest(pts, (float(p[0]), float(p[1])), False) >= 0


def test_eyebar_covers_both_eyes_brows_bags_and_reaches_the_face_edges():
    b = _landmarks()
    x0, y0, x1, y1, poly = fb.eye_bar(b, 0.15, 400, 400)
    for eye in b.landmarks[:2]:
        assert _inside(poly, eye)
    assert _inside(poly, (100, 80 - 40 * 0.5))    # brow height (0.5 eye distances above)
    assert _inside(poly, (100, 80 + 40 * 0.4))    # eye bag height (0.4 below)
    assert not _inside(poly, (100, 80 + 40 * 1.5))  # mouth/nose stay free
    assert min(p[0] for p in poly) <= b.x and max(p[0] for p in poly) >= b.x + b.w  # ear to ear


def test_eyebar_follows_head_tilt():
    flat = fb.eye_bar(_landmarks(tilt_deg=0), 0.15, 400, 400)[4]
    tilt = fb.eye_bar(_landmarks(tilt_deg=20), 0.15, 400, 400)[4]
    assert flat[0][1] == flat[1][1]            # upper edge level
    assert tilt[0][1] != tilt[1][1]            # upper edge tilted with the head
    b = _landmarks(tilt_deg=20)
    assert all(_inside(tilt, eye) for eye in b.landmarks[:2])


def test_eyebar_without_landmarks_falls_back_to_box_geometry():
    x0, y0, x1, y1, poly = fb.eye_bar(fb.FaceBox(50, 40, 100, 120, 0.9), 0.15, 300, 300)
    assert x1 - x0 >= 100 and y1 - y0 < 120


def test_bar_settings_change_the_bar(monkeypatch):
    base = fb.eye_bar(_landmarks(), 0.0, 400, 400)[4]
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_BAR_UP", "0.9")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_BAR_SIDE", "1.4")
    big = fb.eye_bar(_landmarks(), 0.0, 400, 400)[4]
    assert min(p[1] for p in big) < min(p[1] for p in base)
    assert max(p[0] for p in big) > max(p[0] for p in base)


@pytest.mark.parametrize("mode", ["blur", "pixel"])
def test_apply_blur_polygon_changes_only_inside_the_polygon(mode):
    rng = np.random.default_rng(3)
    img = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    orig = img.copy()
    poly = [(40, 60), (160, 90), (160, 130), (40, 100)]  # tilted bar
    box = (40, 60, 161, 131, poly)
    fb.apply_blur(img, [box], mode)
    mask = np.zeros((200, 200), np.uint8)
    cv2.fillConvexPoly(mask, np.array(poly, np.int32), 1)
    assert np.array_equal(img[mask == 0], orig[mask == 0])
    assert not np.array_equal(img[mask == 1], orig[mask == 1])
    assert img[mask == 1].std() < orig[mask == 1].std() * 0.6


def test_eyebar_pipeline_blurs_only_the_bar_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "eyebar")
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src, (200, 160))
    b = _landmarks(cx=100, cy=70, eye_dist=36)
    res = fb.blur_image_file(src, dst, detector=FakeDetector(boxes=[b]))
    a, o = np.asarray(Image.open(src).convert("RGB")), np.asarray(Image.open(dst).convert("RGB"))
    assert not np.array_equal(a[60:80, 80:120], o[60:80, 80:120])    # eyes covered
    assert np.array_equal(a[110:150, 60:140], o[110:150, 60:140])    # mouth/chin untouched
    assert "bar_up=" in res.notes[0] and any(n.startswith("Blur-Fläche") for n in res.notes)
    assert any("übrige Gesichtsteile" in n for n in res.notes)


# ---------------------------------------------------------------- 0.6.4: composable parts

def test_region_list_parsing_and_aliases():
    from phototag.config import parse_region_names

    assert parse_region_names("eyebar, nose+mouth") == (["eyebar", "nose", "mouth"], [])
    assert parse_region_names("eyes_mouth_ears")[0] == ["eyes", "mouth", "ears"]
    assert parse_region_names("EyeBar,eyebar")[0] == ["eyebar"]  # case-insensitive, deduplicated
    assert parse_region_names("nose,toes") == (["nose"], ["toes"])
    assert parse_region_names("") == ([], [])


def test_unknown_parts_are_reported_not_silent(monkeypatch):
    from phototag.config import faceblur_regions, faceblur_settings_summary

    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "nose,toes")
    assert faceblur_regions() == ["nose"]
    assert "toes" in faceblur_settings_summary() and "WARNUNG" in faceblur_settings_summary()
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "toes")
    assert faceblur_regions() == ["face"]  # nothing valid -> safe default
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SCALE_NOSE", "gross")
    assert "SCALE_NOSE" in fb_summary()


def fb_summary():
    from phototag.config import faceblur_settings_summary

    return faceblur_settings_summary()


def _poly(shape):
    return shape[4] if len(shape) > 4 else [(shape[0], shape[1]), (shape[2], shape[1]), (shape[2], shape[3]), (shape[0], shape[3])]


def _center_of(shape):
    pts = _poly(shape)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


@pytest.mark.parametrize("part", ["nose", "mouth", "ears", "chin", "forehead"])
def test_each_part_sits_where_it_belongs(part):
    b = _landmarks(cx=200, cy=160, eye_dist=60)
    shapes = fb.boxes_for_face(b, part, 0.0, 600, 600)
    assert len(shapes) == (2 if part == "ears" else 1)
    nose, mouth_mid = b.landmarks[2], ((b.landmarks[3][0] + b.landmarks[4][0]) / 2, (b.landmarks[3][1] + b.landmarks[4][1]) / 2)
    cx, cy = _center_of(shapes[0])
    if part == "nose":
        assert _inside(_poly(shapes[0]), nose) and not _inside(_poly(shapes[0]), mouth_mid)
    if part == "mouth":
        assert _inside(_poly(shapes[0]), mouth_mid) and not _inside(_poly(shapes[0]), nose)
    if part == "chin":
        assert cy > mouth_mid[1] and not _inside(_poly(shapes[0]), nose)
    if part == "forehead":
        assert cy < 160 and not _inside(_poly(shapes[0]), b.landmarks[0])
    if part == "ears":
        xs = sorted(_center_of(s)[0] for s in shapes)
        assert xs[0] < b.x + 5 and xs[1] > b.x + b.w - 5  # left/right edge of the face


def test_parts_combine_and_leave_the_rest_free():
    b = _landmarks(cx=200, cy=160, eye_dist=60)
    combo = fb.boxes_for_face(b, "eyebar,nose,mouth", 0.1, 600, 600)
    assert len(combo) == 3
    mask = np.zeros((600, 600), np.uint8)
    for s in combo:
        cv2.fillConvexPoly(mask, np.array(_poly(s), np.int32), 1)
    assert mask[160, 200] == 1 and mask[185, 200] == 1 and mask[210, 200] == 1   # eyes, nose, mouth covered
    assert mask[260, 200] == 0                                                   # chin free
    assert mask[160 - 80, 200] == 0                                              # forehead free


def test_tilted_parts_follow_the_head():
    flat = fb.boxes_for_face(_landmarks(tilt_deg=0), "mouth", 0.1, 400, 400)[0][4]
    tilt = fb.boxes_for_face(_landmarks(tilt_deg=25), "mouth", 0.1, 400, 400)[0][4]
    assert flat[0][1] == flat[1][1] and tilt[0][1] != tilt[1][1]


def test_scale_changes_size_per_part(monkeypatch):
    b = _landmarks(eye_dist=60)
    small = fb.boxes_for_face(b, "nose", 0.0, 600, 600)[0]
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SCALE_NOSE", "2")
    big = fb.boxes_for_face(b, "nose", 0.0, 600, 600)[0]
    assert (big[2] - big[0]) > (small[2] - small[0]) * 1.5


def test_parts_work_without_landmarks():
    b = fb.FaceBox(100, 100, 120, 150, 0.9)
    for part in ("nose", "mouth", "ears", "chin", "forehead", "eyebar", "eyes"):
        assert fb.boxes_for_face(b, part, 0.1, 400, 400)


def test_parts_pipeline_and_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "eyebar,nose")
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src, (240, 200))
    b = _landmarks(cx=120, cy=80, eye_dist=40)
    res = fb.blur_image_file(src, dst, detector=FakeDetector(boxes=[b]))
    assert "region=eyebar+nose" in res.notes[0]
    assert any("übrige Gesichtsteile" in n for n in res.notes)
    assert not any("noch" in n and "Gesicht" in n for n in res.notes)  # no leak claim for partial regions
    from phototag.faceblur import count_leaks

    assert count_leaks(FakeDetector(), [np.zeros((50, 50, 3), np.uint8)]) == 0
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face,tight")
    assert count_leaks(FakeDetector(), [np.zeros((50, 50, 3), np.uint8)]) == 1  # box regions are checked


# ---------------------------------------------------------------- 0.6.5: fill between parts

def _shapes_mask(shapes, width=600, height=600):
    mask = np.zeros((height, width), np.uint8)
    for s in shapes:
        x0, y0, x1, y1 = s[:4]
        if len(s) > 4 and hasattr(s[4], "shape"):
            sub = s[4][: y1 - y0, : x1 - x0] > 0
            mask[y0:y0 + sub.shape[0], x0:x0 + sub.shape[1]][sub] = 1
        elif len(s) > 4:
            cv2.fillConvexPoly(mask, np.array(s[4], np.int32), 1)
        else:
            mask[y0:y1, x0:x1] = 1
    return mask


def _prop(cx=300.0, cy=200.0, d=80.0, tilt_deg=0.0):
    """Face with realistic proportions (nose 0.65 d, mouth 1.25 d below the eye line)."""
    import math

    a = math.radians(tilt_deg)
    ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)

    def at(along, down):
        return (cx + ux * along + nx * down, cy + uy * along + ny * down)

    lm = (at(-d / 2, 0), at(d / 2, 0), at(0, 0.65 * d), at(-0.35 * d, 1.25 * d), at(0.35 * d, 1.25 * d))
    return fb.FaceBox(cx - 0.85 * d, cy - 0.9 * d, 1.7 * d, 2.4 * d, 0.99, landmarks=lm), at


def test_fill_none_keeps_separate_bars_and_gaps():
    b, at = _prop()
    m = _shapes_mask(fb.boxes_for_face(b, "eyebar,mouth", 0.0, 600, 600))
    px = lambda p: (int(p[1]), int(p[0]))  # noqa: E731  (row, col)
    assert m[px(at(0, 0))] == 1 and m[px(at(0, 1.25 * 80))] == 1
    assert m[px(at(0, 0.7 * 80))] == 0          # gap between eye bar and mouth


def test_fill_bridge_closes_the_gap_but_keeps_the_cheeks(monkeypatch):
    b, at = _prop()
    plain = _shapes_mask(fb.boxes_for_face(b, "eyebar,mouth", 0.0, 600, 600))
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "bridge")
    shapes = fb.boxes_for_face(b, "eyebar,mouth", 0.0, 600, 600)
    assert len(shapes) == 1
    m = _shapes_mask(shapes)
    px = lambda p: (int(p[1]), int(p[0]))  # noqa: E731
    assert m[px(at(0, 0.7 * 80))] == 1          # centre column closed
    assert m[px(at(0.8 * 80, 0.7 * 80))] == 0   # cheek beside the mouth stays visible
    assert (m >= plain).all()                   # never uncovers what was covered before


def test_fill_hull_covers_everything_between(monkeypatch):
    b, at = _prop()
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "hull")
    shapes = fb.boxes_for_face(b, "eyebar,nose,mouth", 0.0, 600, 600)
    assert len(shapes) == 1
    m = _shapes_mask(shapes)
    px = lambda p: (int(p[1]), int(p[0]))  # noqa: E731
    assert m[px(at(0.5 * 80, 0.7 * 80))] == 1   # cheek inside the convex hull
    assert m[px(at(0, 2.0 * 80))] == 0          # chin stays free


def test_fill_follows_head_tilt(monkeypatch):
    b, at = _prop(tilt_deg=25)
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "bridge")
    m = _shapes_mask(fb.boxes_for_face(b, "eyebar,mouth", 0.0, 600, 600))
    px = lambda p: (int(p[1]), int(p[0]))  # noqa: E731
    assert m[px(at(0, 0.7 * 80))] == 1          # gap closed along the tilted face axis
    assert m[px(at(0.8 * 80, 0.7 * 80))] == 0


def test_fill_ignored_for_box_regions_and_single_parts(monkeypatch):
    b = _landmarks()
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "hull")
    assert len(fb.boxes_for_face(b, "face", 0.1, 400, 400)) == 1
    assert len(fb.boxes_for_face(b, "ears", 0.1, 400, 400)) == 1  # a single shape, nothing to join
    assert len(fb.boxes_for_face(b, "tight,eyebar", 0.1, 400, 400)) == 2  # box regions skip filling


def test_fill_pipeline_blurs_only_inside_the_mask(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "eyebar,nose,mouth")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "bridge")
    src, dst = tmp_path / "a.png", tmp_path / "a.ai-blurred.png"
    _noise_image(src, (300, 260))
    b = _landmarks(cx=150, cy=90, eye_dist=50)
    res = fb.blur_image_file(src, dst, detector=FakeDetector(boxes=[b]))
    a, o = np.asarray(Image.open(src).convert("RGB")), np.asarray(Image.open(dst).convert("RGB"))
    assert not np.array_equal(a[90:150, 130:170], o[90:150, 130:170])   # column from eyes down to the mouth
    assert np.array_equal(a[225:, :], o[225:, :])                       # far below: untouched
    assert "fill=bridge" in res.notes[0]


def test_fill_and_scale_settings_are_validated(monkeypatch):
    from phototag.config import faceblur_fill, faceblur_part_wh, faceblur_settings_summary

    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "alles   # kommentar")
    assert faceblur_fill() == "none" and "FILL" in faceblur_settings_summary()
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_FILL", "bridge   # kommentar")
    assert faceblur_fill() == "bridge"
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SCALE_NOSE_W", "1.5")
    assert faceblur_part_wh("nose") == (1.5, 1.0)
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SCALE_NOSE_H", "breit")
    assert "SCALE_NOSE_H" in faceblur_settings_summary()


def test_width_height_factors_change_the_shape(monkeypatch):
    b = _landmarks(eye_dist=60)
    base = fb.boxes_for_face(b, "mouth", 0.0, 600, 600)[0]
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_SCALE_MOUTH_W", "1.6")
    wide = fb.boxes_for_face(b, "mouth", 0.0, 600, 600)[0]
    assert (wide[2] - wide[0]) > (base[2] - base[0]) * 1.3 and (wide[3] - wide[1]) == (base[3] - base[1])
