"""Face blur for video: detect on a small copy, follow faces over time, render once.

Why not "detect every frame independently" (the first version): every frame then decides on its own,
a body pattern (nipples + navel) or a hand shows up as a "face" for a few frames, and a sideways face
flips between two readings. The result is shapes in the wrong place and sizes that jump.

Pipeline
  1. analysis  decode a small (<= 1280 px) copy, detect every ``stride`` frames
               (4 orientations only while searching or now and then; otherwise the orientation of the
               faces already being followed)
  2. tracking  link detections into tracks, prefer the orientation a track already has,
               drop weak one-off tracks, smooth the landmarks
  3. frames    interpolate between detections, bridge short gaps, keep blurring a moment before the
               first and after the last detection
  4. render    decode the full copy once (scaled to <= VIDEO_MAX_SIDE), blur, encode (ffmpeg)

Everything between 2 and 3 works on plain numbers and is unit-tested without video or model.
"""
from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phototag.config import (
    faceblur_corroborate,
    faceblur_gap_sec,
    faceblur_hold_sec,
    faceblur_margin,
    faceblur_max_faces,
    faceblur_rotate,
    faceblur_settings_summary,
    faceblur_track_min_score,
    faceblur_track_trust_score,
    faceblur_track_sure_score,
    faceblur_video_crf,
    faceblur_video_max_side,
    faceblur_video_preset,
    faceblur_video_stride,
    faceblur_video_timeout_sec,
)
from phototag.faceblur import (
    COLLAPSED_EYES,
    BlurResult,
    FaceBlurError,
    FaceBox,
    FaceDetector,
    NoFacesFound,
    _merge_faces,
    _require_cv2,
    same_face,
    blur_frame,
    probe_display,
    quality_notes,
)

ProgressFn = Callable[[float], None]

ANALYSIS_SIDE = 1280
ANGLE_BONUS = 0.02          # tie-break only: the right view beats the wrong ones by just 0.08-0.16, so no stickiness
JUMP_WEIGHT = 0.12          # continuity: of two similar detections prefer the one that stays where the face was
SURE_ITEM = 0.70           # a sample at/above this (and with trustworthy eyes) can serve as anchor for its neighbours
ACTIVE_WITHIN = 6           # samples: a track counts as "live" this long after its last hit (ghost suppression)


# ---------------------------------------------------------------- geometry helpers

def _centre(f: FaceBox) -> tuple[float, float]:
    return f.x + f.w / 2.0, f.y + f.h / 2.0


def _iou(a: FaceBox, b: FaceBox) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def _near(a: FaceBox, b: FaceBox) -> bool:
    """b plausibly continues a (it moved less than about one face size)."""
    if _iou(a, b) > 0.08:
        return True
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    return math.hypot(ax - bx, ay - by) <= 1.1 * max(a.w, a.h, b.w, b.h)


def _jump(a: FaceBox, b: FaceBox) -> float:
    """How far b is from a in face sizes, plus how much its size changed (log ratio)."""
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    size = max(a.w, 1.0)
    return math.hypot(ax - bx, ay - by) / size + abs(math.log(max(b.w, 1.0) / size))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_face(a: FaceBox, b: FaceBox, t: float) -> FaceBox:
    lm = None
    if a.landmarks and b.landmarks:
        lm = tuple((_lerp(p[0], q[0], t), _lerp(p[1], q[1], t)) for p, q in zip(a.landmarks, b.landmarks))
    return FaceBox(
        _lerp(a.x, b.x, t), _lerp(a.y, b.y, t), _lerp(a.w, b.w, t), _lerp(a.h, b.h, t),
        _lerp(a.score, b.score, t), landmarks=lm, angle=a.angle if t < 0.5 else b.angle,
    )


def scale_face(f: FaceBox, k: float) -> FaceBox:
    lm = tuple((p[0] * k, p[1] * k) for p in f.landmarks) if f.landmarks else None
    return FaceBox(f.x * k, f.y * k, f.w * k, f.h * k, f.score, landmarks=lm, angle=f.angle)


# ---------------------------------------------------------------- tracking

@dataclass
class Track:
    id: int
    angle: int
    items: list[tuple[int, FaceBox]] = field(default_factory=list)  # (frame index, face)
    last_sample: int = 0
    checked: bool = False       # was there a chance to see it in a second orientation?
    corroborated: bool = False  # ... and was it seen there too?

    @property
    def hits(self) -> int:
        return len(self.items)

    @property
    def last_face(self) -> FaceBox:
        return self.items[-1][1]

    @property
    def mean_score(self) -> float:
        return sum(f.score for _, f in self.items) / max(1, len(self.items))

    @property
    def max_score(self) -> float:
        return max((f.score for _, f in self.items), default=0.0)


class Tracker:
    """Links per-sample detections into tracks and decides which tracks are real faces."""

    def __init__(self, *, min_hits: int = 2, min_mean: float | None = None, sure: float | None = None,
                 max_gap_samples: int = 40):
        self.min_hits = min_hits
        self.min_mean = faceblur_track_min_score() if min_mean is None else min_mean
        self.sure = faceblur_track_sure_score() if sure is None else sure
        self.trust = faceblur_track_trust_score()   # above this a detection needs no second orientation
        self.max_gap = max_gap_samples
        self.item_floor = max(0.0, self.min_mean - 0.05)   # single detections below this never extend a track
        self.tracks: list[Track] = []
        self._n = 0          # samples seen
        self._next_id = 1

    def _fits(self, tr: Track, c: FaceBox) -> bool:
        """A weak or suddenly much bigger/smaller box is not the same face, whatever its position
        (a bowed head or a torso gets a large low-score box that would otherwise capture the track)."""
        if c.score < self.item_floor:
            return False
        ref = tr.last_face.w
        return ref <= 0 or 0.5 <= c.w / ref <= 2.0

    def update(self, frame_idx: int, passes: dict[int, list[FaceBox]]) -> None:
        self._n += 1
        cands = [f for faces in passes.values() for f in faces]
        everything = list(cands)
        multi = len([k for k in passes]) > 1

        def corroborate(tr: Track, face: FaceBox) -> None:
            if multi:
                tr.checked = True
                if any(c.angle != face.angle and same_face(face, c) for c in everything):
                    tr.corroborated = True

        for tr in sorted(self.tracks, key=lambda t: (-t.hits, t.id)):
            if self._n - tr.last_sample > self.max_gap:
                continue
            near = [c for c in cands if _near(tr.last_face, c) and self._fits(tr, c)]
            if not near:
                continue
            ref = tr.last_face
            best = max(
                near,
                key=lambda c: c.score
                + (ANGLE_BONUS if c.angle == tr.angle else 0.0)
                - JUMP_WEIGHT * _jump(ref, c),
            )
            tr.items.append((frame_idx, best))
            tr.angle = best.angle
            tr.last_sample = self._n
            corroborate(tr, best)
            cands = [c for c in cands if not same_face(best, c)]
        # What is left may be a NEW face. Never start one on top of a face that is already followed:
        # that is the same face seen in another orientation (wrong landmarks) and would show up as a ghost.
        live = [t.last_face for t in self.tracks if self._n - t.last_sample <= ACTIVE_WITHIN]
        cands = [c for c in cands if not any(same_face(l, c) for l in live)]
        for f in _merge_faces([c for c in cands if c.score >= self.item_floor]):
            tr = Track(self._next_id, f.angle, [(frame_idx, f)], self._n)
            corroborate(tr, f)
            self._next_id += 1
            self.tracks.append(tr)

    def finish(self) -> tuple[list[Track], list[tuple[Track, str]]]:
        """(kept, dropped with reason). A track is real when it is long enough AND clearly a face,
        or when a single detection is very sure."""
        kept: list[Track] = []
        dropped: list[tuple[Track, str]] = []
        for tr in self.tracks:
            unconfirmed = faceblur_corroborate() and tr.checked and not tr.corroborated
            if unconfirmed and tr.mean_score < self.trust:
                dropped.append((tr, f"nur in einer Drehung gefunden (Drehung {tr.angle}, Mittel {tr.mean_score:.2f})"))
            elif tr.hits < self.min_hits:
                if tr.max_score >= self.sure:
                    kept.append(tr)          # one very sure hit: a face that only flashes by
                else:
                    dropped.append((tr, f"nur {tr.hits} Treffer, max {tr.max_score:.2f}"))
            elif tr.mean_score < self.min_mean:
                dropped.append((tr, f"Mittel {tr.mean_score:.2f} < {self.min_mean:.2f}"))
            else:
                kept.append(tr)
        kept, dups = dedupe_tracks(kept)
        dropped.extend(dups)
        limit = faceblur_max_faces()
        if limit and len(kept) > limit:
            ranked = sorted(kept, key=lambda t: -(t.hits * t.mean_score))
            for tr in ranked[limit:]:
                dropped.append((tr, f"MAX_FACES={limit}"))
            kept = ranked[:limit]
        return kept, dropped


def dedupe_tracks(tracks: list[Track]) -> tuple[list[Track], list[tuple[Track, str]]]:
    """Two tracks that sit on the same face at the same time are one face: keep the stronger one."""
    ranked = sorted(tracks, key=lambda t: -(t.hits * t.mean_score))
    kept: list[Track] = []
    dropped: list[tuple[Track, str]] = []
    for tr in ranked:
        mine = dict(tr.items)
        dup_of = None
        for k in kept:
            theirs = dict(k.items)
            common = [i for i in mine if i in theirs]
            if common and sum(1 for i in common if same_face(mine[i], theirs[i])) >= max(1, len(common) // 2):
                dup_of = k
                break
        if dup_of is None:
            kept.append(tr)
        else:
            dropped.append((tr, f"Duplikat von Track {dup_of.id}"))
    return sorted(kept, key=lambda t: t.id), dropped


# ---------------------------------------------------------------- smoothing, interpolation

def _collapsed(f: FaceBox) -> bool:
    if not f.landmarks:
        return False
    (rx, ry), (lx, ly) = f.landmarks[0], f.landmarks[1]
    return math.hypot(lx - rx, ly - ry) < COLLAPSED_EYES * f.w


def is_sure(f: FaceBox) -> bool:
    return f.score >= SURE_ITEM and not _collapsed(f)


def gate_unsure(items: list[tuple[int, FaceBox]], gap: int) -> list[tuple[int, FaceBox]]:
    """Drop unsure samples sandwiched between clear neighbours within ``gap`` frames."""
    if len(items) < 3:
        return list(items)
    sure = [is_sure(f) for _, f in items]
    out: list[tuple[int, FaceBox]] = []
    for k, (idx, f) in enumerate(items):
        if sure[k]:
            out.append((idx, f))
            continue
        before = next((j for j in range(k - 1, -1, -1) if sure[j]), None)
        after = next((j for j in range(k + 1, len(items)) if sure[j]), None)
        bridged = (
            before is not None
            and after is not None
            and items[after][0] - items[before][0] <= gap
        )
        if not bridged:
            out.append((idx, f))
    return out


def despike(items: list[tuple[int, FaceBox]], window: int = 5) -> list[tuple[int, FaceBox]]:
    """Upper-median filter on sample size — alternating small/big readings become one stable size."""
    if window < 3 or len(items) < 3:
        return list(items)
    half = window // 2
    out: list[tuple[int, FaceBox]] = []
    for k, (idx, f) in enumerate(items):
        grp = items[max(0, k - half): k + half + 1]
        mid = sorted(grp, key=lambda it: it[1].w)[min(len(grp) - 1, (len(grp) * 3) // 5)][1]
        off = max(f.w, mid.w) / max(1.0, min(f.w, mid.w)) > 1.3
        out.append((idx, mid if off else f))
    return out


def settle_axes(items: list[tuple[int, FaceBox]], window: int = 21,
                voters: list[tuple[int, FaceBox]] | None = None) -> list[tuple[int, FaceBox]]:
    """Collapsed-eye samples take the detection angle most neighbours agree on."""
    half = window // 2
    out = list(items)
    for k, (idx, f) in enumerate(items):
        if not _collapsed(f):
            continue
        votes: dict[int, int] = {}
        pool = [(i, g) for i, g in (voters if voters is not None else items) if abs(i - idx) <= half * 3]
        for _, g in pool:
            votes[g.angle] = votes.get(g.angle, 0) + 1
        best = max(votes, key=lambda a: (votes[a], a == f.angle))
        if best != f.angle:
            out[k] = (idx, FaceBox(f.x, f.y, f.w, f.h, f.score, landmarks=f.landmarks, angle=best))
    return out


def smooth_items(items: list[tuple[int, FaceBox]], window: int = 3) -> list[tuple[int, FaceBox]]:
    """Moving average of position, size and landmarks over neighbouring samples (keeps jitter out)."""
    if window <= 1 or len(items) < 3:
        return list(items)
    half = window // 2
    out: list[tuple[int, FaceBox]] = []
    for i, (idx, f) in enumerate(items):
        grp = [g for _, g in items[max(0, i - half): i + half + 1]]
        n = len(grp)
        lm = None
        if all(g.landmarks for g in grp):
            lm = tuple(
                (sum(g.landmarks[k][0] for g in grp) / n, sum(g.landmarks[k][1] for g in grp) / n)
                for k in range(len(f.landmarks))
            )
        out.append((idx, FaceBox(
            sum(g.x for g in grp) / n, sum(g.y for g in grp) / n,
            sum(g.w for g in grp) / n, sum(g.h for g in grp) / n, f.score, landmarks=lm, angle=f.angle,
        )))
    return out


def tracks_to_frames(
    tracks: list[Track], total_frames: int, fps: float, *, scale: float = 1.0,
    gap_sec: float | None = None, hold_sec: float | None = None, smooth: int = 3,
) -> dict[int, list[FaceBox]]:
    """frame index -> faces to blur (coordinates multiplied by ``scale``)."""
    gap = max(0, int(round((faceblur_gap_sec() if gap_sec is None else gap_sec) * fps)))
    hold = max(0, int(round((faceblur_hold_sec() if hold_sec is None else hold_sec) * fps)))
    frames: dict[int, list[FaceBox]] = {}

    def put(i: int, f: FaceBox) -> None:
        if 0 <= i < total_frames:
            frames.setdefault(i, []).append(scale_face(f, scale) if scale != 1.0 else f)

    for tr in tracks:
        gated = gate_unsure(sorted(tr.items, key=lambda it: it[0]), max(gap, 1))
        items = smooth_items(settle_axes(despike(gated), voters=gated), smooth)
        segment_start = 0
        for k in range(len(items)):
            last_of_segment = k == len(items) - 1 or items[k + 1][0] - items[k][0] > max(gap, 1)
            if k < len(items) - 1 and not last_of_segment:
                (ia, fa), (ib, fb) = items[k], items[k + 1]
                for i in range(ia, ib):
                    put(i, lerp_face(fa, fb, (i - ia) / max(1, ib - ia)))
            if last_of_segment:
                ia, fa = items[segment_start]
                for i in range(max(0, ia - hold), ia):          # before the first detection
                    put(i, fa)
                ib, fb = items[k]
                for i in range(ib, min(total_frames, ib + hold + 1)):  # the last one and after it
                    put(i, fb)
                segment_start = k + 1
    for i, faces in frames.items():
        if len(faces) > 1:
            frames[i] = _merge_faces(faces)
    return frames


# ---------------------------------------------------------------- video I/O

def _even(n: float) -> int:
    return max(2, int(round(n / 2.0)) * 2)


class _RawFrames:
    """ffmpeg -> raw BGR frames into ONE reused buffer (no per-frame copy)."""

    def __init__(self, path: Path, w: int, h: int, *, scale_to: tuple[int, int] | None, deadline: float, fps: float):
        _, np = _require_cv2()
        cmd = ["ffmpeg", "-v", "error", "-threads", "0", "-i", str(path), "-an", "-sn"]
        # fps filter: a phone video with variable frame rate becomes a constant-rate frame list by
        # timestamp (same list in the analysis and the render pass, and as long as the original)
        chain = [f"fps=fps={fps:.6f}"]
        if scale_to is not None and scale_to != (w, h):
            chain.append(f"scale={scale_to[0]}:{scale_to[1]}:flags=area")
        cmd += ["-vf", ",".join(chain)]
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.w, self.h = scale_to if scale_to else (w, h)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.buf = np.empty((self.h, self.w, 3), dtype=np.uint8)
        self._view = memoryview(self.buf).cast("B")
        self._deadline = deadline

    def __iter__(self):
        size = self.w * self.h * 3
        out = self._proc.stdout
        assert out is not None
        while True:
            if time.time() > self._deadline:
                raise FaceBlurError("Zeitlimit beim Video erreicht (PHOTOTAG_FACEBLUR_VIDEO_TIMEOUT)")
            got = 0
            while got < size:
                n = out.readinto(self._view[got:])
                if not n:
                    break
                got += n
            if got < size:
                return
            yield self.buf

    def close(self) -> None:
        try:
            if self._proc.stdout:
                self._proc.stdout.close()
        finally:
            self._proc.kill()
            self._proc.wait()


@dataclass
class Analysis:
    width: int
    height: int
    fps: float
    duration: float
    total_frames: int
    analysis_size: tuple[int, int]
    tracks: list[Track]
    dropped: list[tuple[Track, str]]
    raw_detections: int
    samples: int
    angle_switches: int = 0


def analyze_video(
    src: Path, detector: FaceDetector, *, progress: ProgressFn | None = None, progress_span: float = 0.35,
) -> Analysis:
    w, h, fps, duration = probe_display(src)
    ratio = min(1.0, ANALYSIS_SIDE / float(max(w, h)))
    aw, ah = (_even(w * ratio), _even(h * ratio)) if ratio < 1.0 else (w, h)
    stride = faceblur_video_stride()
    deadline = time.time() + faceblur_video_timeout_sec()
    est = max(1, int(duration * fps)) if duration else 0
    rotate = faceblur_rotate()
    tracker = Tracker()
    raw = 0
    switches = 0
    total = 0
    reader = _RawFrames(src, w, h, scale_to=(aw, ah), deadline=deadline, fps=fps)
    try:
        for i, frame in enumerate(reader):
            total = i + 1
            if i % stride == 0:
                # all four views every time: while a head turns, the best view changes from sample to sample
                angles = (0, 90, 180, 270) if rotate else (0,)
                passes = detector.detect_passes(frame, angles, max_side=ANALYSIS_SIDE)
                raw += sum(len(v) for v in passes.values())
                before = {t.id: t.angle for t in tracker.tracks}
                tracker.update(i, passes)
                switches += sum(1 for t in tracker.tracks if t.id in before and before[t.id] != t.angle)
            if progress and est and i % 30 == 0:
                progress(min(progress_span, progress_span * i / est))
    finally:
        reader.close()
    if total == 0:
        raise FaceBlurError("Keine Videoframes gelesen")
    kept, dropped = tracker.finish()
    return Analysis(w, h, fps, duration, total, (aw, ah), kept, dropped, raw, tracker._n, switches)


def _encode_cmd(src: Path, dst: Path, w: int, h: int, fps: float) -> list[str]:
    return [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
        "-i", str(src), "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", faceblur_video_preset(), "-crf", str(faceblur_video_crf()),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart",
        str(dst),
    ]


def blur_video(
    src: Path,
    dst: Path,
    *,
    detector: FaceDetector | None = None,
    progress: ProgressFn | None = None,
    allow_empty: bool = False,
) -> BlurResult:
    det = detector or FaceDetector()
    an = analyze_video(src, det, progress=progress)
    if not an.tracks and not allow_empty:
        raise NoFacesFound("Keine Gesichter erkannt — keine Kopie erzeugt")

    max_side = faceblur_video_max_side()
    r = min(1.0, max_side / float(max(an.width, an.height))) if max_side else 1.0
    ow, oh = (_even(an.width * r), _even(an.height * r)) if r < 1.0 else (an.width, an.height)
    k = ow / float(an.analysis_size[0])          # analysis coordinates -> output coordinates
    frame_faces = tracks_to_frames(an.tracks, an.total_frames, an.fps, scale=k)
    deadline = time.time() + faceblur_video_timeout_sec()
    margin = faceblur_margin()

    enc = subprocess.Popen(_encode_cmd(src, dst, ow, oh, an.fps), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    blurred = 0
    reader = _RawFrames(src, an.width, an.height, scale_to=(ow, oh), deadline=deadline, fps=an.fps)
    est = max(1, an.total_frames)
    try:
        assert enc.stdin is not None
        for i, frame in enumerate(reader):
            faces = frame_faces.get(i)
            if faces:
                blur_frame(frame, faces, margin=margin)
                blurred += 1
            enc.stdin.write(frame.data)
            if progress and i % 30 == 0:
                progress(min(0.99, 0.35 + 0.65 * i / est))
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")[:400] if enc.stderr else ""
        rc = enc.wait(timeout=900)
    except BrokenPipeError:
        enc.wait(timeout=30)
        err = enc.stderr.read().decode(errors="replace")[:400] if enc.stderr else ""
        enc.kill()
        dst.unlink(missing_ok=True)
        raise FaceBlurError(f"ffmpeg Encode abgebrochen: {err}") from None
    except Exception:
        enc.kill()
        enc.wait()
        dst.unlink(missing_ok=True)
        raise
    finally:
        reader.close()
    if rc != 0 or not dst.is_file() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise FaceBlurError(f"ffmpeg Encode fehlgeschlagen: {err or rc}")
    _, _, _, out_dur = probe_display(dst)
    if an.duration and abs(out_dur - an.duration) > max(1.0, an.duration * 0.03):
        dst.unlink(missing_ok=True)
        raise FaceBlurError(f"Ausgabe-Dauer weicht ab ({out_dur:.1f}s statt {an.duration:.1f}s)")
    if progress:
        progress(1.0)

    coverage = blurred / float(an.total_frames)
    notes = [
        faceblur_settings_summary(),
        f"{blurred}/{an.total_frames} Frames mit Blur ({coverage:.0%}); {len(an.tracks)} Gesicht(er) verfolgt, "
        f"{len(an.dropped)} unsichere Treffer verworfen; Ausgabe {ow}x{oh}",
    ]
    if coverage < 0.9:
        notes.append(f"WARNUNG: in {1 - coverage:.0%} der Frames kein Gesicht gefunden — Video durchsehen")
    notes.extend(quality_notes(0.0, 0))
    return BlurResult(faces=len(an.tracks), width=ow, height=oh, kind="video", frames=an.total_frames, notes=notes)


# ---------------------------------------------------------------- debug sheet

def debug_sheet(src: Path, detector: FaceDetector, out_png: Path, *, columns: int = 4, rows: int = 3) -> str:
    """Analysis only: PNG with sample frames, kept tracks in green, dropped detections in red."""
    cv2, np = _require_cv2()
    an = analyze_video(src, detector)
    n = columns * rows
    picks = sorted({int(i * (an.total_frames - 1) / max(1, n - 1)) for i in range(n)})
    w, h = an.analysis_size
    kept_by_frame = tracks_to_frames(an.tracks, an.total_frames, an.fps, scale=1.0)
    dropped_items: dict[int, list[tuple[FaceBox, str]]] = {}
    for tr, why in an.dropped:
        for idx, f in tr.items:
            dropped_items.setdefault(idx, []).append((f, why))
    wanted = set(picks)
    tiles = []
    reader = _RawFrames(src, an.width, an.height, scale_to=(w, h), deadline=time.time() + faceblur_video_timeout_sec(), fps=an.fps)
    try:
        for idx, buf in enumerate(reader):
            if idx not in wanted:
                if idx > max(picks):
                    break
                continue
            frame = buf.copy()
            for f in kept_by_frame.get(idx, []):
                cv2.rectangle(frame, (int(f.x), int(f.y)), (int(f.x + f.w), int(f.y + f.h)), (0, 255, 0), 3)
                for p in f.landmarks or ():
                    cv2.circle(frame, (int(p[0]), int(p[1])), 5, (0, 255, 255), -1)
                cv2.putText(frame, f"{f.score:.2f} @{f.angle}", (int(f.x), max(18, int(f.y) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            near = [(f, why) for i2, lst in dropped_items.items() if abs(i2 - idx) <= faceblur_video_stride() for f, why in lst]
            for f, why in near:
                cv2.rectangle(frame, (int(f.x), int(f.y)), (int(f.x + f.w), int(f.y + f.h)), (0, 0, 255), 3)
                cv2.putText(frame, f"verworfen {f.score:.2f}", (int(f.x), int(f.y + f.h + 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.putText(frame, f"Frame {idx}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            tiles.append(cv2.resize(frame, (480, int(480 * h / w))))
    finally:
        reader.close()
    if not tiles:
        raise FaceBlurError("Keine Frames für das Prüfbild gelesen")
    th, tw = tiles[0].shape[:2]
    sheet = np.zeros((rows * th, columns * tw, 3), dtype=np.uint8)
    for i, t in enumerate(tiles[: rows * columns]):
        sheet[(i // columns) * th:(i // columns) * th + th, (i % columns) * tw:(i % columns) * tw + tw] = t
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), sheet)
    lines = [
        f"{src.name}: {an.total_frames} Frames, {an.fps:.1f} fps, {an.width}x{an.height}, Analyse {w}x{h}",
        f"Einstellungen: {faceblur_settings_summary()}",
        f"Rohtreffer {an.raw_detections} in {an.samples} Stichproben; verfolgt: {len(an.tracks)}; verworfen: {len(an.dropped)}; "
        f"Orientierungswechsel: {an.angle_switches}",
    ]
    for tr in an.tracks:
        lines.append(f"  behalten  Track {tr.id}: {tr.hits} Treffer, Mittel {tr.mean_score:.2f}, max {tr.max_score:.2f}, Drehung {tr.angle}")
    for tr, why in an.dropped:
        f = tr.last_face
        lines.append(f"  verworfen Track {tr.id}: {why} (Drehung {tr.angle}, Box {int(f.w)}x{int(f.h)} bei {int(f.x)},{int(f.y)})")
    lines.append(f"Prüfbild: {out_png}")
    return "\n".join(lines)
