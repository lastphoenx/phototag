"""Human review of a video blur: keep the analysis, show the uncertain stretches, apply corrections, re-render.

Why: no detector is complete (hands that look like faces, faces at the frame edge, heads bowed down). The blur job
therefore keeps what it found; the admin shows the timeline (sure / unsure / dropped candidate / nothing found),
the person corrects whole stretches (``none`` = no face here, ``face`` = blur the whole face, ``rect`` = a drawn
box that may move from a start to an end box) and the video is rendered again from the ORIGINAL (no generation loss).
"""
from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phototag.config import faceblur_margin, faceblur_video_stride
from phototag.faceblur import FaceBlurError, FaceBox, _shape_poly, boxes_for_face
from phototag.faceblur_video import (
    Analysis,
    Track,
    is_sure,
    lerp_face,
    output_geometry,
    scale_face,
    tracks_to_frames,
)

SCHEMA_VERSION = 1
MODES = ("none", "face", "rect")
NEAR_FRAMES = 90          # a "face" correction may use a detection this far (3 s at 30 fps) from the frame

DDL = """
CREATE TABLE IF NOT EXISTS blur_analysis(
  image_id INTEGER PRIMARY KEY,         -- the blur copy
  data BLOB NOT NULL,                   -- zlib(JSON) of the analysis
  options TEXT,                         -- UI options the job ran with (JSON) or NULL = server settings
  created_at REAL NOT NULL,
  rendered_edit_id INTEGER NOT NULL DEFAULT 0   -- highest edit id that is part of the current file
);
CREATE TABLE IF NOT EXISTS blur_edits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  image_id INTEGER NOT NULL,
  frame_from INTEGER NOT NULL,
  frame_to INTEGER NOT NULL,
  mode TEXT NOT NULL,                   -- none | face | rect
  rect_start TEXT,                      -- JSON [x0,y0,x1,y1] as 0..1 shares of the frame
  rect_end TEXT,
  replace_auto INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS blur_edits_image ON blur_edits(image_id);
"""


def ensure_tables(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    con.commit()


# ---------------------------------------------------------------- analysis <-> JSON

def _face_to_list(frame: int, f: FaceBox) -> list[Any]:
    lm = [[round(x, 1), round(y, 1)] for x, y in f.landmarks] if f.landmarks else None
    return [frame, round(f.x, 1), round(f.y, 1), round(f.w, 1), round(f.h, 1), round(f.score, 3), f.angle, lm]


def _face_from_list(row: list[Any]) -> tuple[int, FaceBox]:
    lm = tuple((float(p[0]), float(p[1])) for p in row[7]) if row[7] else None
    return int(row[0]), FaceBox(row[1], row[2], row[3], row[4], row[5], landmarks=lm, angle=int(row[6]))


def _track_to_dict(t: Track, why: str | None = None) -> dict[str, Any]:
    d = {
        "id": t.id, "angle": t.angle, "confirmed": t.confirmed, "checked": t.checked,
        "corroborated": t.corroborated, "items": [_face_to_list(i, f) for i, f in t.items],
    }
    if why is not None:
        d["why"] = why
    return d


def _track_from_dict(d: dict[str, Any]) -> Track:
    t = Track(int(d["id"]), int(d["angle"]), [_face_from_list(r) for r in d["items"]])
    t.confirmed, t.checked, t.corroborated = int(d.get("confirmed", 0)), bool(d.get("checked")), bool(d.get("corroborated"))
    return t


def analysis_to_dict(an: Analysis) -> dict[str, Any]:
    return {
        "v": SCHEMA_VERSION, "width": an.width, "height": an.height, "fps": an.fps, "duration": an.duration,
        "total_frames": an.total_frames, "analysis_size": list(an.analysis_size), "raw": an.raw_detections,
        "samples": an.samples, "switches": an.angle_switches, "cuts": list(an.cuts),
        "tracks": [_track_to_dict(t) for t in an.tracks],
        "dropped": [_track_to_dict(t, why) for t, why in an.dropped],
    }


def analysis_from_dict(d: dict[str, Any]) -> Analysis:
    if d.get("v") != SCHEMA_VERSION:
        raise ValueError("Analyse-Format unbekannt — Blur-Kopie neu erzeugen")
    return Analysis(
        int(d["width"]), int(d["height"]), float(d["fps"]), float(d["duration"]), int(d["total_frames"]),
        (int(d["analysis_size"][0]), int(d["analysis_size"][1])),
        [_track_from_dict(t) for t in d["tracks"]],
        [(_track_from_dict(t), str(t.get("why", ""))) for t in d["dropped"]],
        int(d["raw"]), int(d["samples"]), int(d["switches"]), [int(c) for c in d["cuts"]],
    )


def save_analysis(con: sqlite3.Connection, image_id: int, an: Analysis, options: dict[str, Any] | None) -> None:
    ensure_tables(con)
    blob = zlib.compress(json.dumps(analysis_to_dict(an), separators=(",", ":")).encode("utf-8"), 6)
    con.execute("DELETE FROM blur_edits WHERE image_id=?", (image_id,))     # a fresh analysis starts without old fixes
    con.execute(
        "INSERT OR REPLACE INTO blur_analysis(image_id,data,options,created_at,rendered_edit_id) VALUES(?,?,?,?,0)",
        (image_id, blob, json.dumps(options, ensure_ascii=False) if options else None, time.time()),
    )
    con.commit()


def load_analysis(con: sqlite3.Connection, image_id: int) -> tuple[Analysis, dict[str, Any] | None, int]:
    """(analysis, options, rendered_edit_id). LookupError when there is none (older copy or an image)."""
    ensure_tables(con)
    row = con.execute("SELECT * FROM blur_analysis WHERE image_id=?", (image_id,)).fetchone()
    if not row:
        raise LookupError("Für diese Kopie liegt keine Analyse vor (älter als die Prüf-Funktion) — Blur neu erzeugen.")
    an = analysis_from_dict(json.loads(zlib.decompress(row["data"]).decode("utf-8")))
    return an, (json.loads(row["options"]) if row["options"] else None), int(row["rendered_edit_id"])


def delete_review_data(con: sqlite3.Connection, image_id: int) -> None:
    ensure_tables(con)
    con.execute("DELETE FROM blur_analysis WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM blur_edits WHERE image_id=?", (image_id,))
    con.commit()


# ---------------------------------------------------------------- edits

@dataclass(frozen=True)
class Edit:
    id: int
    frame_from: int
    frame_to: int
    mode: str
    rect_start: tuple[float, float, float, float] | None = None
    rect_end: tuple[float, float, float, float] | None = None
    replace: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "frame_from": self.frame_from, "frame_to": self.frame_to, "mode": self.mode,
            "rect_start": list(self.rect_start) if self.rect_start else None,
            "rect_end": list(self.rect_end) if self.rect_end else None, "replace": self.replace,
        }


def _rect(raw: Any, name: str) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: erwartet [x0, y0, x1, y1] als Anteile 0..1") from None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:
        raise ValueError(f"{name}: Rechteck zu klein")
    return x0, y0, x1, y1


def list_edits(con: sqlite3.Connection, image_id: int) -> list[Edit]:
    ensure_tables(con)
    out = []
    for r in con.execute("SELECT * FROM blur_edits WHERE image_id=? ORDER BY id", (image_id,)):
        out.append(Edit(
            r["id"], r["frame_from"], r["frame_to"], r["mode"],
            tuple(json.loads(r["rect_start"])) if r["rect_start"] else None,
            tuple(json.loads(r["rect_end"])) if r["rect_end"] else None,
            bool(r["replace_auto"]),
        ))
    return out


def add_edit(
    con: sqlite3.Connection, image_id: int, an: Analysis, *, frame_from: Any, frame_to: Any, mode: str,
    rect_start: Any = None, rect_end: Any = None, replace: bool = True,
) -> Edit:
    ensure_tables(con)
    if mode not in MODES:
        raise ValueError(f"Modus '{mode}' unbekannt (erlaubt: {', '.join(MODES)})")
    try:
        a, b = int(frame_from), int(frame_to)
    except (TypeError, ValueError):
        raise ValueError("frame_from/frame_to müssen Zahlen sein") from None
    a, b = max(0, min(a, b)), min(an.total_frames - 1, max(a, b))
    if a > b:
        raise ValueError("Bereich liegt ausserhalb des Videos")
    rs = re = None
    if mode == "rect":
        if rect_start is None:
            raise ValueError("Für ein Rechteck fehlt rect_start")
        rs = _rect(rect_start, "rect_start")
        re = _rect(rect_end, "rect_end") if rect_end is not None else None
    elif mode == "face":
        if not any(nearest_face(an, i) for i in (a, (a + b) // 2, b)):
            raise ValueError("Kein erkanntes Gesicht in der Nähe — bitte ein Rechteck ziehen")
    cur = con.execute(
        "INSERT INTO blur_edits(image_id,frame_from,frame_to,mode,rect_start,rect_end,replace_auto,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (image_id, a, b, mode, json.dumps(rs) if rs else None, json.dumps(re) if re else None, 1 if replace else 0, time.time()),
    )
    con.commit()
    return Edit(int(cur.lastrowid), a, b, mode, rs, re, bool(replace))


def delete_edit(con: sqlite3.Connection, image_id: int, edit_id: int) -> bool:
    ensure_tables(con)
    cur = con.execute("DELETE FROM blur_edits WHERE image_id=? AND id=?", (image_id, edit_id))
    con.commit()
    return cur.rowcount > 0


def pending_edits(con: sqlite3.Connection, image_id: int, rendered_edit_id: int) -> bool:
    """True when the file on disk does not yet contain every correction (added or removed since the last render)."""
    ids = [e.id for e in list_edits(con, image_id)]
    return (max(ids) if ids else 0) != rendered_edit_id


def mark_rendered(con: sqlite3.Connection, image_id: int) -> None:
    ids = [e.id for e in list_edits(con, image_id)]
    con.execute("UPDATE blur_analysis SET rendered_edit_id=? WHERE image_id=?", (max(ids) if ids else 0, image_id))
    con.commit()


# ---------------------------------------------------------------- what is blurred where

def nearest_face(an: Analysis, frame: int, scale: float = 1.0) -> FaceBox | None:
    """The best known face geometry for ``frame`` from ANY track (kept or dropped): between two detections of one
    track it is interpolated, otherwise the closest detection within NEAR_FRAMES counts."""
    best: tuple[int, FaceBox] | None = None
    for tr in [*an.tracks, *(t for t, _ in an.dropped)]:
        items = sorted(tr.items, key=lambda it: it[0])
        before = [it for it in items if it[0] <= frame]
        after = [it for it in items if it[0] >= frame]
        a = before[-1] if before else None
        b = after[0] if after else None
        cand: tuple[int, FaceBox] | None = None
        if a and b and b[0] - a[0] <= NEAR_FRAMES * 2 and b[0] != a[0]:
            cand = (min(frame - a[0], b[0] - frame), lerp_face(a[1], b[1], (frame - a[0]) / (b[0] - a[0])))
        else:
            pick = min((x for x in (a, b) if x), key=lambda it: abs(it[0] - frame), default=None)
            if pick and abs(pick[0] - frame) <= NEAR_FRAMES:
                cand = (abs(pick[0] - frame), pick[1])
        if cand and (best is None or cand[0] < best[0]):
            best = cand
    if best is None:
        return None
    return scale_face(best[1], scale) if scale != 1.0 else best[1]


def _rect_at(e: Edit, frame: int) -> tuple[float, float, float, float]:
    a = e.rect_start
    assert a is not None
    if e.rect_end is None or e.frame_to == e.frame_from:
        return a
    t = (frame - e.frame_from) / float(e.frame_to - e.frame_from)
    return tuple(a[k] + (e.rect_end[k] - a[k]) * t for k in range(4))  # type: ignore[return-value]


@dataclass
class Plan:
    faces: dict[int, list[FaceBox]]
    full: dict[int, list[FaceBox]]
    rects: dict[int, list[tuple[float, float, float, float]]]


def plan_frames(an: Analysis, edits: list[Edit], scale: float = 1.0) -> Plan:
    """Automatic blur plus the corrections (later corrections win)."""
    faces = tracks_to_frames(an.tracks, an.total_frames, an.fps, scale=scale, cuts=an.cuts)
    full: dict[int, list[FaceBox]] = {}
    rects: dict[int, list[tuple[float, float, float, float]]] = {}
    for e in sorted(edits, key=lambda x: x.id):
        for i in range(e.frame_from, min(e.frame_to, an.total_frames - 1) + 1):
            if e.mode == "none":
                faces.pop(i, None), full.pop(i, None), rects.pop(i, None)
            elif e.mode == "face":
                f = nearest_face(an, i, scale)
                if f is None:
                    continue
                faces.pop(i, None), rects.pop(i, None)
                full[i] = [f]
            elif e.mode == "rect":
                if e.replace:
                    faces.pop(i, None), full.pop(i, None), rects.pop(i, None)
                rects.setdefault(i, []).append(_rect_at(e, i))
    return Plan(faces, full, rects)


# ---------------------------------------------------------------- timeline

KINDS = ("sure", "unsure", "dropped", "none")


def classify_frames(an: Analysis) -> list[str]:
    """Per frame: sure (clear detection nearby) / unsure (blurred, but only interpolated or weak) /
    dropped (not blurred, but the tracker threw away a candidate nearby) / none (nothing found)."""
    stride = max(1, faceblur_video_stride())
    n = an.total_frames
    covered = set(tracks_to_frames(an.tracks, n, an.fps, cuts=an.cuts))
    sure, cand = bytearray(n), bytearray(n)
    for tr in an.tracks:
        for i, f in tr.items:
            if is_sure(f):
                for k in range(max(0, i - stride), min(n, i + stride + 1)):
                    sure[k] = 1
    for tr, _why in an.dropped:
        for i, _f in tr.items:
            for k in range(max(0, i - 2 * stride), min(n, i + 2 * stride + 1)):
                cand[k] = 1
    out = []
    for i in range(n):
        if i in covered:
            out.append("sure" if sure[i] else "unsure")
        else:
            out.append("dropped" if cand[i] else "none")
    return out


def build_segments(an: Analysis, edits: list[Edit]) -> list[dict[str, Any]]:
    kinds = classify_frames(an)
    n = an.total_frames
    cuts = set(an.cuts)
    runs: list[list[Any]] = []        # [start, end, kind]
    for i, k in enumerate(kinds):
        if runs and runs[-1][2] == k and i not in cuts:
            runs[-1][1] = i
        else:
            runs.append([i, i, k])
    min_len = max(2, int(round(0.5 * an.fps)))
    for idx, r in enumerate(runs):               # a short "sure" blip between uncertain runs is not worth its own segment
        if r[2] == "sure" and r[1] - r[0] + 1 < min_len and len(runs) > 1:
            nb = runs[idx - 1] if idx > 0 else runs[idx + 1]
            r[2] = nb[2]
    merged: list[list[Any]] = []
    for r in runs:
        if merged and merged[-1][2] == r[2] and r[0] not in cuts:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    segs = []
    for sid, (a, b, kind) in enumerate(merged):
        length = b - a + 1
        count = max(1, min(length, max(3, min(8, math.ceil(length / (an.fps * 1.5))))))
        thumbs = sorted({a + round(j * (length - 1) / max(1, count - 1)) for j in range(count)})
        segs.append({
            "id": sid, "start": a, "end": b, "kind": kind, "frames": length,
            "t0": round(a / an.fps, 2), "t1": round((b + 1) / an.fps, 2), "thumbs": thumbs,
            "edited": any(e.frame_from <= b and e.frame_to >= a for e in edits),
        })
    return segs


def _poly_norm(poly: list[tuple[int, int]], w: int, h: int) -> list[list[float]]:
    return [[round(x / w, 4), round(y / h, 4)] for x, y in poly]


def shapes_for_frame(an: Analysis, plan: Plan, frame: int) -> list[list[list[float]]]:
    """Polygons (0..1 shares of the frame) of what is blurred at ``frame`` — for the overlay in the review."""
    w, h = an.analysis_size
    margin = faceblur_margin()
    polys: list[list[list[float]]] = []
    for f in plan.faces.get(frame, []):
        polys.extend(_poly_norm(_shape_poly(s), w, h) for s in boxes_for_face(f, None, margin, w, h))
    for f in plan.full.get(frame, []):
        polys.extend(_poly_norm(_shape_poly(s), w, h) for s in boxes_for_face(f, "face", margin, w, h))
    for x0, y0, x1, y1 in plan.rects.get(frame, []):
        polys.append([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return polys


def review_payload(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    from phototag.blur_options import temporary_blur_overlay
    from phototag.blur_photo import has_photo, photo_payload

    if has_photo(con, image_id):
        return photo_payload(con, image_id)           # a photo: brush instead of timeline
    an, options, rendered = load_analysis(con, image_id)
    edits = list_edits(con, image_id)
    segments = build_segments(an, edits)
    with temporary_blur_overlay(options):           # the shapes must come from the settings the video was made with
        plan = plan_frames(an, edits)
        for s in segments:
            s["thumbs"] = [
                {"frame": f, "t": round(f / an.fps, 2), "shapes": shapes_for_frame(an, plan, f)} for f in s["thumbs"]
            ]
    counts = {k: sum(s["frames"] for s in segments if s["kind"] == k) for k in KINDS}
    return {
        "ok": True, "kind": "video", "id": image_id, "fps": an.fps, "total_frames": an.total_frames, "duration": an.duration,
        "aspect": an.analysis_size[0] / float(an.analysis_size[1]), "cuts": an.cuts,
        "segments": segments, "edits": [e.as_dict() for e in edits], "counts": counts,
        "pending": pending_edits(con, image_id, rendered),
    }


# ---------------------------------------------------------------- one frame as JPEG (original, unblurred)

def frame_jpeg(src: Path, frame: int, fps: float, width: int = 640, timeout: int = 60) -> bytes:
    width = max(160, min(1280, int(width)))
    t = max(0.0, frame / float(fps))
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
        capture_output=True, timeout=timeout, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise FaceBlurError("Frame konnte nicht gelesen werden")
    return proc.stdout
