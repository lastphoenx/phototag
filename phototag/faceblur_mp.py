"""Optional second opinion for the video pipeline: Google MediaPipe FaceLandmarker (Apache-2.0).

YuNet alone confuses hands and thighs with faces (scores 0.5-0.65, exactly like a face with glasses) and misses
big close-up faces that the frame edge cuts off. MediaPipe makes different mistakes, so a track that both
detectors agree on is a face. Needs ``pip install mediapipe`` and the model file
``face_landmarker.task`` (PHOTOTAG_FACEBLUR_MP_MODEL); without them the pipeline works as before.
"""
from __future__ import annotations

from pathlib import Path

from phototag.config import faceblur_mp_model, faceblur_mp_score
from phototag.faceblur import FaceBox

# MediaPipe mesh indices -> the five YuNet landmarks (right eye = image left, as YuNet orders them)
_RIGHT_EYE = (33, 133)
_LEFT_EYE = (362, 263)
_NOSE = 1
_MOUTH_R, _MOUTH_L = 61, 291
MP_SCORE = 0.8     # MediaPipe gives no score: a face it reports counts as this sure


def mesh_to_face(points: list[tuple[float, float]]) -> FaceBox:
    """478 mesh points (pixels) -> FaceBox with YuNet-style 5 landmarks."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mid = lambda a, b: ((points[a][0] + points[b][0]) / 2.0, (points[a][1] + points[b][1]) / 2.0)  # noqa: E731
    lm = (mid(*_RIGHT_EYE), mid(*_LEFT_EYE), points[_NOSE], points[_MOUTH_R], points[_MOUTH_L])
    return FaceBox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), MP_SCORE, landmarks=lm, angle=0)


class MediaPipeFaces:
    def __init__(self, model: Path):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision

        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(model)),
            num_faces=2,
            min_face_detection_confidence=faceblur_mp_score(),
            min_face_presence_confidence=faceblur_mp_score(),
        )
        self._lm = vision.FaceLandmarker.create_from_options(opts)

    def detect(self, bgr) -> list[FaceBox]:
        import numpy as np

        h, w = bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(bgr[:, :, ::-1]))
        out = []
        for pts in self._lm.detect(img).face_landmarks:
            f = mesh_to_face([(p.x * w, p.y * h) for p in pts])
            if 0 <= f.x + f.w / 2 < w and 0 <= f.y + f.h / 2 < h:
                out.append(f)
        return out


def create_mediapipe() -> MediaPipeFaces | None:
    """The detector, or None when it is not configured / not installed (the pipeline then runs without it)."""
    model = faceblur_mp_model()
    if not model or not Path(model).is_file():
        return None
    try:
        return MediaPipeFaces(Path(model))
    except Exception:       # mediapipe missing or the model file is unusable: carry on with YuNet alone
        return None
