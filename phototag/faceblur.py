"""Face blur for images and videos (local, no cloud). Originals are never modified.

Detector: YuNet (OpenCV ``FaceDetectorYN``, ONNX file, MIT licence). Blurred copies are
written next to the original with the suffix ``ai-blurred`` (see ``blurred_filename``).
Output files carry no EXIF/GPS/container metadata.

Optional dependencies: ``pip install -e ".[faceblur]"`` (opencv-python-headless, numpy).
"""
from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phototag.config import (
    faceblur_margin,
    faceblur_max_detect_side,
    faceblur_mode,
    faceblur_model_path,
    faceblur_score,
    faceblur_suffix,
    faceblur_video_crf,
    faceblur_video_stride,
    faceblur_video_timeout_sec,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


# Row status of blur copies (not picked up by night loop / batch tagging)
BLUR_REVIEW = "blurred_review"
BLUR_OK = "blurred_ok"
BLUR_STATUSES = (BLUR_REVIEW, BLUR_OK)


class FaceBlurError(RuntimeError):
    """Configuration, input or processing problem (message is shown in the UI)."""


class NoFacesFound(FaceBlurError):
    """No face detected: no copy is written (a copy without blur would look anonymised)."""


@dataclass
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0


@dataclass
class BlurResult:
    faces: int
    width: int
    height: int
    kind: str
    frames: int = 0
    notes: list[str] = field(default_factory=list)


ProgressFn = Callable[[float], None]


# ---------------------------------------------------------------- naming

def is_blurred_name(name: str, suffix: str | None = None) -> bool:
    suffix = suffix or faceblur_suffix()
    return f".{suffix}" in Path(name).stem or Path(name).stem.endswith(f"-{suffix}")


def blurred_filename(name: str, suffix: str | None = None) -> str:
    """``IMG_1.jpg`` -> ``IMG_1.ai-blurred.jpg``; videos become ``.mp4`` (re-encoded)."""
    suffix = suffix or faceblur_suffix()
    p = Path(name)
    if is_blurred_name(name, suffix):
        raise FaceBlurError(f"Datei ist bereits eine Blur-Kopie: {name}")
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        ext = ".mp4"
    elif ext not in IMAGE_EXTS:
        raise FaceBlurError(f"Dateityp nicht unterstützt: {p.suffix or '(keine Endung)'}")
    return f"{p.stem}.{suffix}{ext}"


def unique_destination(directory: Path, name: str) -> Path:
    """Never overwrite: ``x.ai-blurred.jpg`` exists -> ``x.ai-blurred-2.jpg`` ..."""
    dest = directory / name
    if not dest.exists():
        return dest
    stem, ext = Path(name).stem, Path(name).suffix
    for n in range(2, 1000):
        cand = directory / f"{stem}-{n}{ext}"
        if not cand.exists():
            return cand
    raise FaceBlurError(f"Kein freier Dateiname für {name}")


# ---------------------------------------------------------------- detector

def _require_cv2():
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        raise FaceBlurError(
            'opencv/numpy fehlen — installieren: pip install -e ".[faceblur]"'
        ) from exc
    import cv2
    import numpy as np

    return cv2, np


class FaceDetector:
    """YuNet wrapper. Boxes are returned in the coordinates of the image passed in."""

    def __init__(self, model_path: str | None = None, score: float | None = None):
        cv2, _ = _require_cv2()
        path = model_path or faceblur_model_path()
        if not path:
            raise FaceBlurError("PHOTOTAG_FACEBLUR_MODEL nicht gesetzt (YuNet .onnx, siehe DEPLOY.md)")
        if not Path(path).is_file():
            raise FaceBlurError(f"Face-Modell fehlt: {path}")
        self._cv2 = cv2
        self._score = score if score is not None else faceblur_score()
        self._det = cv2.FaceDetectorYN.create(path, "", (320, 320), self._score, 0.3, 5000)

    def detect(self, bgr, *, max_side: int | None = None) -> list[FaceBox]:
        cv2 = self._cv2
        h, w = bgr.shape[:2]
        limit = max_side or faceblur_max_detect_side()
        scale = 1.0
        work = bgr
        if max(h, w) > limit:
            scale = limit / float(max(h, w))
            work = cv2.resize(bgr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        wh, ww = work.shape[:2]
        self._det.setInputSize((ww, wh))
        _, faces = self._det.detect(work)
        out: list[FaceBox] = []
        if faces is None:
            return out
        for f in faces:
            x, y, bw, bh, score = (float(v) for v in (f[0], f[1], f[2], f[3], f[14]))
            out.append(FaceBox(x / scale, y / scale, bw / scale, bh / scale, score))
        return out


# ---------------------------------------------------------------- blur

def expand_box(b: FaceBox, margin: float, width: int, height: int) -> tuple[int, int, int, int]:
    """Grow the detector box (forehead, hair, chin are often outside) and clip to the image."""
    mx, my = b.w * margin, b.h * margin
    x0 = max(0, int(b.x - mx))
    y0 = max(0, int(b.y - my * 1.2))  # a bit more above: forehead/hair
    x1 = min(width, int(b.x + b.w + mx))
    y1 = min(height, int(b.y + b.h + my))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def apply_blur(bgr, boxes: list[tuple[int, int, int, int]], mode: str | None = None) -> None:
    """In-place. ``blur`` = very strong Gaussian, ``pixel`` = coarse mosaic (harder to reverse)."""
    cv2, _ = _require_cv2()
    mode = mode or faceblur_mode()
    for x0, y0, x1, y1 in boxes:
        roi = bgr[y0:y1, x0:x1]
        rh, rw = roi.shape[:2]
        if rh < 2 or rw < 2:
            continue
        if mode == "pixel":
            cells = 8
            small = cv2.resize(roi, (max(1, min(cells, rw)), max(1, min(cells, rh))), interpolation=cv2.INTER_AREA)
            bgr[y0:y1, x0:x1] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
        else:
            # shrink first (drops detail for good), blur, scale back
            small = cv2.resize(roi, (max(2, rw // 6), max(2, rh // 6)), interpolation=cv2.INTER_AREA)
            sigma = max(2.0, max(small.shape[:2]) / 4.0)
            small = cv2.GaussianBlur(small, (0, 0), sigma)
            bgr[y0:y1, x0:x1] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_CUBIC)


def blur_frame(bgr, faces: list[FaceBox], *, margin: float | None = None, mode: str | None = None) -> int:
    h, w = bgr.shape[:2]
    m = faceblur_margin() if margin is None else margin
    boxes = [expand_box(b, m, w, h) for b in faces]
    apply_blur(bgr, boxes, mode)
    return len(boxes)


# ---------------------------------------------------------------- images

def blur_image_file(
    src: Path,
    dst: Path,
    *,
    detector: FaceDetector | None = None,
    allow_empty: bool = False,
) -> BlurResult:
    cv2, np = _require_cv2()
    from PIL import Image, ImageOps

    det = detector or FaceDetector()
    try:
        with Image.open(src) as im:
            if getattr(im, "n_frames", 1) > 1:
                raise FaceBlurError("Animierte Bilder werden nicht unterstützt")
            im = ImageOps.exif_transpose(im)
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            rgba = im.convert("RGBA") if has_alpha else None
            rgb = np.asarray(im.convert("RGB"))
    except OSError as exc:
        raise FaceBlurError(f"Bild nicht lesbar: {exc}") from exc

    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    faces = det.detect(bgr)
    if not faces and not allow_empty:
        raise NoFacesFound("Keine Gesichter erkannt — keine Kopie erzeugt")
    n = blur_frame(bgr, faces)
    out = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
    if rgba is not None:
        out = out.convert("RGBA")
        out.putalpha(rgba.getchannel("A"))
    ext = dst.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        out.convert("RGB").save(dst, "JPEG", quality=95, subsampling=0)  # no exif argument = metadata stripped
    elif ext == ".png":
        out.save(dst, "PNG", optimize=True)
    elif ext == ".webp":
        out.save(dst, "WEBP", quality=95)
    else:
        raise FaceBlurError(f"Ausgabeformat nicht unterstützt: {ext}")
    h, w = bgr.shape[:2]
    return BlurResult(faces=n, width=w, height=h, kind="image")


# ---------------------------------------------------------------- video

def _parse_rate(raw: str | None) -> float:
    try:
        if raw and "/" in raw:
            a, b = raw.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(raw or 0)
    except ValueError:
        return 0.0


def probe_display(path: Path) -> tuple[int, int, float, float]:
    """(width, height, fps, duration) as the player shows it (rotation applied)."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
         "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation:format=duration",
         str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        raise FaceBlurError(f"ffprobe fehlgeschlagen: {proc.stderr[:200].strip()}")
    data: dict[str, Any] = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise FaceBlurError("Keine Videospur gefunden")
    s = streams[0]
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    rot = 0
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if not rot:
        try:
            rot = int((s.get("tags") or {}).get("rotate") or 0)
        except ValueError:
            rot = 0
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate")) or 25.0
    duration = float((data.get("format") or {}).get("duration") or 0)
    if w <= 0 or h <= 0:
        raise FaceBlurError("Videogrösse unbekannt")
    return w, h, min(max(fps, 1.0), 120.0), duration


def _iter_frames(path: Path, w: int, h: int, deadline: float) -> Iterator[Any]:
    _, np = _require_cv2()
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    size = w * h * 3
    try:
        while True:
            if time.time() > deadline:
                raise FaceBlurError("Zeitlimit beim Video erreicht (PHOTOTAG_FACEBLUR_VIDEO_TIMEOUT)")
            buf = proc.stdout.read(size) if proc.stdout else b""
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy()
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()


def blur_video_file(
    src: Path,
    dst: Path,
    *,
    detector: FaceDetector | None = None,
    progress: ProgressFn | None = None,
    allow_empty: bool = False,
) -> BlurResult:
    """Two passes: (1) detect every ``stride`` frames, (2) blur each frame with boxes from the
    neighbouring detections (±2 strides, so faces are covered before they are first detected
    and after they are last seen). Audio is re-encoded to AAC, metadata is dropped."""
    _, np = _require_cv2()
    det = detector or FaceDetector()
    w, h, fps, duration = probe_display(src)
    stride = faceblur_video_stride()
    hold = stride * 2
    deadline = time.time() + faceblur_video_timeout_sec()
    est_frames = max(1, int(duration * fps)) if duration else 0

    samples: dict[int, list[FaceBox]] = {}
    total = 0
    for i, frame in enumerate(_iter_frames(src, w, h, deadline)):
        total = i + 1
        if i % stride == 0:
            found = det.detect(frame, max_side=1280)
            if found:
                samples[i] = found
        if progress and est_frames and i % 30 == 0:
            progress(min(0.45, 0.45 * i / est_frames))
    if total == 0:
        raise FaceBlurError("Keine Videoframes gelesen")
    if not samples and not allow_empty:
        raise NoFacesFound("Keine Gesichter erkannt — keine Kopie erzeugt")

    margin = faceblur_margin() + 0.1  # extra room for motion between samples
    sample_idx = sorted(samples)
    crf = faceblur_video_crf()
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
         "-i", str(src), "-map", "0:v:0", "-map", "1:a:0?",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k",
         "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart",
         "-shortest", str(dst)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    blurred_frames = 0
    max_faces = max((len(v) for v in samples.values()), default=0)
    try:
        import bisect

        for i, frame in enumerate(_iter_frames(src, w, h, deadline)):
            lo = bisect.bisect_left(sample_idx, i - hold)
            hi = bisect.bisect_right(sample_idx, i + hold)
            near: list[FaceBox] = []
            for k in sample_idx[lo:hi]:
                near.extend(samples[k])
            if near:
                blur_frame(frame, near, margin=margin)
                blurred_frames += 1
            assert enc.stdin is not None
            enc.stdin.write(np.ascontiguousarray(frame).tobytes())
            if progress and est_frames and i % 30 == 0:
                progress(min(0.99, 0.45 + 0.55 * i / est_frames))
        assert enc.stdin is not None
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")[:400] if enc.stderr else ""
        rc = enc.wait(timeout=600)
    except BrokenPipeError:
        err = enc.stderr.read().decode(errors="replace")[:400] if enc.stderr else ""
        enc.kill()
        raise FaceBlurError(f"ffmpeg Encode abgebrochen: {err}") from None
    except Exception:
        enc.kill()
        enc.wait()
        dst.unlink(missing_ok=True)
        raise
    if rc != 0 or not dst.is_file() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise FaceBlurError(f"ffmpeg Encode fehlgeschlagen: {err or rc}")
    out_w, out_h, _, out_dur = probe_display(dst)
    if duration and abs(out_dur - duration) > max(2.0, duration * 0.05):
        dst.unlink(missing_ok=True)
        raise FaceBlurError(f"Ausgabe-Dauer weicht ab ({out_dur:.1f}s statt {duration:.1f}s)")
    if progress:
        progress(1.0)
    return BlurResult(faces=max_faces, width=w, height=h, kind="video", frames=total,
                      notes=[f"{blurred_frames}/{total} Frames mit Blur"])


def blur_file(src: Path, dst: Path, **kw: Any) -> BlurResult:
    ext = src.suffix.lower()
    if ext in IMAGE_EXTS:
        kw.pop("progress", None)
        return blur_image_file(src, dst, **kw)
    if ext in VIDEO_EXTS:
        return blur_video_file(src, dst, **kw)
    raise FaceBlurError(f"Dateityp nicht unterstützt: {src.suffix or '(keine Endung)'}")


def temp_output_path(ext: str) -> Path:
    import uuid

    from phototag.config import telegram_temp_dir

    d = telegram_temp_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"phototag-fb-{uuid.uuid4().hex}{ext}"


def sweep_stale_blur_temps(max_age_sec: int = 6 * 3600) -> None:
    from phototag.config import telegram_temp_dir

    d = telegram_temp_dir()
    if not d.is_dir():
        return
    now = time.time()
    for p in d.glob("phototag-fb-*"):
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "BlurResult",
    "FaceBlurError",
    "FaceBox",
    "FaceDetector",
    "NoFacesFound",
    "apply_blur",
    "blur_file",
    "blur_image_file",
    "blur_video_file",
    "blurred_filename",
    "expand_box",
    "is_blurred_name",
    "temp_output_path",
    "unique_destination",
]
