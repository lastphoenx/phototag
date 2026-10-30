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
