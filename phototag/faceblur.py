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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phototag.config import (
    faceblur_fill,
    faceblur_fill_gap,
    faceblur_part_wh,
    BOX_PARTS,
    parse_region_names,
    faceblur_bar_down,
    faceblur_bar_side,
    faceblur_bar_up,
    faceblur_margin,
    faceblur_max_detect_side,
    faceblur_mode,
    faceblur_model_path,
    faceblur_region,
    faceblur_regions,
    faceblur_rotate,
    faceblur_scale,
    faceblur_score,
    faceblur_settings_summary,
    faceblur_strength,
    faceblur_unsure,
    faceblur_suffix,
)

ROTATED_MIN_SCORE = 0.7

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
    # YuNet: right eye, left eye, nose, right mouth corner, left mouth corner
    landmarks: tuple[tuple[float, float], ...] | None = None
    angle: int = 0  # detection pass that produced it (0/90/180/270): the face is upright in that view


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

    def _raw_detect(self, bgr, max_side: int | None = None, min_score: float | None = None) -> list[FaceBox]:
        """One pass, coordinates of the image passed in."""
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
        self._det.setScoreThreshold(faceblur_score() if min_score is None else min_score)
        _, faces = self._det.detect(work)
        out: list[FaceBox] = []
        if faces is None:
            return out
        for f in faces:
            x, y, bw, bh, score = (float(v) for v in (f[0], f[1], f[2], f[3], f[14]))
            lm: tuple[tuple[float, float], ...] | None = None
            if len(f) >= 15:
                lm = (
                    (float(f[4]) / scale, float(f[5]) / scale),
                    (float(f[6]) / scale, float(f[7]) / scale),
                    (float(f[8]) / scale, float(f[9]) / scale),
                    (float(f[10]) / scale, float(f[11]) / scale),
                    (float(f[12]) / scale, float(f[13]) / scale),
                )
            out.append(FaceBox(x / scale, y / scale, bw / scale, bh / scale, score, landmarks=lm))
        return out

    def detect_passes(self, bgr, angles=(0, 90, 180, 270), *, max_side: int | None = None) -> dict[int, list[FaceBox]]:
        """One list per requested orientation, in the coordinates of ``bgr`` (not merged).
        A face lying on its side or upside down is seen upright in one of the passes, so its landmarks
        are right there; the other passes give canonical (wrong) landmarks at a lower score."""
        cv2 = self._cv2
        h, w = bgr.shape[:2]
        codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        out: dict[int, list[FaceBox]] = {}
        for k in angles:
            if k == 0:
                faces = self._raw_detect(bgr, max_side)
                for f in faces:
                    f.angle = 0
                out[0] = faces
            else:
                # stricter in the extra passes: a real sideways face scores high once seen upright,
                # while helmets, knees and body patterns only show up at low scores
                strict = max(faceblur_score(), ROTATED_MIN_SCORE)
                out[k] = [_unrotate_face(f, k, w, h) for f in self._raw_detect(cv2.rotate(bgr, codes[k]), max_side, strict)]
        return out

    def detect(self, bgr, *, max_side: int | None = None) -> list[FaceBox]:
        """Faces in the coordinates of ``bgr`` (all orientations merged when rotation passes are on)."""
        angles = (0, 90, 180, 270) if faceblur_rotate() else (0,)
        found: list[FaceBox] = []
        for faces in self.detect_passes(bgr, angles, max_side=max_side).values():
            found.extend(faces)
        return _merge_faces(found)


def _unrotate_point(x: float, y: float, k: int, w: int, h: int) -> tuple[float, float]:
    """Map a point of the image rotated clockwise by k degrees back to the original (w x h)."""
    if k == 90:
        return y, h - x
    if k == 180:
        return w - x, h - y
    if k == 270:
        return w - y, x
    return x, y


def _unrotate_face(f: FaceBox, k: int, w: int, h: int) -> FaceBox:
    corners = [_unrotate_point(px, py, k, w, h) for px, py in
               ((f.x, f.y), (f.x + f.w, f.y), (f.x + f.w, f.y + f.h), (f.x, f.y + f.h))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    lm = tuple(_unrotate_point(px, py, k, w, h) for px, py in f.landmarks) if f.landmarks else None
    return FaceBox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), f.score, landmarks=lm, angle=k)


def _iou(a: FaceBox, b: FaceBox) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def same_face(a: FaceBox, b: FaceBox) -> bool:
    """Two detections of ONE face (typically from different orientations): boxes overlap, one contains
    the other's centre, or the centres are close compared with the face size."""
    if _iou(a, b) > 0.2:
        return True
    ax, ay, bx, by = a.x + a.w / 2, a.y + a.h / 2, b.x + b.w / 2, b.y + b.h / 2
    if a.x <= bx <= a.x + a.w and a.y <= by <= a.y + a.h:
        return True
    if b.x <= ax <= b.x + b.w and b.y <= ay <= b.y + b.h:
        return True
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 < 0.5 * max(a.w, a.h, b.w, b.h)


def _merge_faces(faces: list[FaceBox]) -> list[FaceBox]:
    """Same face seen in several orientations -> keep the best-scoring one (its landmarks are the right ones)."""
    kept: list[FaceBox] = []
    for f in sorted(faces, key=lambda x: -x.score):
        if any(same_face(f, k) for k in kept):
            continue
        kept.append(f)
    return sorted(kept, key=lambda x: (x.y, x.x))


# ---------------------------------------------------------------- blur

def _clamp_rect(x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> tuple[int, int, int, int]:
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1))
    y1 = max(y0 + 1, min(height, y1))
    return x0, y0, x1, y1


def _square_at(cx: float, cy: float, half: float, width: int, height: int) -> tuple[int, int, int, int]:
    return _clamp_rect(int(cx - half), int(cy - half), int(cx + half), int(cy + half), width, height)


def expand_box(b: FaceBox, margin: float, width: int, height: int) -> tuple[int, int, int, int]:
    """Grow the detector box and clip to the image. ``margin`` is fraction of face w/h per side."""
    mx, my = b.w * margin, b.h * margin
    top = my * (1.0 + margin * 0.35)
    x0 = max(0, int(b.x - mx))
    y0 = max(0, int(b.y - top))
    x1 = min(width, int(b.x + b.w + mx))
    y1 = min(height, int(b.y + b.h + my))
    return _clamp_rect(x0, y0, x1, y1, width, height)


def _eye_boxes_from_bbox(b: FaceBox, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Fallback when landmarks missing (tests): upper face, left/right halves."""
    x0, y0, x1, y1 = expand_box(b, 0.08, width, height)
    mid = (x0 + x1) // 2
    ey1 = y0 + int((y1 - y0) * 0.45)
    return [
        _clamp_rect(x0, y0, mid, ey1, width, height),
        _clamp_rect(mid, y0, x1, ey1, width, height),
    ]


Shape = tuple  # (x0, y0, x1, y1) or (x0, y0, x1, y1, polygon)


COLLAPSED_EYES = 0.28   # eye distance below this share of the box width = landmarks are not trustworthy
# eye axis u and "down the face" n in image coordinates for a face found in the image rotated by k degrees
_AXES = {0: (1.0, 0.0, 0.0, 1.0), 90: (0.0, -1.0, 1.0, 0.0), 180: (-1.0, 0.0, 0.0, -1.0), 270: (0.0, 1.0, -1.0, 0.0)}


class _Frame:
    """Head coordinate frame from the five YuNet landmarks (or estimated from the box)."""

    def __init__(self, b: FaceBox):
        if b.landmarks:
            self.re, self.le, self.nose, self.rcm, self.lcm = b.landmarks
        else:  # estimate: eyes at 38 %, nose at 58 %, mouth corners at 78 % of the box
            self.re = (b.x + b.w * 0.30, b.y + b.h * 0.38)
            self.le = (b.x + b.w * 0.70, b.y + b.h * 0.38)
            self.nose = (b.x + b.w * 0.50, b.y + b.h * 0.58)
            self.rcm = (b.x + b.w * 0.36, b.y + b.h * 0.78)
            self.lcm = (b.x + b.w * 0.64, b.y + b.h * 0.78)
        dx, dy = self.le[0] - self.re[0], self.le[1] - self.re[1]
        raw_d = max(8.0, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / raw_d, dy / raw_d
        # frontal eye distance is ~0.47 of the box width; profile or bowed heads squeeze it, which would
        # shrink every part to a sliver. Never let the scale fall below 0.38 x box width.
        self.d = max(raw_d, 0.38 * b.w)
        self.eye_mid = ((self.re[0] + self.le[0]) / 2.0, (self.re[1] + self.le[1]) / 2.0)
        self.mouth_mid = ((self.rcm[0] + self.lcm[0]) / 2.0, (self.rcm[1] + self.lcm[1]) / 2.0)
        # "down the face" = from the eyes towards the mouth, whatever the head roll (lying, upside down)
        nx, ny = -uy, ux
        if (self.mouth_mid[0] - self.eye_mid[0]) * nx + (self.mouth_mid[1] - self.eye_mid[1]) * ny < 0:
            ux, uy, nx, ny = -ux, -uy, -nx, -ny
        self.collapsed = bool(b.landmarks) and raw_d < COLLAPSED_EYES * b.w
        if self.collapsed:
            ux, uy, nx, ny = _AXES.get(b.angle, _AXES[0])
        self.u = (ux, uy)
        self.n = (nx, ny)
        mdx, mdy = self.lcm[0] - self.rcm[0], self.lcm[1] - self.rcm[1]
        self.mouth_w = max(self.d * 0.4, (mdx * mdx + mdy * mdy) ** 0.5)
        self.box = b

    def at(self, c: tuple[float, float], along: float = 0.0, down: float = 0.0) -> tuple[float, float]:
        """Point c moved ``along`` the eye axis and ``down`` the face normal (pixels)."""
        return (c[0] + self.u[0] * along + self.n[0] * down, c[1] + self.u[1] * along + self.n[1] * down)


def _rot_rect(fr: _Frame, c: tuple[float, float], half_len: float, up: float, down: float, width: int, height: int) -> Shape:
    """Rectangle centred on c, half_len along the eye axis, ``up``/``down`` along the normal."""
    pts = [fr.at(c, -half_len, -up), fr.at(c, half_len, -up), fr.at(c, half_len, down), fr.at(c, -half_len, down)]
    poly = [(int(round(x)), int(round(y))) for x, y in pts]
    x0, y0, x1, y1 = _clamp_rect(
        min(p[0] for p in poly), min(p[1] for p in poly),
        max(p[0] for p in poly) + 1, max(p[1] for p in poly) + 1, width, height,
    )
    return x0, y0, x1, y1, poly


def eye_bar(b: FaceBox, margin: float, width: int, height: int) -> Shape:
    """One bar from eye to eye, reaching to the ears, covering eyebrows (above) and eye bags (below)."""
    m = max(0.0, float(margin))
    fr = _Frame(b)
    if fr.collapsed and faceblur_unsure() == "face":
        return expand_box(b, m, width, height)
    half_len = max(fr.d * 1.1, b.w * 0.5) * faceblur_bar_side() * (1.0 + m)
    return _rot_rect(fr, fr.eye_mid, half_len, fr.d * (faceblur_bar_up() + m), fr.d * (faceblur_bar_down() + m), width, height)


def _part_shapes(b: FaceBox, part: str, margin: float, width: int, height: int) -> list[Shape]:
    m = max(0.0, float(margin))
    if part == "face":
        return [expand_box(b, margin, width, height)]
    if part == "tight":
        tight_m = min(margin, 0.22) if margin > 0 else 0.12
        return [expand_box(b, tight_m, width, height)]
    if part == "eyebar":
        return [eye_bar(b, margin, width, height)]
    fr = _Frame(b)
    d = fr.d
    sc = faceblur_scale(part)
    wm, hm = faceblur_part_wh(part)  # optional extra width / height factor per part
    pad = d * m * 0.5  # margin = extra room on every side, in half eye distances
    if part == "eyes":  # two separate squares (needs real landmarks)
        if not b.landmarks:
            return _eye_boxes_from_bbox(b, width, height)  # type: ignore[return-value]
        half = d * (0.10 + m * 0.40)
        return [_square_at(fr.le[0], fr.le[1], half, width, height), _square_at(fr.re[0], fr.re[1], half, width, height)]
    if part == "nose":
        return [_rot_rect(fr, fr.nose, d * 0.34 * sc * wm + pad, d * 0.50 * sc * hm + pad, d * 0.22 * sc * hm + pad, width, height)]
    if part == "mouth":
        return [_rot_rect(fr, fr.mouth_mid, (fr.mouth_w / 2 + d * 0.20) * sc * wm + pad, d * 0.22 * sc * hm + pad, d * 0.30 * sc * hm + pad, width, height)]
    if part == "ears":
        out: list[Shape] = []
        reach = max(b.w * 0.5, d * 0.95)
        for sign in (-1.0, 1.0):
            c = fr.at(fr.eye_mid, sign * reach, d * 0.50)
            out.append(_rot_rect(fr, c, d * 0.22 * sc * wm + pad, d * 0.45 * sc * hm + pad, d * 0.55 * sc * hm + pad, width, height))
        return out
    if part == "chin":
        length = max(d * 0.45, b.h * 0.20) * sc * hm
        c = fr.at(fr.mouth_mid, 0.0, d * 0.32 + length / 2)
        return [_rot_rect(fr, c, (fr.mouth_w / 2 + d * 0.25) * sc * wm + pad, length / 2 + pad, length / 2 + pad, width, height)]
    if part == "forehead":
        length = max(d * 0.35, b.h * 0.25) * sc * hm
        c = fr.at(fr.eye_mid, 0.0, -(d * faceblur_bar_up() + length / 2))
        return [_rot_rect(fr, c, d * 0.9 * sc * wm + pad, length / 2 + pad, length / 2 + pad, width, height)]
    return [expand_box(b, margin, width, height)]


def _shape_poly(item: Shape) -> list[tuple[int, int]]:
    if len(item) > 4 and not hasattr(item[4], "shape"):
        return list(item[4])
    x0, y0, x1, y1 = item[:4]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def fill_shapes(b: FaceBox, shapes: list[Shape], mode: str, width: int, height: int) -> list[Shape]:
    """Join separate parts into one shape. ``hull`` = convex hull of everything,
    ``bridge`` = close the gaps between neighbouring parts along the face's vertical axis
    (e.g. eye bar -> nose -> mouth), the cheeks beside narrower parts stay visible."""
    cv2, np = _require_cv2()
    if mode == "none" or len(shapes) < 2:
        return shapes
    polys = [_shape_poly(s) for s in shapes]
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    x0, y0 = max(0, min(xs)), max(0, min(ys))
    x1, y1 = min(width, max(xs) + 1), min(height, max(ys) + 1)
    if x1 <= x0 or y1 <= y0:
        return shapes
    mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
    for poly in polys:
        cv2.fillConvexPoly(mask, np.array([(px - x0, py - y0) for px, py in poly], dtype=np.int32), 255)
    if mode == "hull":
        pts = cv2.findNonZero(mask)
        hull = cv2.convexHull(pts)
        mask = np.zeros_like(mask)
        cv2.fillConvexPoly(mask, hull, 255)
    else:  # bridge: vertical closing in the head's own (tilted) frame
        fr = _Frame(b)
        h, w = mask.shape
        pad = int(np.hypot(h, w)) + 4
        canvas = cv2.copyMakeBorder(mask, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        ch, cw = canvas.shape
        angle = float(np.degrees(np.arctan2(fr.u[1], fr.u[0])))  # rotate so the eye axis is horizontal
        rot = cv2.getRotationMatrix2D((cw / 2, ch / 2), angle, 1.0)
        level = cv2.warpAffine(canvas, rot, (cw, ch), flags=cv2.INTER_NEAREST)
        k = max(3, int(round(faceblur_fill_gap() * fr.d)))
        level = cv2.morphologyEx(level, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
        sliver = max(3, int(round(fr.d * 0.25)))  # hairline gaps between side-by-side parts
        level = cv2.morphologyEx(level, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (sliver, 1)))
        back = cv2.warpAffine(level, rot, (cw, ch), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
        mask = np.where(back[pad:pad + h, pad:pad + w] > 0, 255, 0).astype(np.uint8)
        mask = np.maximum(mask, np.where(canvas[pad:pad + h, pad:pad + w] > 0, 255, 0).astype(np.uint8))
    return [(x0, y0, x1, y1, mask)]


def boxes_for_face(
    b: FaceBox,
    region: str | None,
    margin: float,
    width: int,
    height: int,
) -> list[Shape]:
    """Map PHOTOTAG_FACEBLUR_REGION (one or several parts) to blur shapes (rectangles / rotated bars)."""
    from phototag.config import parse_region_names

    parts = parse_region_names(region)[0] if region else faceblur_regions()
    shapes: list[Shape] = []
    for part in parts or ["face"]:
        shapes.extend(_part_shapes(b, part, margin, width, height))
    if not set(parts) & BOX_PARTS:  # box regions need no joining
        shapes = fill_shapes(b, shapes, faceblur_fill(), width, height)
    return shapes


def apply_blur(
    bgr,
    boxes: list[tuple[int, int, int, int]],
    mode: str | None = None,
    *,
    strength: float | None = None,
) -> None:
    """In-place. ``blur`` = Gaussian (strength↑ = stärker), ``pixel`` = mosaic (strength↑ = gröber)."""
    cv2, _ = _require_cv2()
    mode = mode or faceblur_mode()
    s = faceblur_strength() if strength is None else strength
    s = max(0.2, min(4.0, float(s)))
    for item in boxes:
        x0, y0, x1, y1 = item[:4]
        poly = item[4] if len(item) > 4 else None
        roi = bgr[y0:y1, x0:x1]
        rh, rw = roi.shape[:2]
        if rh < 2 or rw < 2:
            continue
        if mode == "pixel":
            if max(rw, rh) > 1.8 * min(rw, rh):
                # elongated (eye bar): cell size from the SHORT side, else the eyes stay readable
                short_cells = max(2, int(round(3.0 / s)))
                cell = max(1.0, min(rw, rh) / short_cells)
                cx_cells, cy_cells = max(1, int(round(rw / cell))), max(1, int(round(rh / cell)))
            else:
                cx_cells = cy_cells = max(3, int(round(14.0 / s)))
            small = cv2.resize(roi, (max(1, min(cx_cells, rw)), max(1, min(cy_cells, rh))), interpolation=cv2.INTER_AREA)
            result = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
        else:
            div = max(2, int(round(6.0 / s)))
            small = cv2.resize(roi, (max(2, rw // div), max(2, rh // div)), interpolation=cv2.INTER_AREA)
            sigma = max(1.0, max(small.shape[:2]) / 4.0 * s)
            small = cv2.GaussianBlur(small, (0, 0), sigma)
            result = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_CUBIC)
        if poly is None:
            bgr[y0:y1, x0:x1] = result
        else:  # rotated bar or filled shape: replace only the pixels inside it
            _, np = _require_cv2()
            if hasattr(poly, "shape"):
                mask = poly[: rh, : rw]
            else:
                mask = np.zeros((rh, rw), dtype=np.uint8)
                cv2.fillConvexPoly(mask, np.array([(px - x0, py - y0) for px, py in poly], dtype=np.int32), 255)
            region = bgr[y0:y1, x0:x1]
            region[mask > 0] = result[mask > 0]


def blur_frame(
    bgr,
    faces: list[FaceBox],
    *,
    margin: float | None = None,
    mode: str | None = None,
    region: str | None = None,
    strength: float | None = None,
) -> int:
    h, w = bgr.shape[:2]
    m = faceblur_margin() if margin is None else margin
    reg = region or faceblur_region()
    strg = faceblur_strength() if strength is None else strength
    all_boxes: list[tuple[int, int, int, int]] = []
    for fb in faces:
        all_boxes.extend(boxes_for_face(fb, reg, m, w, h))
    if not all_boxes:
        return 0
    apply_blur(bgr, all_boxes, mode, strength=strg)
    return len(all_boxes)


# ---------------------------------------------------------------- images

def blur_area_share(faces: list[FaceBox], width: int, height: int, *, margin: float | None = None, region: str | None = None) -> float:
    """Share of the image covered by blur boxes (overlaps counted once via a mask)."""
    _, np = _require_cv2()
    m = faceblur_margin() if margin is None else margin
    cv2, _ = _require_cv2()
    mask = np.zeros((height, width), dtype=np.uint8)
    for f in faces:
        for item in boxes_for_face(f, region or faceblur_region(), m, width, height):
            x0, y0, x1, y1 = item[:4]
            if len(item) > 4 and hasattr(item[4], "shape"):
                sub_mask = item[4][: y1 - y0, : x1 - x0] > 0
                mask[y0:y0 + sub_mask.shape[0], x0:x0 + sub_mask.shape[1]][sub_mask] = 1
            elif len(item) > 4:
                cv2.fillConvexPoly(mask, np.array(item[4], dtype=np.int32), 1)
            else:
                mask[y0:y1, x0:x1] = 1
    return float(mask.mean())


def quality_notes(area_share: float, leaks: int | None, region: str | None = None) -> list[str]:
    """Human-readable hints for the review step."""
    reg = region or faceblur_region()
    notes: list[str] = []
    box_only = set(parse_region_names(reg)[0] or ["face"]) <= BOX_PARTS
    if box_only and area_share > 0.25:
        notes.append(
            f"WARNUNG: Blur deckt {area_share:.0%} des Bildes ab (grosses Gesicht, Nahaufnahme?) — "
            "PHOTOTAG_FACEBLUR_REGION/MARGIN prüfen"
        )
    elif area_share:
        notes.append(f"Blur-Fläche {area_share:.1%}")
    if not box_only and "face" not in parse_region_names(reg)[0]:
        notes.append(f"Region {reg}: übrige Gesichtsteile bleiben sichtbar")
    if leaks:
        notes.append(f"WARNUNG: in der Kopie noch {leaks} Gesicht(er) erkennbar — prüfen")
    return notes


def count_leaks(detector: "FaceDetector", bgr_frames: list[Any]) -> int:
    """Faces still detectable after blurring (only meaningful for region face/tight)."""
    if not set(faceblur_regions()) <= BOX_PARTS:
        return 0
    return sum(len(detector.detect(f, max_side=1280)) for f in bgr_frames)


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
    share = blur_area_share(faces, w, h)
    leaks = count_leaks(det, [np.ascontiguousarray(np.asarray(Image.open(dst).convert("RGB"))[:, :, ::-1])])
    notes = [faceblur_settings_summary(), f"blur_boxes={n}", *quality_notes(share, leaks)]
    return BlurResult(faces=len(faces), width=w, height=h, kind="image", notes=notes)


def blur_image_to_jpeg(
    src: Path,
    *,
    detector: FaceDetector | None = None,
    allow_empty: bool = False,
    max_side: int = 1600,
    quality: int = 88,
) -> tuple[bytes, BlurResult]:
    """Blur in memory — for UI preview (no mount write)."""
    import io

    cv2, np = _require_cv2()
    from PIL import Image, ImageOps

    det = detector or FaceDetector()
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        rgb = np.asarray(im.convert("RGB"))
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    faces = det.detect(bgr)
    if not faces and not allow_empty:
        raise NoFacesFound("Keine Gesichter erkannt")
    blur_frame(bgr, faces)
    out = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
    h, w = bgr.shape[:2]
    long_side = max(w, h)
    if max_side > 0 and long_side > max_side:
        scale = max_side / long_side
        out = out.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        w, h = out.size
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=quality, subsampling=0)
    share = blur_area_share(faces, w, h)
    notes = [faceblur_settings_summary(), f"blur_boxes={len(faces)}", *quality_notes(share, 0)]
    return buf.getvalue(), BlurResult(faces=len(faces), width=w, height=h, kind="image", notes=notes)


def blur_preview_jpeg(src: Path, **kw: Any) -> tuple[bytes, BlurResult]:
    ext = src.suffix.lower()
    if ext in IMAGE_EXTS:
        return blur_image_to_jpeg(src, **kw)
    if ext in VIDEO_EXTS:
        import tempfile

        w, h, fps, duration = probe_display(src)
        seek = min(max(0.5, duration * 0.15), max(0.0, duration - 0.1)) if duration > 0 else 0.5
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-ss", f"{seek:.3f}", "-i", str(src),
                    "-frames:v", "1", "-q:v", "2", "-y", str(frame_path),
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not frame_path.is_file():
                raise FaceBlurError("Vorschau-Frame konnte nicht gelesen werden")
            return blur_image_to_jpeg(frame_path, **kw)
        finally:
            frame_path.unlink(missing_ok=True)
    raise FaceBlurError(f"Vorschau nicht unterstützt: {src.suffix or '(keine Endung)'}")


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
         "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,duration:stream_tags=rotate:stream_side_data=rotation:format=duration",
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
    # the VIDEO stream's length: phones often record audio a second longer than the picture
    duration = float(s.get("duration") or 0) or float((data.get("format") or {}).get("duration") or 0)
    if w <= 0 or h <= 0:
        raise FaceBlurError("Videogrösse unbekannt")
    return w, h, min(max(fps, 1.0), 120.0), duration


def blur_video_file(
    src: Path,
    dst: Path,
    *,
    detector: FaceDetector | None = None,
    progress: ProgressFn | None = None,
    allow_empty: bool = False,
) -> BlurResult:
    """Video: analysis on a small copy, tracking over time, one render pass (see faceblur_video)."""
    from phototag.faceblur_video import blur_video

    return blur_video(src, dst, detector=detector, progress=progress, allow_empty=allow_empty)


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


def stage_local(src: Path) -> tuple[Path, bool]:
    """Copy a (video) file from the slow mount to local disk ONCE. Returns (path, staged).
    ffprobe, the analysis pass and the render pass would otherwise each read it over WebDAV again.
    Falls back to the original when the disk is too small."""
    import shutil
    import uuid

    from phototag.config import telegram_temp_dir

    d = telegram_temp_dir()
    d.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    if shutil.disk_usage(d).free < size * 3 + 200 * 1024 * 1024:
        return src, False
    dst = d / f"phototag-fb-in-{uuid.uuid4().hex}{src.suffix.lower()}"
    try:
        shutil.copyfile(src, dst)
    except OSError:
        dst.unlink(missing_ok=True)
        return src, False
    return dst, True


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
    "blur_preview_jpeg",
    "blur_video_file",
    "blurred_filename",
    "boxes_for_face",
    "expand_box",
    "is_blurred_name",
    "temp_output_path",
    "unique_destination",
]
