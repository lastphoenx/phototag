"""Video face blur: tracking, ghost/false-positive filtering, interpolation, staging, UI hooks."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from phototag import faceblur as fb  # noqa: E402
from phototag import faceblur_video as fv  # noqa: E402
from phototag.cli import connect  # noqa: E402

HAVE_FFMPEG = subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg fehlt")


def face(x, y, w=100, h=130, score=0.95, angle=0):
    cx = x + w / 2
    lm = ((cx - 0.23 * w, y + 0.38 * h), (cx + 0.23 * w, y + 0.38 * h), (cx, y + 0.58 * h),
          (cx - 0.18 * w, y + 0.78 * h), (cx + 0.18 * w, y + 0.78 * h))
    return fb.FaceBox(x, y, w, h, score, landmarks=lm, angle=angle)


def run_tracker(samples, **kw):
    """samples: list of dict angle -> [FaceBox]; returns (kept, dropped)."""
    tr = fv.Tracker(**kw)
    for i, passes in enumerate(samples):
        tr.update(i * 2, passes)
    return tr.finish()


# ---------------------------------------------------------------- tracking and filtering

def test_one_face_moving_gives_one_track():
    samples = [{0: [face(100 + i * 3, 80)]} for i in range(30)]
    kept, dropped = run_tracker(samples)
    assert len(kept) == 1 and kept[0].hits == 30 and not dropped


def test_same_face_in_four_orientations_is_not_a_ghost():
    """YuNet finds the same face in all 4 views (0.95/0.87/0.87/0.82). Only ONE track may result."""
    samples = []
    for i in range(30):
        x = 100 + i * 4
        samples.append({
            0: [face(x, 80, 100, 130, 0.95, 0)],
            90: [face(x - 15, 100, 120, 105, 0.87, 90)],
            180: [face(x - 3, 90, 106, 104, 0.87, 180)],
            270: [face(x + 10, 120, 95, 80, 0.82, 270)],
        })
    kept, dropped = run_tracker(samples)
    assert len(kept) == 1, [(t.id, t.angle, t.hits) for t in kept]
    assert kept[0].angle == 0 and kept[0].hits == 30


def test_one_off_weak_detection_is_dropped_strong_one_is_kept():
    samples = [{0: [face(100, 80)]} for _ in range(10)]
    samples[4][0].append(face(400, 300, 90, 120, 0.50))      # weak, once
    samples[6][0].append(face(600, 50, 90, 120, 0.92))       # strong, once
    kept, dropped = run_tracker(samples)
    assert len(kept) == 2
    assert any("Treffer" in why for _, why in dropped)


def test_persistent_but_weak_track_is_dropped():
    """A body pattern that keeps looking vaguely like a face (mean 0.45) is not a face."""
    samples = [{0: [face(100, 80), face(500, 300, 200, 260, 0.45)]} for _ in range(20)]
    kept, dropped = run_tracker(samples)
    assert len(kept) == 1 and kept[0].mean_score > 0.9
    assert len(dropped) == 0     # below the per-detection floor it never even starts a track


def test_single_orientation_track_needs_a_second_view_unless_very_sure(monkeypatch):
    def with_ghost(score, corroborated):
        samples = []
        for _ in range(20):
            passes = {0: [face(100, 80, 100, 130, 0.95, 0)], 180: [face(500, 300, 200, 260, score, 180)]}
            if corroborated:
                passes[90] = [face(505, 305, 190, 250, 0.78, 90)]
            samples.append(passes)
        return samples

    kept, dropped = run_tracker(with_ghost(0.80, False))
    assert len(kept) == 1 and any("einer Drehung" in why for _, why in dropped)   # false face on a torso
    kept, _ = run_tracker(with_ghost(0.80, True))
    assert len(kept) == 2                                                          # seen in 2 views -> real
    kept, _ = run_tracker(with_ghost(0.93, False))
    assert len(kept) == 2                                                          # very sure on its own
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_CORROBORATE", "0")
    kept, _ = run_tracker(with_ghost(0.80, False))
    assert len(kept) == 2                                                          # switched off


def test_track_follows_the_best_orientation_without_stickiness():
    """A turning head: the right view beats the wrong ones by only ~0.1, so the track must follow it."""
    in_0 = {0: [face(100, 80, 100, 130, 0.95, 0)], 90: [face(98, 82, 100, 125, 0.84, 90)]}
    in_90 = {0: [face(100, 80, 100, 130, 0.84, 0)], 90: [face(98, 82, 100, 125, 0.94, 90)]}
    kept, _ = run_tracker([in_0] * 4 + [in_90] * 4)
    assert kept[0].angle == 90
    assert [f.angle for _, f in kept[0].items] == [0] * 4 + [90] * 4
    kept, _ = run_tracker([in_90] * 4 + [in_0] * 4)
    assert [f.angle for _, f in kept[0].items] == [90] * 4 + [0] * 4


def test_max_faces_keeps_the_best_tracks(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MAX_FACES", "1")
    samples = [{0: [face(100, 80, 100, 130, 0.95), face(700, 80, 100, 130, 0.70)]} for _ in range(10)]
    kept, dropped = run_tracker(samples)
    assert len(kept) == 1 and kept[0].mean_score > 0.9 and any("MAX_FACES" in why for _, why in dropped)


def test_dedupe_tracks_merges_two_tracks_on_one_face():
    a = fv.Track(1, 0, [(i * 2, face(100, 80, score=0.95)) for i in range(10)])
    b = fv.Track(2, 180, [(i * 2, face(104, 84, 96, 120, 0.80, 180)) for i in range(10)])
    kept, dropped = fv.dedupe_tracks([a, b])
    assert [t.id for t in kept] == [1] and "Duplikat" in dropped[0][1]


# ---------------------------------------------------------------- interpolation, smoothing

def test_interpolation_hold_and_gap_rules():
    t = fv.Track(1, 0, [(10, face(100, 100)), (14, face(140, 100)), (80, face(300, 100))])
    frames = fv.tracks_to_frames([t], 120, 30.0, gap_sec=1.0, hold_sec=0.3, smooth=1)
    assert frames[12][0].x == pytest.approx(120)             # halfway between sample 10 and 14
    assert 1 in frames and 9 in frames                       # hold 9 frames before the first sample
    assert 15 in frames                                      # hold after the end of the first segment
    assert 40 not in frames                                  # gap of 66 frames > 1 s: nothing invented
    assert 85 in frames and 89 in frames and 90 not in frames  # hold after the last detection (9 frames)
    assert max(frames) < 120


def test_frames_are_scaled_to_the_output_size():
    t = fv.Track(1, 0, [(0, face(100, 50, 40, 60))])
    frames = fv.tracks_to_frames([t], 5, 25.0, scale=0.5, smooth=1)
    f = frames[0][0]
    assert (f.x, f.y, f.w, f.h) == (50, 25, 20, 30) and f.landmarks[0][0] == pytest.approx(face(100, 50, 40, 60).landmarks[0][0] * 0.5)


def test_smoothing_calms_jitter_without_lagging():
    rng = np.random.default_rng(5)
    noisy = [(i * 2, face(100 + i * 2 + float(rng.normal(0, 6)), 80 + float(rng.normal(0, 6)))) for i in range(40)]
    sm = fv.smooth_items(noisy, 5)
    raw_err = np.std([f.x - (100 + i * 2) for i, (_, f) in enumerate(noisy)])
    sm_err = np.std([f.x - (100 + i * 2) for i, (_, f) in enumerate(sm)][2:-2])
    assert sm_err < raw_err * 0.6


def test_scale_never_collapses_for_squeezed_eye_distance():
    """Profile or bowed heads report eyes almost on top of each other; the parts must not shrink to slivers."""
    b = fb.FaceBox(100, 100, 120, 150, 0.9, landmarks=((158, 140), (166, 142), (162, 170), (150, 190), (172, 192)))
    bar = fb.boxes_for_face(b, "eyebar", 0.1, 600, 600)[0]
    assert (bar[3] - bar[1]) > 0.38 * 120 * 0.6          # at least a sensible height
    assert (bar[2] - bar[0]) >= 100


# ---------------------------------------------------------------- video integration (fake detector)

class Scripted:
    """detect_passes() answers from a function of the sample number."""

    def __init__(self, fn):
        self.fn, self.n = fn, 0

    def detect_passes(self, bgr, angles=(0,), *, max_side=None):
        out = self.fn(self.n, tuple(angles))
        self.n += 1
        return {k: out.get(k, []) for k in angles}

    def detect(self, bgr, *, max_side=None):
        return [f for faces in self.detect_passes(bgr, (0,)).values() for f in faces]


def make_video(path: Path, w=640, h=360, seconds=4, fps=25, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac", "-shortest"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True)


def probe(path: Path):
    return fb.probe_display(path)


@needs_ffmpeg
def test_video_pipeline_covers_gaps_caps_size_and_keeps_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_VIDEO_MAX_SIDE", "320")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_VIDEO_STRIDE", "2")
    src, dst = tmp_path / "c.mp4", tmp_path / "c.ai-blurred.mp4"
    make_video(src, seconds=6)   # 150 frames -> 75 samples at stride 2
    # face present in the first 60 % of the samples; the detector misses it for a short while (3 samples)
    det = Scripted(lambda n, angles: {0: [face(200 + n, 100, 60, 80)]} if (n < 30 or n >= 33) and n < 45 else {})
    res = fb.blur_video_file(src, dst, detector=det)
    assert res.kind == "video" and (res.width, res.height) == (320, 180)       # output capped to 320 px
    assert probe(dst)[:2] == (320, 180)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(dst)],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams
    assert res.faces == 1
    note = next(n for n in res.notes if "Frames mit Blur" in n)
    assert "verfolgt" in note
    assert any("WARNUNG" in n and "kein Gesicht" in n for n in res.notes)       # only ~60 % covered


@needs_ffmpeg
def test_video_without_a_trusted_face_writes_nothing(tmp_path):
    src, dst = tmp_path / "c.mp4", tmp_path / "c.ai-blurred.mp4"
    make_video(src, seconds=2)
    det = Scripted(lambda n, angles: {0: [face(100, 100, 50, 60, 0.45)]} if n == 3 else {})   # one weak hit
    with pytest.raises(fb.NoFacesFound):
        fb.blur_video_file(src, dst, detector=det)
    assert not dst.exists()


@needs_ffmpeg
def test_blur_is_applied_where_the_interpolated_face_is(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MARGIN", "0.1")
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_VIDEO_MAX_SIDE", "0")
    src, dst = tmp_path / "c.mp4", tmp_path / "c.ai-blurred.mp4"
    make_video(src, audio=False, seconds=3)
    det = Scripted(lambda n, angles: {0: [face(100 + 4 * n, 90, 80, 100)]})
    fb.blur_video_file(src, dst, detector=det)
    co, cb = cv2.VideoCapture(str(src)), cv2.VideoCapture(str(dst))
    for i in range(75):
        ok1, a = co.read()
        ok2, b = cb.read()
        if i == 40:                                                      # a frame BETWEEN two samples
            x = 100 + 2 * i
            inside = np.abs(a[100:180, x + 10:x + 70].astype(int) - b[100:180, x + 10:x + 70].astype(int)).mean()
            outside = np.abs(a[250:350, 20:80].astype(int) - b[250:350, 20:80].astype(int)).mean()
            assert inside > 8 and outside < 4, (inside, outside)
            break


# ---------------------------------------------------------------- staging, job flow, API

@needs_ffmpeg
def test_stage_local_copies_once_and_falls_back_on_a_full_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_TELEGRAM_TEMP_DIR", str(tmp_path / "tmp"))
    src = tmp_path / "v.mp4"
    make_video(src, seconds=1, audio=False)
    staged, was = fb.stage_local(src)
    assert was and staged != src and staged.read_bytes() == src.read_bytes() and staged.name.startswith("phototag-fb-in-")
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    again, was2 = fb.stage_local(src)
    assert not was2 and again == src


@pytest.fixture
def vid_env(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "Album").mkdir(parents=True)
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MODEL", str(tmp_path / "m.onnx"))
    monkeypatch.setenv("PHOTOTAG_TELEGRAM_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("PHOTOTAG_INSTALL_DIR", str(tmp_path))
    return mount


@needs_ffmpeg
def test_video_job_stages_locally_registers_from_the_local_copy_and_cleans_up(vid_env, tmp_path):
    from phototag.blur_jobs import blur_one_file
    import hashlib

    make_video(vid_env / "Album" / "v.mp4", seconds=2)
    con = connect()
    det = Scripted(lambda n, angles: {0: [face(200, 90, 60, 80)]})
    res = blur_one_file(con, "Album/v.mp4", detector=det)
    dest = vid_env / "Album" / "v.ai-blurred.mp4"
    assert dest.is_file() and res["status"] == "blurred_review"
    row = con.execute("SELECT * FROM images WHERE id=?", (res["id"],)).fetchone()
    assert row["sha1"] == hashlib.sha1(dest.read_bytes()).hexdigest() and row["thumb"]
    assert row["derived_from"] == res["source_id"]
    leftovers = list((tmp_path / "tmp").glob("phototag-fb-*")) if (tmp_path / "tmp").exists() else []
    assert leftovers == []                                   # staged source and temp output are gone


def test_active_job_endpoint_and_ui_hooks(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(tmp_path))
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MODEL", str(tmp_path / "m.onnx"))
    monkeypatch.setenv("PHOTOTAG_ADMIN_USER", "u")
    monkeypatch.setenv("PHOTOTAG_ADMIN_PASSWORD", "p")
    monkeypatch.setenv("PHOTOTAG_ADMIN_SESSION_SECRET", "s")
    from phototag.admin_app import create_app
    from phototag import blur_jobs

    client = create_app().test_client()
    client.post("/login", data={"username": "u", "password": "p"})
    assert client.get("/api/blur/jobs/active").json == {"ok": True, "job": None}
    con = connect()
    blur_jobs.ensure_blur_jobs_table(con)
    con.execute("INSERT INTO blur_jobs(id,nc_path,status,created_at,heartbeat_at,progress) VALUES('j1','A/x.mp4','running',?,?,0.4)",
                (__import__("time").time(),) * 2)
    con.commit()
    job = client.get("/api/blur/jobs/active").json["job"]
    assert job["id"] == "j1" and job["status"] == "running"
    js = (Path(__file__).resolve().parent.parent / "phototag/static/admin/admin.js").read_text(encoding="utf-8")
    assert "resumeBlurJob" in js and "Freigegeben ✓" in js and "Verwerfen fehlgeschlagen" in js


@needs_ffmpeg
def test_debug_sheet_lists_kept_and_dropped_tracks(tmp_path, monkeypatch):
    src, png = tmp_path / "d.mp4", tmp_path / "out" / "sheet.png"
    make_video(src, seconds=3, audio=False)

    def script(n, angles):
        out = {0: [face(200, 90, 60, 80)]}
        if n % 2 == 0:
            out[0].append(face(400, 200, 90, 110, 0.45))      # persistent weak ghost
        return out

    text = fv.debug_sheet(src, Scripted(script), png)
    assert png.is_file() and cv2.imread(str(png)) is not None
    assert "behalten" in text and "verworfen" in text and "Rohtreffer" in text


def test_tracker_ignores_weak_oversized_box_near_track():
    from phototag.faceblur_video import Tracker
    from phototag.faceblur import FaceBox
    tr = Tracker(min_hits=2)
    good = FaceBox(600, 200, 90, 140, 0.9, landmarks=None, angle=0)
    huge = FaceBox(200, 190, 590, 590, 0.40, landmarks=None, angle=0)
    for i in range(0, 12, 3):
        tr.update(i, {0: [good]})
    tr.update(12, {0: [huge]})
    kept, _ = tr.finish()
    assert len(kept) == 1
    assert all(f.score >= 0.9 for _, f in kept[0].items)


def _fb(x, y, w, score=0.8, angle=0, eye_d=None):
    from phototag.faceblur import FaceBox
    e = w * 0.45 if eye_d is None else eye_d
    cx, cy = x + w / 2, y + w * 0.4
    lm = ((cx - e / 2, cy), (cx + e / 2, cy), (cx, cy + w * 0.2), (cx - w * 0.15, cy + w * 0.35), (cx + w * 0.15, cy + w * 0.35))
    return FaceBox(x, y, w, w * 1.2, score, landmarks=lm, angle=angle)


def test_collapsed_eyes_use_the_axis_of_the_orientation_not_noise():
    from phototag.faceblur import _Frame
    for angle, axis in ((0, (1.0, 0.0)), (270, (0.0, 1.0)), (90, (0.0, -1.0))):
        fr = _Frame(_fb(100, 100, 120, angle=angle, eye_d=4))
        assert fr.u == axis
    fr = _Frame(_fb(100, 100, 120, angle=270, eye_d=54))
    assert abs(fr.u[1]) < 0.01


def test_median_filter_removes_alternating_readings():
    from phototag.faceblur_video import despike
    items = [(i * 3, _fb(400, 250, 140) if i % 3 else _fb(380, 230, 70)) for i in range(12)]
    out = despike(items)
    assert all(f.w == 140 for _, f in out[1:-1])


def test_axis_follows_the_majority_for_collapsed_samples():
    from phototag.faceblur_video import settle_axes
    items = [(i * 3, _fb(100, 100, 120, angle=0, eye_d=4)) for i in range(8)]
    items[4] = (12, _fb(100, 100, 120, angle=270, eye_d=4))
    assert settle_axes(items)[4][1].angle == 0


def test_two_tracks_on_one_face_make_one_box():
    from phototag.faceblur_video import Track, tracks_to_frames
    a = Track(1, 0, [(i, _fb(100, 100, 100, 0.9)) for i in range(0, 30, 3)])
    b = Track(2, 0, [(i, _fb(110, 105, 110, 0.7)) for i in range(0, 30, 3)])
    frames = tracks_to_frames([a, b], 30, 30.0)
    assert all(len(v) == 1 for v in frames.values())


def test_unsure_sample_between_clear_neighbours_is_rebuilt_by_interpolation():
    from phototag.faceblur_video import gate_unsure, tracks_to_frames, Track
    sure_a, sure_b = _fb(100, 100, 120, 0.9), _fb(130, 100, 120, 0.9)
    odd = _fb(400, 300, 60, 0.55)
    items = [(0, sure_a), (3, odd), (6, sure_b)]
    assert [i for i, _ in gate_unsure(items, 30)] == [0, 6]
    frames = tracks_to_frames([Track(1, 0, items)], 20, 30.0, smooth=1)
    assert frames[3][0].x == pytest.approx(115)


def test_unsure_samples_stay_when_there_is_no_clear_neighbour_close_by():
    from phototag.faceblur_video import gate_unsure
    items = [(0, _fb(100, 100, 120, 0.9)), (50, _fb(400, 300, 60, 0.55)), (100, _fb(130, 100, 120, 0.9))]
    assert len(gate_unsure(items, 30)) == 3


def test_eyebar_of_a_bowed_head_keeps_the_bar_unless_face_fallback_is_chosen(monkeypatch):
    from phototag.faceblur import eye_bar
    bowed = _fb(100, 100, 120, angle=0, eye_d=4)
    bar = eye_bar(bowed, 0.15, 720, 480)
    assert (bar[2] - bar[0]) > 1.5 * (bar[3] - bar[1])
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_UNSURE", "face")
    face = eye_bar(bowed, 0.15, 720, 480)
    assert (face[2] - face[0]) >= 120 and (face[3] - face[1]) >= 120
    healthy = eye_bar(_fb(100, 100, 120, angle=0, eye_d=54), 0.15, 720, 480)
    assert (healthy[2] - healthy[0]) > 1.5 * (healthy[3] - healthy[1])


def test_when_readings_alternate_the_bigger_one_wins():
    from phototag.faceblur_video import despike
    items = [(i * 3, _fb(380, 230, 70) if i % 2 else _fb(400, 250, 140)) for i in range(12)]
    assert all(f.w == 140 for _, f in despike(items)[1:-1])


def test_axis_votes_come_from_the_original_readings_not_the_size_filter():
    from phototag.faceblur_video import settle_axes
    original = [(i * 3, _fb(100, 100, 120, angle=270 if i % 3 == 0 else 0, eye_d=4)) for i in range(10)]
    filtered = [(i, _fb(100, 100, 120, angle=270, eye_d=4)) for i, _ in original]
    assert all(f.angle == 0 for _, f in settle_axes(filtered, voters=original)[2:-2])
